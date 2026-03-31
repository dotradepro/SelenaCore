"""
system_modules/voice_core/module.py — Voice Core SystemModule.

Single audio loop architecture:
  - One parecord process (PulseAudio) captures mic continuously
  - One Vosk KaldiRecognizer processes all audio in real-time
  - State machine: IDLE → LISTENING → PROCESSING
    IDLE:       Vosk recognizes speech, checks for activation phrase
    LISTENING:  Collects user command after activation, stops on silence
    PROCESSING: Sends to LLM, synthesizes TTS, plays response
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 4000       # 250ms
BYTES_PER_CHUNK = CHUNK_SAMPLES * 2  # 16-bit


# ── Request models ───────────────────────────────────────────────────────────

class VoiceConfigRequest(BaseModel):
    stt_model: str | None = None
    tts_voice: str | None = None
    wake_word_model: str | None = None
    privacy_mode: bool | None = None
    speaker_threshold: float | None = Field(None, ge=0.3, le=1.0)
    stt_silence_timeout: float | None = Field(None, ge=0.5, le=5.0)


class TranscribeRequest(BaseModel):
    sample_rate: int = 16000


class SynthesizeRequest(BaseModel):
    text: str
    voice: str | None = None


class TestCommandRequest(BaseModel):
    text: str
    speak: bool = True


class EnrollSpeakerRequest(BaseModel):
    user_id: str


# ── State constants ──────────────────────────────────────────────────────────

STATE_IDLE = "idle"            # waiting for wake phrase
STATE_LISTENING = "listening"  # recording user command
STATE_PROCESSING = "processing"  # LLM + TTS


class VoiceCoreModule(SystemModule):
    name = "voice-core"

    def __init__(self) -> None:
        super().__init__()
        self._stt = None
        self._tts = None
        self._speaker_id = None
        self._voice_history = None
        self._privacy_task: asyncio.Task | None = None
        self._listen_task: asyncio.Task | None = None
        self._state = STATE_IDLE
        self._privacy_mode = False
        self._system_speak_done = asyncio.Event()
        self._session: list[dict[str, str]] = []  # conversation history [{role, content}]
        self._session_ts: float = 0.0              # last interaction timestamp
        self._last_intent: str = ""                # last classified intent (for rephrase context)
        self._last_query: str = ""                 # last user query text

        # Defaults from env, overridden by core.yaml
        defaults = {
            "stt_model": os.getenv("VOSK_MODEL", "vosk-model-small-uk"),
            "tts_voice": os.getenv("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"),
            "wake_word_model": os.getenv("WAKE_WORD_MODEL", "привіт селена"),
            "privacy_mode": False,
            "speaker_threshold": float(os.getenv("SPEAKER_THRESHOLD", "0.75")),
        }
        try:
            from core.config_writer import read_config
            saved = read_config().get("voice", {})
            for k in defaults:
                if k in saved:
                    defaults[k] = type(defaults[k])(saved[k])
        except Exception:
            pass
        self._config: dict[str, Any] = defaults

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_wake_phrase(self) -> str:
        return self._config.get("wake_word_model", "").replace("_", " ").lower().strip()

    def _get_silence_timeout(self) -> float:
        try:
            from core.config_writer import get_value
            return float(get_value("voice", "stt_silence_timeout", 1.5))
        except Exception:
            return 1.5

    def _get_input_device(self) -> str | None:
        try:
            from core.config_writer import get_value
            dev = get_value("voice", "audio_force_input")
            if dev:
                return dev
        except Exception:
            pass
        try:
            from system_modules.voice_core.audio_manager import get_best_input
            best = get_best_input()
            if best:
                return best.id
        except Exception:
            pass
        return None

    def _get_output_device(self) -> str | None:
        try:
            from core.config_writer import get_value
            dev = get_value("voice", "audio_force_output")
            if dev:
                return dev
        except Exception:
            pass
        try:
            from system_modules.voice_core.audio_manager import get_best_output
            best = get_best_output()
            if best:
                return best.id
        except Exception:
            pass
        return None

    @staticmethod
    def _matches_phrase(text: str, phrase: str) -> bool:
        """Fuzzy match: first 3 chars of each wake word in some text word."""
        t = text.lower().strip()
        if phrase in t:
            return True
        text_words = t.split()
        for pw in phrase.split():
            prefix = pw[:3]
            if not any(tw.startswith(prefix) for tw in text_words):
                return False
        return True

    # ── Main audio loop ──────────────────────────────────────────────────

    async def _audio_loop(self) -> None:
        """Single continuous loop: parecord → Vosk → state machine."""
        loop = asyncio.get_running_loop()

        # Load Vosk
        self._stt._load()
        if self._stt._model is None:
            logger.error("Voice loop: Vosk model not available, exiting")
            return

        from vosk import KaldiRecognizer

        input_device = self._get_input_device()
        cmd = ["parecord", "--raw", "--format=s16le", "--rate=16000", "--channels=1"]
        if input_device:
            cmd.append("--device=" + input_device)

        logger.info("Voice loop: starting parecord (input=%s)", input_device or "default")

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.error("Voice loop: cannot start parecord: %s", e)
            return

        rec = KaldiRecognizer(self._stt._model, SAMPLE_RATE)
        rec.SetWords(False)
        wake_phrase = self._get_wake_phrase()
        self._state = STATE_IDLE

        # Listening state vars
        command_phrases: list[str] = []
        last_speech_time = 0.0

        logger.info("Voice loop: ready, wake phrase='%s'", wake_phrase)

        try:
            while True:
                # Privacy mode — pause
                if self._privacy_mode:
                    await asyncio.sleep(0.5)
                    continue

                # Read audio chunk
                data = await loop.run_in_executor(
                    None, proc.stdout.read, BYTES_PER_CHUNK
                )
                if not data or len(data) < BYTES_PER_CHUNK:
                    logger.warning("Voice loop: parecord stream ended, restarting...")
                    break

                # ── STATE: PROCESSING — skip audio while TTS is playing ──
                if self._state == STATE_PROCESSING:
                    continue

                # ── Feed Vosk ──
                is_final = rec.AcceptWaveform(data)

                if is_final:
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip()
                else:
                    partial = json.loads(rec.PartialResult())
                    text = partial.get("partial", "").strip()

                if not text:
                    # Check silence timeout in LISTENING state
                    if self._state == STATE_LISTENING and command_phrases:
                        if (time.monotonic() - last_speech_time) >= self._get_silence_timeout():
                            # Silence detected — process command
                            full_text = " ".join(command_phrases)
                            command_phrases.clear()
                            self._state = STATE_PROCESSING
                            rec = KaldiRecognizer(self._stt._model, SAMPLE_RATE)
                            rec.SetWords(False)
                            asyncio.create_task(self._process_command(full_text))
                    continue

                # ── STATE: IDLE — looking for wake phrase ──
                if self._state == STATE_IDLE:
                    wake_phrase = self._get_wake_phrase()
                    if self._matches_phrase(text, wake_phrase):
                        logger.info("Voice: wake phrase detected in '%s'", text)
                        await self.publish("voice.wake_word", {"wake_word": wake_phrase})
                        self._state = STATE_LISTENING
                        command_phrases.clear()
                        last_speech_time = time.monotonic()
                        # Reset recognizer for clean command capture
                        rec = KaldiRecognizer(self._stt._model, SAMPLE_RATE)
                        rec.SetWords(False)
                        # Play chime
                        asyncio.create_task(self._play_chime())
                    elif is_final:
                        logger.debug("Voice idle heard: '%s'", text)

                # ── STATE: LISTENING — collecting command ──
                elif self._state == STATE_LISTENING:
                    last_speech_time = time.monotonic()
                    if is_final and text:
                        command_phrases.append(text)
                        logger.info("Voice: command phrase: '%s'", text)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Voice loop error: %s", e)
        finally:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass

        # Auto-restart loop (unless module is stopping)
        if self._listen_task and not self._listen_task.cancelled():
            logger.info("Voice loop: restarting in 2s...")
            await asyncio.sleep(2)
            self._listen_task = asyncio.create_task(self._audio_loop())

    # ── Language detection ──────────────────────────────────────────────

    def _detect_lang(self) -> str:
        """Derive language code from STT model name."""
        stt = self._config.get("stt_model", "")
        code = (
            stt.lower()
            .replace("vosk-model-small-", "")
            .replace("vosk-model-big-", "")
            .replace("vosk-model-", "")
            .split("-")[0]
        )
        return code if code in ("uk", "en") else "en"

    @staticmethod
    def _is_system_module_intent(intent: str) -> bool:
        """Check if intent belongs to a registered system module."""
        try:
            from system_modules.llm_engine.intent_router import get_intent_router
            for entry in get_intent_router()._system_intents:
                if entry.intent == intent:
                    return True
        except Exception:
            pass
        return False

    # ── Command processing pipeline ──────────────────────────────────────

    async def _process_command(self, text: str) -> None:
        """IntentRouter → TTS → playback, then back to IDLE.

        Resolution order (handled by IntentRouter):
          Tier 1:   FastMatcher (keyword/regex rules) — zero latency
          Tier 1.5: System module intents (in-process) — microseconds
          Tier 2:   User module intents (HTTP) — milliseconds
          Tier 3:   LLM fallback — dynamic understanding
        """
        start_ts = time.monotonic()
        try:
            logger.info("Voice pipeline: recognized '%s'", text)
            await self.publish("voice.recognized", {"text": text})

            # Route through IntentRouter (includes LLM as Tier 3 fallback)
            lang = self._detect_lang()
            from system_modules.llm_engine.intent_router import get_intent_router
            result = await get_intent_router().route(text, user_id=None, lang=lang)

            logger.info(
                "Voice pipeline: intent='%s' source='%s' latency=%dms",
                result.intent, result.source, result.latency_ms,
            )

            # Session context for LLM rephrase
            self._last_query = text
            self._last_intent = result.intent
            # Reset session after 5 min of inactivity
            if time.monotonic() - self._session_ts > 300:
                self._session.clear()
            self._session_ts = time.monotonic()
            self._session.append({"role": "user", "content": text})

            # System modules handle their own TTS via EventBus (voice.speak).
            # For system_module intents (or cloud_llm-classified intents that map
            # to a system module), stay in PROCESSING until TTS completes
            # to prevent mic from picking up speaker audio or accepting new commands.
            _is_system_handled = (
                result.source == "system_module"
                or (result.source == "cloud_llm" and self._is_system_module_intent(result.intent))
            )
            if _is_system_handled:
                self._system_speak_done.clear()
                try:
                    await asyncio.wait_for(self._system_speak_done.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("Voice pipeline: system module TTS timeout (15s)")
            elif result.response:
                await self.publish("voice.response", {"text": result.response, "query": text})
                logger.info("Voice pipeline: speaking...")
                await self._stream_speak(result.response)
                await self.publish("voice.speak_done", {"text": result.response})

            # Track assistant response in session (trim to last 10 exchanges)
            if result.response:
                self._session.append({"role": "assistant", "content": result.response})
            if len(self._session) > 20:
                self._session = self._session[-20:]

            # History
            duration_ms = int((time.monotonic() - start_ts) * 1000)
            if self._voice_history:
                from system_modules.voice_core.voice_history import VoiceRecord
                await self._voice_history.add(VoiceRecord(
                    timestamp=time.time(),
                    user_id=None,
                    wake_word=self._config.get("wake_word_model", ""),
                    recognized_text=text,
                    intent=result.intent,
                    response=result.response,
                    duration_ms=duration_ms,
                ))

            logger.info("Voice pipeline: complete (%dms)", duration_ms)

        except Exception as exc:
            logger.error("Voice pipeline error: %s", exc)
        finally:
            self._state = STATE_IDLE

    # ── Chime ────────────────────────────────────────────────────────────

    async def _play_chime(self) -> None:
        chime_path = "/var/lib/selena/sounds/listen.wav"
        if not Path(chime_path).exists():
            return
        output_device = self._get_output_device()
        loop = asyncio.get_running_loop()

        def _play() -> None:
            cmd = ["paplay"]
            if output_device:
                cmd.append("--device=" + output_device)
            cmd.append(chime_path)
            subprocess.run(cmd, timeout=3, capture_output=True)

        try:
            await loop.run_in_executor(None, _play)
        except Exception:
            pass

    # ── LLM ──────────────────────────────────────────────────────────────

    async def _query_llm(self, text: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "http://localhost:7070/api/ui/setup/llm/chat",
                    json={"text": text},
                )
                if resp.status_code != 200:
                    logger.error("LLM query failed: HTTP %d", resp.status_code)
                    return ""
                data = resp.json()
                if data.get("status") == "ok":
                    return data.get("response", "")
                logger.error("LLM error: %s", data.get("error", "unknown"))
                return ""
        except Exception as exc:
            logger.error("LLM query failed: %s", exc)
            return ""

    # ── TTS + Playback ───────────────────────────────────────────────────

    async def _stream_speak(self, text: str) -> None:
        """Stream TTS: piper --output-raw | paplay --raw.

        Audio starts playing as soon as piper generates the first samples.
        No intermediate file, no waiting for full synthesis.
        """
        from system_modules.voice_core.tts import sanitize_for_tts, PIPER_BIN, MODELS_DIR, _load_tts_settings

        clean = sanitize_for_tts(text)
        if not clean:
            return

        voice = self._tts.voice if self._tts else os.getenv("PIPER_VOICE", "uk_UA-ukrainian_tts-medium")
        model_path = str(Path(MODELS_DIR) / f"{voice}.onnx")
        settings = _load_tts_settings()
        output_device = self._get_output_device()
        loop = asyncio.get_running_loop()

        def _pipe() -> None:
            piper_cmd = [
                PIPER_BIN, "--model", model_path, "--output-raw",
                "--length-scale", str(settings.length_scale),
                "--noise-scale", str(settings.noise_scale),
                "--noise-w-scale", str(settings.noise_w_scale),
                "--sentence-silence", str(settings.sentence_silence),
                "--speaker", str(settings.speaker),
            ]
            try:
                from core.hardware import should_use_gpu, onnxruntime_has_gpu
                if should_use_gpu() and onnxruntime_has_gpu():
                    piper_cmd.append("--cuda")
            except Exception:
                pass

            play_cmd = [
                "paplay", "--raw",
                "--format=s16le", "--rate=22050", "--channels=1",
            ]
            if output_device:
                play_cmd.append("--device=" + output_device)

            piper_proc = None
            play_proc = None
            try:
                piper_proc = subprocess.Popen(
                    piper_cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                play_proc = subprocess.Popen(
                    play_cmd,
                    stdin=piper_proc.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                piper_proc.stdout.close()
                piper_proc.stdin.write(clean.encode("utf-8"))
                piper_proc.stdin.close()
                play_proc.wait(timeout=120)
                piper_proc.wait(timeout=5)
                logger.info("Voice pipeline: playback complete")
            except FileNotFoundError as e:
                logger.warning("piper or paplay not found: %s", e)
            except subprocess.TimeoutExpired:
                logger.warning("TTS stream timed out")
            except Exception as e:
                logger.error("Stream speak error: %s", e)
            finally:
                for p in [piper_proc, play_proc]:
                    if p:
                        try:
                            p.kill()
                        except Exception:
                            pass

        try:
            await loop.run_in_executor(None, _pipe)
        except Exception as exc:
            logger.warning("Stream speak failed: %s", exc)

    # ── Privacy ──────────────────────────────────────────────────────────

    async def _on_privacy_change(self, enabled: bool) -> None:
        event_type = "voice.privacy_on" if enabled else "voice.privacy_off"
        await self.publish(event_type, {"privacy_mode": enabled})
        self._privacy_mode = enabled

    async def _rephrase_via_llm(self, default_text: str) -> str:
        """Ask Cloud LLM to rephrase a module's hardcoded response naturally.

        Uses conversation session for context. Falls back to default_text on failure.
        """
        try:
            from core.config_writer import read_config
            config = read_config()
            voice_cfg = config.get("voice", {})
            provider = voice_cfg.get("llm_provider", "ollama")
            if provider in ("ollama", "llamacpp"):
                return default_text

            p_cfg = voice_cfg.get("providers", {}).get(provider, {})
            api_key = p_cfg.get("api_key", "")
            model = p_cfg.get("model", "")
            if not api_key or not model:
                return default_text

            lang = self._detect_lang()
            lang_names = {"uk": "Ukrainian", "en": "English"}
            lang_name = lang_names.get(lang, "English")

            system = (
                f"You are a smart home voice assistant. Speak ONLY {lang_name}.\n"
                "The system performed an action and generated a default response.\n"
                "Rephrase it naturally and concisely (1 sentence, no emoji, no markdown).\n"
                "Vary your phrasing — don't repeat the same structure.\n"
                "Keep it short for TTS. Plain text only."
            )

            # Build context from session
            messages_ctx = ""
            if self._last_query:
                messages_ctx += f"User said: \"{self._last_query}\"\n"
            if self._last_intent:
                messages_ctx += f"Classified intent: {self._last_intent}\n"
            messages_ctx += f"Default response: \"{default_text}\"\n"
            messages_ctx += "Your rephrased response:"

            from system_modules.llm_engine.cloud_providers import generate
            import asyncio as _aio
            rephrased = await _aio.wait_for(
                generate(provider, api_key, model, messages_ctx, system, temperature=0.9),
                timeout=8.0,
            )

            rephrased = rephrased.strip().strip('"').strip("'")
            if rephrased and len(rephrased) < 300:
                return rephrased
        except Exception as exc:
            logger.debug("Rephrase via LLM failed: %s", exc)

        return default_text

    async def _on_voice_event(self, event: Any) -> None:
        if event.type == "voice.speak" and self._tts:
            text = event.payload.get("text", "")
            if text:
                # Rephrase hardcoded module response via Cloud LLM for variety
                text = await self._rephrase_via_llm(text)
                await self._stream_speak(text)
                await self.publish("voice.speak_done", {"text": text})
                self._system_speak_done.set()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        from system_modules.voice_core.stt import get_stt
        from system_modules.voice_core.tts import get_tts
        from system_modules.voice_core.speaker_id import get_speaker_id
        from system_modules.voice_core.voice_history import get_voice_history
        from system_modules.voice_core.privacy import on_privacy_change

        self._stt = get_stt(self._config["stt_model"])
        self._tts = get_tts(self._config["tts_voice"])
        self._speaker_id = get_speaker_id()
        self._voice_history = get_voice_history()

        on_privacy_change(self._on_privacy_change)
        self.subscribe(["voice.speak"], self._on_voice_event)

        # Start single audio loop
        self._listen_task = asyncio.create_task(self._audio_loop())

        # Start GPIO privacy listener
        from system_modules.voice_core.privacy import gpio_listener_loop
        self._privacy_task = asyncio.create_task(gpio_listener_loop())

        await self.publish("module.started", {"name": self.name})
        logger.info("VoiceCoreModule started")

    async def stop(self) -> None:
        if self._listen_task:
            self._listen_task.cancel()
            await asyncio.gather(self._listen_task, return_exceptions=True)
            self._listen_task = None
        if self._privacy_task:
            self._privacy_task.cancel()
            await asyncio.gather(self._privacy_task, return_exceptions=True)
            self._privacy_task = None
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})
        logger.info("VoiceCoreModule stopped")

    # ── API Routes ───────────────────────────────────────────────────────

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            return {"status": "ok", "module": svc.name}

        @router.get("/config")
        async def get_config() -> JSONResponse:
            from system_modules.voice_core.privacy import is_privacy_mode
            cfg = dict(svc._config)
            cfg["privacy_mode"] = is_privacy_mode()
            cfg["state"] = svc._state
            return JSONResponse(cfg)

        @router.post("/config")
        async def update_config(req: VoiceConfigRequest) -> JSONResponse:
            from system_modules.voice_core.privacy import set_privacy_mode
            from core.config_writer import update_config as _persist

            updates = req.model_dump(exclude_none=True)
            for k, v in updates.items():
                if k == "privacy_mode":
                    await set_privacy_mode(v)
                else:
                    svc._config[k] = v
                    _persist("voice", k, v)

            # Reload STT model if changed
            if "stt_model" in updates:
                from system_modules.voice_core.stt import reload_stt
                svc._stt = reload_stt(updates["stt_model"])
                # Restart audio loop with new model
                if svc._listen_task:
                    svc._listen_task.cancel()
                    await asyncio.sleep(0.5)
                    svc._listen_task = asyncio.create_task(svc._audio_loop())

            if "speaker_threshold" in updates:
                import system_modules.voice_core.speaker_id as sid
                sid.SIMILARITY_THRESHOLD = updates["speaker_threshold"]

            return JSONResponse({"ok": True, "config": svc._config})

        @router.get("/privacy")
        async def get_privacy() -> JSONResponse:
            from system_modules.voice_core.privacy import is_privacy_mode
            return JSONResponse({"privacy_mode": is_privacy_mode()})

        @router.post("/privacy/toggle")
        async def toggle_privacy() -> JSONResponse:
            from system_modules.voice_core.privacy import toggle_privacy_mode
            new_state = await toggle_privacy_mode()
            return JSONResponse({"privacy_mode": new_state})

        @router.get("/audio/devices")
        async def list_audio_devices() -> JSONResponse:
            from system_modules.voice_core.audio_manager import detect_audio_devices
            devices = detect_audio_devices()
            return JSONResponse({
                "inputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.inputs],
                "outputs": [{"id": d.id, "name": d.name, "type": d.type} for d in devices.outputs],
            })

        @router.get("/stt/status")
        async def stt_status() -> JSONResponse:
            return JSONResponse({
                "model": svc._config["stt_model"],
                "available": svc._stt is not None,
            })

        @router.get("/tts/voices")
        async def list_voices() -> JSONResponse:
            if svc._tts is None:
                raise HTTPException(503, "TTS not ready")
            return JSONResponse({"voices": svc._tts.list_voices()})

        @router.post("/tts/test")
        async def test_tts(req: SynthesizeRequest) -> JSONResponse:
            if svc._tts is None:
                raise HTTPException(503, "TTS not ready")
            wav_bytes = await svc._tts.synthesize(req.text, req.voice)
            if not wav_bytes:
                raise HTTPException(500, "Synthesis failed")
            return JSONResponse({"ok": True, "size_bytes": len(wav_bytes)})

        @router.get("/speakers")
        async def list_speakers() -> JSONResponse:
            if svc._speaker_id is None:
                raise HTTPException(503, "Speaker ID not ready")
            return JSONResponse({"speakers": svc._speaker_id.list_enrolled()})

        @router.delete("/speakers/{user_id}")
        async def remove_speaker(user_id: str) -> JSONResponse:
            if svc._speaker_id is None:
                raise HTTPException(503, "Speaker ID not ready")
            ok = svc._speaker_id.remove_enrollment(user_id)
            if not ok:
                raise HTTPException(404, "Speaker not found")
            return JSONResponse({"ok": True, "removed": user_id})

        @router.get("/wakeword/status")
        async def wakeword_status() -> JSONResponse:
            return JSONResponse({
                "model": svc._config.get("wake_word_model", ""),
                "state": svc._state,
                "running": svc._listen_task is not None and not svc._listen_task.done(),
            })

        @router.get("/history")
        async def get_history(limit: int = 50) -> JSONResponse:
            if svc._voice_history is None:
                raise HTTPException(503, "Voice history not ready")
            records = await svc._voice_history.get_recent(min(limit, 200))
            return JSONResponse({"records": records})

        @router.get("/intents")
        async def list_intents() -> JSONResponse:
            """List all registered voice intents from all sources."""
            import re as _re
            from system_modules.llm_engine.intent_router import get_intent_router
            router = get_intent_router()
            intents: list[dict] = []

            # System module intents (Tier 1.5)
            for entry in router._system_intents:
                param_names: list[str] = []
                for patterns in entry.patterns.values():
                    for p in patterns:
                        param_names.extend(_re.findall(r"\?P<(\w+)>", p))
                intents.append({
                    "module": entry.module,
                    "intent": entry.intent,
                    "description": entry.description or "",
                    "priority": entry.priority,
                    "params": sorted(set(param_names)),
                    "source": "system_module",
                })

            # Fast matcher rules (Tier 1)
            try:
                from system_modules.llm_engine.fast_matcher import get_fast_matcher
                for rule in get_fast_matcher()._rules:
                    name = rule.get("name", "")
                    if not name:
                        continue
                    intents.append({
                        "module": "fast-matcher",
                        "intent": name,
                        "description": ", ".join(rule.get("keywords", [])[:3]),
                        "priority": 0,
                        "params": [],
                        "source": "fast_matcher",
                    })
            except Exception:
                pass

            # Module Bus intents (Tier 2)
            try:
                from core.module_bus import get_module_bus
                for item in get_module_bus()._intent_index:
                    module_name = getattr(item, "module", "")
                    desc = getattr(item, "description", "")
                    intents.append({
                        "module": module_name,
                        "intent": f"module.{module_name}",
                        "description": desc,
                        "priority": getattr(item, "priority", 0),
                        "params": [],
                        "source": "module_bus",
                    })
            except Exception:
                pass

            return JSONResponse({"intents": intents, "total": len(intents)})

        @router.post("/test-command")
        async def test_command(req: TestCommandRequest) -> JSONResponse:
            """Run text through the full intent pipeline (simulates voice command)."""
            import time as _time
            from system_modules.llm_engine.intent_router import get_intent_router

            start_ts = _time.monotonic()
            text = req.text.strip()
            if not text:
                raise HTTPException(400, "Empty text")

            lang = svc._detect_lang()
            result, trace_steps = await get_intent_router().route(
                text, user_id=None, lang=lang, trace=True,
            )

            # Set session context for LLM rephrase
            svc._last_query = text
            svc._last_intent = result.intent

            tts_done = False
            # Cloud LLM intents that map to system modules are handled
            # by the module itself via EventBus (voice.intent → module.handle → module.speak)
            _sys_handled = (
                result.source == "system_module"
                or (result.source == "cloud_llm" and svc._is_system_module_intent(result.intent))
            )
            if req.speak and _sys_handled:
                svc._system_speak_done.clear()
                try:
                    await asyncio.wait_for(svc._system_speak_done.wait(), timeout=15.0)
                    tts_done = True
                except asyncio.TimeoutError:
                    pass
            elif req.speak and result.response:
                await svc.publish("voice.response", {"text": result.response, "query": text})
                await svc._stream_speak(result.response)
                await svc.publish("voice.speak_done", {"text": result.response})
                tts_done = True

            duration_ms = int((_time.monotonic() - start_ts) * 1000)

            # Save to history
            if svc._voice_history:
                from system_modules.voice_core.voice_history import VoiceRecord
                await svc._voice_history.add(VoiceRecord(
                    timestamp=_time.time(),
                    user_id=None,
                    wake_word="[text-test]",
                    recognized_text=text,
                    intent=result.intent,
                    response=result.response,
                    duration_ms=duration_ms,
                ))

            return JSONResponse({
                "ok": True,
                "input_text": text,
                "lang": lang,
                "intent": result.intent,
                "response": result.response,
                "source": result.source,
                "latency_ms": result.latency_ms,
                "duration_ms": duration_ms,
                "action": result.action,
                "params": result.params,
                "tts_played": tts_done,
                "trace": trace_steps,
            })

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        @router.websocket("/stream")
        async def audio_stream_ws(websocket: WebSocket) -> None:
            from system_modules.voice_core.webrtc_stream import audio_stream_ws as _handler
            await _handler(websocket)

        return router
