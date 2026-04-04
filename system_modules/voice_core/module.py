"""
system_modules/voice_core/module.py — Voice Core SystemModule.

Single audio loop architecture:
  - One parecord process (PulseAudio) captures mic continuously
  - STT provider processes audio (Whisper/OpenAI — auto-detected)
  - State machine: IDLE → LISTENING → PROCESSING
    IDLE:       STT recognizes speech, checks for activation phrase
    LISTENING:  Collects user command after activation, stops on silence
    PROCESSING: Sends to IntentRouter, synthesizes TTS, plays response
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
    energy_threshold: int | None = Field(None, ge=10, le=10000)
    min_speech_chunks: int | None = Field(None, ge=1, le=30)


class TranscribeRequest(BaseModel):
    sample_rate: int = 16000


class SynthesizeRequest(BaseModel):
    text: str
    voice: str | None = None


def _detect_text_lang(text: str, primary_lang: str = "") -> str:
    """Detect language from text using Unicode script + word heuristics.

    Used by test-command when Whisper STT is not available.
    """
    import re as _re
    import unicodedata

    # Count characters by script
    cyrillic = len(_re.findall(r'[А-Яа-яІіЇїЄєҐґЁёЎўЪъЫы]', text))
    latin = len(_re.findall(r'[A-Za-zÀ-ÿ]', text))
    arabic = len(_re.findall(r'[\u0600-\u06FF]', text))
    cjk = len(_re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', text))

    if cyrillic > latin:
        # Distinguish Cyrillic languages by specific letters/words
        if _re.search(r'[ІіЇїЄєҐґ]', text):
            return "uk"
        if _re.search(r'[ЁёЫыЭэ]', text):
            return "ru"
        # Bulgarian: ъ or article suffixes (-та, -то, -те, какво, времето)
        lower = text.lower()
        if _re.search(r'[Ъъ]', text) or _re.search(r'\b\w+(?:та|то|те)\b', lower) or _re.search(r'\b(?:какво|колко|къде)\b', lower):
            return "bg"
        # Serbian Latin markers would be caught above; Cyrillic Serbian
        if _re.search(r'[ЉљЊњЋћЏџЂђ]', text):
            return "sr"
        return primary_lang  # default Cyrillic → primary

    if arabic > 0:
        return "ar"
    if cjk > 0:
        return "zh"

    if latin > 0:
        lower = text.lower()
        # German markers
        if _re.search(r'\b(das|der|die|ist|ein|und|nicht|ich|mach|bitte)\b', lower):
            return "de"
        # French markers
        if _re.search(r'\b(le|la|les|est|une|des|pas|que|sur|dans|fait)\b', lower):
            return "fr"
        # Spanish markers
        if _re.search(r'\b(el|los|las|una|que|por|para|como|está)\b', lower):
            return "es"
        # Polish markers
        if _re.search(r'[ąćęłńóśźż]', lower):
            return "pl"
        # Default Latin → English
        return "en"

    return "en"


class TestCommandRequest(BaseModel):
    text: str
    speak: bool = True
    lang: str | None = None  # auto-detect if not provided


class EnrollSpeakerRequest(BaseModel):
    user_id: str


# ── TTS text preparation ─────────────────────────────────────────────────────

_NUM2WORDS_LANGS = {"uk": "uk", "en": "en", "de": "de", "fr": "fr", "es": "es", "pl": "pl"}


def _numbers_to_words(text: str, lang: str) -> str:
    """Replace all numbers in text with words using num2words.

    Handles integers and decimals. Falls back to original text if num2words
    is not installed or language not supported.
    """
    try:
        from num2words import num2words
    except ImportError:
        return text

    n2w_lang = _NUM2WORDS_LANGS.get(lang, "en")

    import re
    def _replace(m: re.Match) -> str:
        s = m.group(0)
        try:
            if "." in s or "," in s:
                val = float(s.replace(",", "."))
                return num2words(val, lang=n2w_lang)
            return num2words(int(s), lang=n2w_lang)
        except Exception:
            return s

    return re.sub(r"\d+[.,]\d+|\d+", _replace, text)


# ── State constants ──────────────────────────────────────────────────────────

STATE_IDLE = "idle"            # waiting for wake phrase
STATE_LISTENING = "listening"  # recording user command
STATE_PROCESSING = "processing"  # LLM + TTS


def generate_wake_variants(phrase: str) -> list[str]:
    """Generate phonetic variants of a wake phrase for robust STT matching.

    Whisper often misrecognizes names as similar-sounding words.
    Uses rule-based substitutions as a fast fallback.
    """
    phrase = phrase.lower().strip()
    if not phrase:
        return []

    variants: set[str] = {phrase}

    # Cyrillic phonetic substitutions (common Whisper misrecognitions)
    _CYR_SUBS: list[tuple[str, list[str]]] = [
        ("е", ["и", "є", "э"]),
        ("и", ["е", "і", "ы"]),
        ("і", ["и", "е", "ї"]),
        ("о", ["а"]),
        ("а", ["о"]),
        ("с", ["з", "ц"]),
        ("з", ["с"]),
        ("г", ["х", "ґ"]),
        ("к", ["г"]),
        ("д", ["т"]),
        ("т", ["д"]),
        ("б", ["п"]),
        ("п", ["б"]),
        ("в", ["ф"]),
        ("л", ["р"]),
        ("ц", ["с"]),
        ("ж", ["ш"]),
        ("ш", ["щ"]),
    ]
    _LAT_SUBS: list[tuple[str, list[str]]] = [
        ("e", ["a", "i"]),
        ("a", ["e", "o"]),
        ("i", ["e", "y"]),
        ("s", ["z", "c"]),
        ("c", ["s", "k"]),
        ("k", ["c", "g"]),
        ("g", ["k"]),
        ("v", ["w", "f"]),
        ("th", ["t"]),
    ]

    import re
    is_cyrillic = bool(re.search(r'[а-яіїєґ]', phrase))
    subs = _CYR_SUBS if is_cyrillic else _LAT_SUBS

    for pattern, replacements in subs:
        if pattern in phrase:
            for repl in replacements:
                variants.add(phrase.replace(pattern, repl, 1))

    # Truncated forms (Whisper sometimes drops last character)
    for word in phrase.split():
        if len(word) >= 4:
            variants.add(word[:-1])

    variants = {v for v in variants if len(v) >= 3}
    return sorted(variants)


async def generate_wake_variants_llm(phrase: str, lang: str = "uk") -> list[str]:
    """Generate phonetic variants via LLM (cloud provider).

    Asks the LLM to produce words that Whisper STT might output when
    someone says the wake phrase. Returns list of variants or empty.
    """
    try:
        from system_modules.llm_engine.cloud_providers import generate as llm_generate
        from core.config_writer import read_config
        cfg = read_config()

        # Find available cloud provider (config → env)
        api_key = ""
        provider = ""
        model = ""
        llm_cfg = cfg.get("llm", {})
        _env_keys = {
            "google": ("GEMINI_API_KEY", "gemini-2.0-flash"),
            "openai": ("OPENAI_API_KEY", "gpt-4o-mini"),
            "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-4-20250514"),
            "groq": ("GROQ_API_KEY", "llama-3.1-8b-instant"),
        }
        for prov_name, (env_name, default_model) in _env_keys.items():
            key = llm_cfg.get(f"{prov_name}_api_key", "") or os.getenv(env_name, "")
            if key:
                api_key = key
                provider = prov_name
                models = llm_cfg.get(f"{prov_name}_models", [])
                model = models[0] if models else llm_cfg.get(f"{prov_name}_model", default_model)
                break

        if not api_key or not provider:
            return []

        from core.lang_utils import lang_code_to_name
        lang_name = lang_code_to_name(lang)

        prompt = (
            f'The word "{phrase}" is a voice assistant wake word in {lang_name}. '
            f"When someone says this word, speech recognition (Whisper) sometimes "
            f"transcribes it incorrectly as a similar-sounding word.\n\n"
            f"Generate 15-20 common misrecognition variants that Whisper STT might "
            f"produce when someone says \"{phrase}\". Include:\n"
            f"- Words with similar vowel sounds (е↔и, о↔а, i↔e)\n"
            f"- Words with similar consonant sounds (с↔з, д↔т, г↔х)\n"
            f"- Truncated or slightly different endings\n"
            f"- Common phonetic confusions in {lang_name}\n\n"
            f"Output ONLY the variants, one per line, lowercase, no numbering, "
            f"no explanations. Include the original word first."
        )

        result = await llm_generate(
            provider=provider,
            api_key=api_key,
            model=model,
            prompt=prompt,
            temperature=0.3,
        )

        if not result:
            return []

        # Parse LLM response: one variant per line
        variants = []
        for line in result.strip().splitlines():
            word = line.strip().lower().strip("- •·0123456789.)")
            if word and len(word) >= 3 and len(word) <= 30:
                variants.append(word)

        logger.info("LLM generated %d wake variants for '%s'", len(variants), phrase)
        return variants

    except Exception as e:
        logger.warning("LLM wake variant generation failed: %s", e)
        return []


def _rms_energy(pcm_data: bytes) -> float:
    """Compute RMS energy of 16-bit signed PCM audio chunk."""
    import struct as _struct
    if len(pcm_data) < 2:
        return 0.0
    n_samples = len(pcm_data) // 2
    samples = _struct.unpack(f"<{n_samples}h", pcm_data[:n_samples * 2])
    sum_sq = sum(s * s for s in samples)
    return (sum_sq / n_samples) ** 0.5


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
        self._last_spoken: str = ""                # last TTS text (after rephrase, for debug)

        # Speech queue: serializes all TTS playback (priority, timestamp, text, done_event, voice_override)
        self._speech_queue: asyncio.PriorityQueue[tuple[int, float, str, asyncio.Event | None, str | None]] = (
            asyncio.PriorityQueue(maxsize=200)
        )
        self._speech_worker_task: asyncio.Task | None = None

        # Mic test lock: when set, voice loop pauses to release the device
        self._mic_test_active = False
        # Last RMS energy from audio loop (for mic-level monitoring without lock)
        self._last_energy: float = 0.0
        self._last_has_speech: bool = False

        # Audio loop state (promoted from locals for API observability)
        _default_thr = int(os.getenv("VOICE_ENERGY_THRESHOLD", "300"))
        _default_chunks = int(os.getenv("VOICE_MIN_SPEECH_CHUNKS", "6"))
        try:
            from core.config_writer import read_config as _rc
            _vc = _rc().get("voice", {})
            _default_thr = int(_vc.get("energy_threshold", _default_thr))
            _default_chunks = int(_vc.get("min_speech_chunks", _default_chunks))
        except Exception:
            pass
        self._energy_threshold: int = _default_thr
        self._min_speech_chunks: int = _default_chunks
        self._speech_chunks_in_buffer: int = 0
        self._idle_buffer_start: float = 0.0
        self._audio_debug_counter: int = 0

        # Audio preprocessor (noise reduction, AGC, speaker gate)
        from system_modules.voice_core.audio_preprocessor import get_audio_preprocessor
        self._preprocessor = get_audio_preprocessor(SAMPLE_RATE, CHUNK_SAMPLES)

        # Live debug log: ring buffer of recent STT events for UI terminal
        self._live_log: list[dict] = []
        self._live_log_max = 100
        # Current arecord process (for killing when mic test starts)
        self._arecord_proc: subprocess.Popen | None = None

        # Detected language from last STT result (auto-updated by Whisper)
        # Default language — set properly in start() from Piper config
        self._lang: str = ""
        # STT provider (created in start())
        self._stt_provider = None

        # Defaults from env, overridden by core.yaml
        defaults = {
            "stt_model": os.getenv("STT_MODEL", "small"),
            "tts_voice": os.getenv("PIPER_VOICE", "uk_UA-ukrainian_tts-medium"),
            "wake_word_model": os.getenv("WAKE_WORD_MODEL", "селена"),
            "wake_word_enabled": True,  # False = always listening (no wake word needed)
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

    def _log_live(self, event: str, data: dict | None = None) -> None:
        """Append to live debug log (ring buffer for UI terminal)."""
        import time as _t
        entry = {"ts": _t.time(), "event": event, **(data or {})}
        self._live_log.append(entry)
        if len(self._live_log) > self._live_log_max:
            self._live_log = self._live_log[-self._live_log_max:]

    async def _capture_active_speaker(self, audio_bytes: bytes) -> None:
        """Capture speaker embedding from wake word audio for voice focus."""
        try:
            if self._speaker_id is None:
                return
            loop = asyncio.get_running_loop()
            audio_float = self._speaker_id._audio_to_float(audio_bytes)
            embedding = await loop.run_in_executor(
                None, self._speaker_id._compute_embedding, audio_float,
            )
            if embedding is not None:
                self._preprocessor.set_active_speaker(embedding)
        except Exception as e:
            logger.debug("Speaker capture failed: %s", e)

    async def _speak_wake_response(self) -> None:
        """Speak a short confirmation after wake phrase detected, then listen."""
        try:
            from core.i18n import t
            text = t("voice.wake_response", lang=self._tts_primary_lang)
            from system_modules.voice_core.tts import sanitize_for_tts
            clean = sanitize_for_tts(text).lower()
            if clean:
                done = asyncio.Event()
                await self._enqueue_speech(clean, priority=0, done_event=done)
                await asyncio.wait_for(done.wait(), timeout=5.0)
        except Exception as exc:
            logger.debug("Wake response failed: %s", exc)

    def _idle_state(self) -> str:
        """Return the 'resting' state: IDLE if wake word enabled, LISTENING if disabled."""
        return STATE_IDLE if self._config.get("wake_word_enabled", True) else STATE_LISTENING

    def _get_silence_timeout(self) -> float:
        try:
            from core.config_writer import get_value
            return float(get_value("voice", "stt_silence_timeout", 1.0))
        except Exception:
            return 1.0

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
        # Prefer mixer device (routes through dmix for concurrent playback)
        try:
            from core.audio_mixer import get_mixer
            mixer = get_mixer()
            if mixer.is_initialized():
                return mixer.get_device("tts")
        except Exception:
            pass
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

    def _matches_phrase(self, text: str, phrase: str) -> bool:
        """Match wake word with phonetic variant tolerance.

        Uses generated variants (common STT misrecognitions) + fuzzy matching.
        """
        from difflib import SequenceMatcher

        t = text.lower().strip()
        # Exact substring match
        if phrase in t:
            return True

        text_words = t.split()
        # Get pre-generated variants (includes the original phrase)
        variants = self._get_wake_variants()

        for variant in variants:
            variant_words = variant.split()
            # For single-word variants: check each text word
            if len(variant_words) == 1:
                for tw in text_words:
                    # Exact match
                    if tw == variant:
                        return True
                    # Fuzzy match: SequenceMatcher ratio >= 0.75
                    if len(tw) >= 3 and SequenceMatcher(None, tw, variant).ratio() >= 0.75:
                        return True
            else:
                # Multi-word variant: check as substring
                if variant in t:
                    return True

        return False

    def _get_wake_variants(self) -> list[str]:
        """Return wake phrase + phonetic variants from config cache."""
        if not hasattr(self, "_wake_variants_cache"):
            self._wake_variants_cache = []
        phrase = self._get_wake_phrase()
        if self._wake_variants_cache and self._wake_variants_cache[0] == phrase:
            return self._wake_variants_cache[1]
        # Load from config or generate
        variants = self._config.get("wake_word_variants", [])
        if not variants or not isinstance(variants, list):
            variants = generate_wake_variants(phrase)
        # Always include original
        if phrase and phrase not in variants:
            variants.insert(0, phrase)
        self._wake_variants_cache = [phrase, variants]
        return variants

    # ── Main audio loop ──────────────────────────────────────────────────

    async def _audio_loop(self) -> None:
        """Single continuous loop: parecord → buffer → STT provider → state machine.

        Audio buffering strategy:
        - IDLE: accumulate 2-3 sec segments, transcribe to detect wake phrase
        - LISTENING: accumulate audio, on silence timeout send full buffer to STT
        - PROCESSING: skip audio (TTS is playing)

        Energy-based VAD: chunks with RMS below threshold are "silent".
        """
        loop = asyncio.get_running_loop()
        provider = self._stt_provider
        if provider is None:
            logger.error("Voice loop: no STT provider available, exiting")
            return

        # Wait if mic test is running
        while self._mic_test_active:
            await asyncio.sleep(0.5)

        input_device = self._get_input_device()
        cmd = ["arecord", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]
        if input_device:
            cmd.extend(["-D", input_device])

        logger.info("Voice loop: starting arecord (input=%s)", input_device or "default")

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._arecord_proc = proc
        except Exception as e:
            logger.error("Voice loop: cannot start arecord: %s", e)
            return

        wake_phrase = self._get_wake_phrase()
        wake_enabled = self._config.get("wake_word_enabled", True)
        # If wake word disabled → start in LISTENING mode (always listening)
        self._state = STATE_IDLE if wake_enabled else STATE_LISTENING

        # Audio buffer for STT
        audio_buffer = bytearray()
        last_speech_time = 0.0
        self._speech_chunks_in_buffer = 0
        # IDLE: transcribe every N seconds to check wake phrase
        # 4 sec minimum for reliable Whisper language detection
        idle_interval_sec = 4.0
        self._idle_buffer_start = time.monotonic()

        async def _safe_transcribe(buf: bytes) -> tuple[str, str]:
            """Transcribe with error handling. Returns (text, lang)."""
            try:
                r = await provider.transcribe(buf, SAMPLE_RATE)
                return r.text.strip(), r.lang or self._lang
            except Exception as exc:
                logger.warning("STT transcription error: %s", exc)
                return "", self._lang

        if wake_enabled:
            logger.info("Voice loop: ready, wake phrase='%s'", wake_phrase)
        else:
            logger.info("Voice loop: ready, wake word DISABLED (always listening)")

        try:
            while True:
                if self._privacy_mode or self._mic_test_active:
                    if self._mic_test_active:
                        logger.info("Voice loop: pausing for mic test")
                        break
                    await asyncio.sleep(0.5)
                    continue

                data = await loop.run_in_executor(
                    None, proc.stdout.read, BYTES_PER_CHUNK
                )
                if not data or len(data) < BYTES_PER_CHUNK:
                    logger.warning("Voice loop: arecord stream ended, restarting...")
                    break

                if self._state == STATE_PROCESSING:
                    continue

                # Audio preprocessing (noise reduction, AGC)
                raw_data = data  # keep raw for noise profiling
                data, rms = self._preprocessor.process(data)
                has_speech = rms > self._energy_threshold

                # Update noise profile from RAW audio during silence
                if not has_speech:
                    self._preprocessor.update_noise_profile(raw_data)

                # Store energy for mic-level monitoring (no mic lock needed)
                self._last_energy = rms
                self._last_has_speech = has_speech

                # Periodic debug logging (~every 4 sec)
                self._audio_debug_counter += 1
                if self._audio_debug_counter % 16 == 0:
                    logger.debug(
                        "Audio: energy=%.0f thr=%d speech=%s chunks=%d/%d state=%s",
                        rms, self._energy_threshold, has_speech,
                        self._speech_chunks_in_buffer, self._min_speech_chunks, self._state,
                    )

                # ── STATE: IDLE — buffer and check for wake phrase ──
                if self._state == STATE_IDLE:
                    audio_buffer.extend(data)
                    if has_speech:
                        self._speech_chunks_in_buffer += 1
                    elapsed = time.monotonic() - self._idle_buffer_start

                    # Transcribe ONLY if there was enough speech energy (not just background noise)
                    if elapsed >= idle_interval_sec or (has_speech and elapsed >= 1.0):
                        if self._speech_chunks_in_buffer >= self._min_speech_chunks and len(audio_buffer) > BYTES_PER_CHUNK * 2:
                            text, detected_lang = await _safe_transcribe(bytes(audio_buffer))
                            self._lang = detected_lang

                            if text:
                                self._log_live("stt", {"text": text, "lang": detected_lang, "state": "idle"})
                                wake_phrase = self._get_wake_phrase()
                                if self._matches_phrase(text, wake_phrase):
                                    logger.info("Voice: wake phrase detected in '%s'", text)
                                    self._log_live("wake", {"phrase": wake_phrase})
                                    await self.publish("voice.wake_word", {"wake_word": wake_phrase})
                                    # Capture speaker embedding for voice focus
                                    await self._capture_active_speaker(bytes(audio_buffer))
                                    # Respond with "listening?" then switch to LISTENING
                                    await self._speak_wake_response()
                                    self._state = STATE_LISTENING
                                    audio_buffer.clear()
                                    self._speech_chunks_in_buffer = 0
                                    last_speech_time = 0.0  # reset — wait for new speech
                                else:
                                    logger.debug("Voice idle heard: '%s'", text)

                        audio_buffer.clear()
                        self._speech_chunks_in_buffer = 0
                        self._idle_buffer_start = time.monotonic()

                # ── STATE: LISTENING — accumulate command audio ──
                elif self._state == STATE_LISTENING:
                    # Speaker gate: check if current voice matches wake word speaker
                    speaker_ok = await self._preprocessor.check_speaker_async(data)
                    if not speaker_ok and has_speech:
                        # Different speaker — skip this chunk
                        continue

                    if has_speech:
                        audio_buffer.extend(data)
                        self._speech_chunks_in_buffer += 1
                        last_speech_time = time.monotonic()
                    elif last_speech_time:
                        # Keep buffering silence after speech (for natural pauses)
                        audio_buffer.extend(data)

                    # Check silence timeout — only if we had speech before
                    silence_dur = time.monotonic() - last_speech_time if last_speech_time else 0
                    if last_speech_time and silence_dur >= self._get_silence_timeout():
                        if self._speech_chunks_in_buffer >= self._min_speech_chunks and len(audio_buffer) > BYTES_PER_CHUNK:
                            # Transcribe the full command buffer
                            text, detected_lang = await _safe_transcribe(bytes(audio_buffer))
                            self._lang = detected_lang

                            if text:
                                self._log_live("command", {"text": text, "lang": self._lang})
                                logger.info("Voice: command recognized: '%s' (lang=%s)", text, self._lang)
                                self._state = STATE_PROCESSING
                                audio_buffer.clear()
                                self._speech_chunks_in_buffer = 0
                                self._preprocessor.clear_active_speaker()
                                asyncio.create_task(self._process_command(text))
                            else:
                                logger.debug("Voice: empty transcription, back to idle")
                                self._state = self._idle_state()
                                audio_buffer.clear()
                                self._speech_chunks_in_buffer = 0
                                self._idle_buffer_start = time.monotonic()
                                self._preprocessor.clear_active_speaker()
                        else:
                            # Not enough speech — discard and reset
                            self._state = self._idle_state()
                            audio_buffer.clear()
                            self._speech_chunks_in_buffer = 0
                            last_speech_time = 0.0
                            self._idle_buffer_start = time.monotonic()
                            self._preprocessor.clear_active_speaker()

                    # Safety: max 15 sec of listening
                    elif len(audio_buffer) > SAMPLE_RATE * 2 * 15:
                        text, detected_lang = await _safe_transcribe(bytes(audio_buffer))
                        self._lang = detected_lang
                        if text:
                            self._state = STATE_PROCESSING
                            audio_buffer.clear()
                            self._speech_chunks_in_buffer = 0
                            self._preprocessor.clear_active_speaker()
                            asyncio.create_task(self._process_command(text))
                        else:
                            self._state = self._idle_state()
                            audio_buffer.clear()
                            self._speech_chunks_in_buffer = 0
                            last_speech_time = 0.0
                            self._idle_buffer_start = time.monotonic()
                            self._preprocessor.clear_active_speaker()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Voice loop error: %s", e)
        finally:
            self._arecord_proc = None
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
        """Return current language detected by STT provider.

        For Whisper-based providers, this is auto-detected from speech.
        For non-Whisper providers, this defaults to 'en'.
        Updated automatically in _audio_loop() after each transcription.
        """
        return self._lang

    def _get_tts_for_lang(self, stt_lang: str) -> tuple:
        """Select TTS engine and response language based on STT-detected language.

        Returns (tts_engine, tts_lang):
          - Default: primary TTS voice + primary lang (always the main voice)
          - Fallback to EN voice ONLY when STT explicitly detects English
            AND primary TTS is NOT English
        """
        # Always prefer primary TTS voice for the main response language
        if not self._tts_fallback_lang or stt_lang != self._tts_fallback_lang:
            return self._tts, self._tts_primary_lang
        # STT detected the fallback language (e.g. English) explicitly
        return self._tts_fallback, self._tts_fallback_lang

    @staticmethod
    def _is_system_module_intent(intent: str) -> bool:
        """Check if intent belongs to a registered system module (DB-driven)."""
        try:
            from system_modules.llm_engine.intent_compiler import get_intent_compiler
            defn = get_intent_compiler().get_definition(intent)
            if defn and defn.module:
                return True
        except Exception:
            pass
        # Also check ModuleRegistry
        try:
            from core.module_registry import get_module_registry
            module = get_module_registry().get_module_for_intent(intent)
            if module:
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
            stt_lang = self._detect_lang()
            tts_engine, tts_lang = self._get_tts_for_lang(stt_lang)
            from system_modules.llm_engine.intent_router import get_intent_router
            result = await get_intent_router().route(
                text, user_id=None, lang=stt_lang, tts_lang=tts_lang,
            )

            self._log_live("intent", {
                "text": text, "intent": result.intent, "source": result.source,
                "response": result.response[:100] if result.response else "",
                "latency_ms": result.latency_ms,
            })
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
            # For system_module intents (or LLM-classified intents that map
            # to a system module), stay in PROCESSING until TTS completes
            # to prevent mic from picking up speaker audio or accepting new commands.
            _is_system_handled = (
                result.source == "system_module"
                or (result.source == "llm" and self._is_system_module_intent(result.intent))
            )
            if _is_system_handled:
                self._system_speak_done.clear()
                try:
                    await asyncio.wait_for(self._system_speak_done.wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("Voice pipeline: system module TTS timeout (15s)")
            elif result.response:
                from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
                tts_text = preprocess_for_tts(result.response, tts_lang)
                # Use fallback voice if STT language != primary TTS language
                use_voice = tts_engine.voice if tts_engine != self._tts else None
                await self.publish("voice.response", {"text": tts_text, "query": text})
                logger.info("Voice pipeline: speaking (tts_lang=%s)...", tts_lang)
                done = asyncio.Event()
                await self._enqueue_speech(tts_text, priority=0, done_event=done, voice=use_voice)
                await done.wait()
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
            self._state = self._idle_state()

    # ── Chime ────────────────────────────────────────────────────────────

    async def _play_chime(self) -> None:
        """Enqueue chime through speech queue to prevent overlaps."""
        chime_path = "/var/lib/selena/sounds/listen.wav"
        if not Path(chime_path).exists():
            return
        done = asyncio.Event()
        await self._enqueue_speech("__CHIME__", priority=0, done_event=done)
        await done.wait()

    async def _play_chime_internal(self) -> None:
        """Actually play the chime WAV via aplay (called from speech worker)."""
        chime_path = "/var/lib/selena/sounds/listen.wav"
        if not Path(chime_path).exists():
            return
        output_device = self._get_output_device()
        loop = asyncio.get_running_loop()

        def _play() -> None:
            cmd = ["aplay"]
            if output_device:
                cmd.extend(["-D", output_device])
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

    # ── Speech Queue ────────────────────────────────────────────────────

    async def _enqueue_speech(self, text: str, priority: int = 1,
                              done_event: asyncio.Event | None = None,
                              voice: str | None = None) -> None:
        """Add text to the speech queue. priority=0 high, 1 normal.
        voice: override TTS voice (e.g. fallback EN voice for non-primary language).
        """
        try:
            self._speech_queue.put_nowait((priority, time.monotonic(), text, done_event, voice))
        except asyncio.QueueFull:
            logger.warning("Speech queue full, dropping: %s", text[:60])
            if done_event:
                done_event.set()

    async def _speech_worker(self) -> None:
        """Long-running worker: pulls items from speech queue one at a time."""
        while True:
            try:
                priority, _ts, text, done_event, voice_override = await self._speech_queue.get()

                await self.publish("voice.tts_start", {"text": text})
                await asyncio.sleep(0.15)  # let ducking take effect

                try:
                    if text == "__CHIME__":
                        await self._play_chime_internal()
                    else:
                        await self._stream_speak(text, voice_override=voice_override)
                except Exception as exc:
                    logger.error("Speech worker playback error: %s", exc)
                finally:
                    await self.publish("voice.tts_done", {"text": text})
                    if done_event:
                        done_event.set()
                    self._speech_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Speech worker unexpected error: %s", exc)

    # ── TTS + Playback ───────────────────────────────────────────────────

    async def _stream_speak(self, text: str, voice_override: str | None = None) -> None:
        """TTS playback: fetch raw PCM from Piper server → paplay.

        Primary: HTTP request to native Piper server (GPU-accelerated on host).
        Fallback: local piper binary pipe (if server unavailable).
        voice_override: use specific voice (e.g. EN fallback) instead of config default.
        """
        from system_modules.voice_core.tts import sanitize_for_tts, TTSSettings, PIPER_BIN, MODELS_DIR, _load_tts_settings

        clean = sanitize_for_tts(text)
        if not clean:
            return

        # Force lowercase — Piper VITS models garble uppercase letters
        clean = clean.lower()

        # Select voice + settings: override > auto-detect from text language
        import re as _re
        is_primary = bool(_re.search(r'[А-Яа-яІіЇїЄєҐґЁё]', text))

        if voice_override:
            voice = voice_override
        else:
            try:
                from core.config_writer import read_config
                cfg = read_config()
                tts_cfg = cfg.get("voice", {}).get("tts", {})
                if is_primary:
                    voice = tts_cfg.get("primary", {}).get("voice", "") or self._tts.voice
                else:
                    voice = tts_cfg.get("fallback", {}).get("voice", "") or self._tts.voice
            except Exception:
                voice = self._tts.voice if self._tts else ""

        # Load per-voice settings from config
        try:
            from core.config_writer import read_config
            cfg = read_config()
            tts_cfg = cfg.get("voice", {}).get("tts", {})
            role = "primary" if is_primary else "fallback"
            voice_settings = tts_cfg.get(role, {}).get("settings", {})
            settings = TTSSettings(**voice_settings) if voice_settings else _load_tts_settings()
        except Exception:
            settings = _load_tts_settings()
        output_device = self._get_output_device()
        loop = asyncio.get_running_loop()

        # Try native Piper server first (GPU-accelerated, runs on host)
        tts_result = await self._fetch_tts_raw(clean, voice, settings)
        if tts_result:
            pcm_data, sample_rate = tts_result
            await loop.run_in_executor(
                None, self._play_raw_pcm, pcm_data, output_device, sample_rate,
            )
            return

        # Fallback: local piper binary pipe
        model_path = str(Path(MODELS_DIR) / f"{voice}.onnx")
        await loop.run_in_executor(
            None, self._pipe_piper_local, clean, model_path, settings, output_device,
        )

    async def _fetch_tts_raw(self, text: str, voice: str, settings) -> tuple[bytes, int] | None:
        """Fetch raw PCM from native Piper server. Returns (pcm_bytes, sample_rate) or None."""
        try:
            import httpx
            gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
            payload = {
                "text": text, "voice": voice,
                "length_scale": settings.length_scale,
                "noise_scale": settings.noise_scale,
                "noise_w_scale": settings.noise_w_scale,
                "sentence_silence": settings.sentence_silence,
                "speaker": settings.speaker,
                "volume": settings.volume,
            }
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{gpu_url}/synthesize/raw", json=payload)
                if resp.status_code == 200 and resp.content:
                    sample_rate = int(resp.headers.get("X-Audio-Rate", "22050"))
                    return resp.content, sample_rate
        except Exception:
            pass
        return None

    @staticmethod
    def _get_output_volume() -> float:
        """Read output_volume from config (0-150) and return as a 0.0-1.5 multiplier."""
        try:
            from core.config_writer import get_value
            vol = get_value("voice", "output_volume")
            if vol is not None:
                return max(0.0, min(1.5, int(vol) / 100.0))
        except Exception:
            pass
        return 1.0

    @staticmethod
    def _scale_pcm(pcm_data: bytes, volume: float) -> bytes:
        """Scale s16le PCM samples by a volume multiplier (software volume)."""
        if abs(volume - 1.0) < 0.01:
            return pcm_data
        import struct
        n = len(pcm_data) // 2
        samples = struct.unpack(f"<{n}h", pcm_data)
        scaled = struct.pack(f"<{n}h", *(
            max(-32768, min(32767, int(s * volume))) for s in samples
        ))
        return scaled

    @staticmethod
    def _play_raw_pcm(pcm_data: bytes, output_device: str | None, sample_rate: int = 22050) -> None:
        """Play raw PCM s16le mono via aplay (ALSA direct) with software volume."""
        # Prepend 150ms silence — prevents aplay pipe from cutting first syllable
        silence_bytes = b'\x00\x00' * int(sample_rate * 0.15)
        pcm_data = silence_bytes + pcm_data
        volume = VoiceCoreModule._get_output_volume()
        pcm_data = VoiceCoreModule._scale_pcm(pcm_data, volume)
        play_cmd = [
            "aplay", "-t", "raw",
            "-f", "S16_LE", "-r", str(sample_rate), "-c", "1",
        ]
        if output_device:
            play_cmd.extend(["-D", output_device])
        try:
            proc = subprocess.Popen(
                play_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            proc.stdin.write(pcm_data)
            proc.stdin.close()
            proc.wait(timeout=120)
            logger.info("Voice pipeline: playback complete")
        except Exception as e:
            logger.error("PCM playback error: %s", e)
        finally:
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

    @staticmethod
    def _pipe_piper_local(text: str, model_path: str, settings, output_device: str | None) -> None:
        """Fallback: local piper binary → aplay with software volume."""
        from system_modules.voice_core.tts import PIPER_BIN
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
            "aplay", "-t", "raw",
            "-f", "S16_LE", "-r", "22050", "-c", "1",
        ]
        if output_device:
            play_cmd.extend(["-D", output_device])

        piper_proc = None
        play_proc = None
        try:
            piper_proc = subprocess.Popen(
                piper_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            piper_proc.stdin.write(text.encode("utf-8"))
            piper_proc.stdin.close()
            pcm_data = piper_proc.stdout.read()
            piper_proc.wait(timeout=30)

            volume = VoiceCoreModule._get_output_volume()
            pcm_data = VoiceCoreModule._scale_pcm(pcm_data, volume)

            play_proc = subprocess.Popen(
                play_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            play_proc.stdin.write(pcm_data)
            play_proc.stdin.close()
            play_proc.wait(timeout=120)
            logger.info("Voice pipeline: playback complete")
        except FileNotFoundError as e:
            logger.warning("piper or aplay not found: %s", e)
        except subprocess.TimeoutExpired:
            logger.warning("TTS stream timed out")
        except Exception as e:
            logger.error("Stream speak error: %s", e)
        finally:
            for p in [piper_proc, play_proc]:
                if p:
                    try:
                        p.kill()
                        p.wait(timeout=2)
                    except Exception:
                        pass

    # ── Privacy ──────────────────────────────────────────────────────────

    async def _on_privacy_change(self, enabled: bool) -> None:
        event_type = "voice.privacy_on" if enabled else "voice.privacy_off"
        await self.publish(event_type, {"privacy_mode": enabled})
        self._privacy_mode = enabled

    @staticmethod
    def _num2words_available() -> bool:
        try:
            import num2words  # noqa: F401
            return True
        except ImportError:
            return False

    async def _rephrase_via_llm(self, default_text: str) -> str:
        """Ask LLM to rephrase a module response for natural TTS output.

        Works with any provider (cloud, ollama, llamacpp).
        Converts numbers to words, translates foreign text, varies phrasing.
        Falls back to default_text on failure.
        """
        try:
            from core.config_writer import read_config
            config = read_config()
            voice_cfg = config.get("voice", {})
            provider = voice_cfg.get("llm_provider", "ollama")

            # Use TTS language (not STT) — response must match the voice output
            lang = self._tts_primary_lang or self._detect_lang()
            from core.lang_utils import lang_code_to_name
            lang_name = lang_code_to_name(lang)

            # Load rephrase prompt from DB (prompt_store)
            rephrase_rules = ""
            try:
                from core.prompt_store import get_prompt_store
                rephrase_rules = await get_prompt_store().get(lang, "rephrase_prompt")
            except Exception:
                pass
            if not rephrase_rules:
                from core.api.routes.voice_engines import DEFAULT_REPHRASE_PROMPT
                rephrase_rules = DEFAULT_REPHRASE_PROMPT

            # Load rephrase_system template from DB (editable via UI)
            rephrase_system_tpl = ""
            try:
                from core.prompt_store import get_prompt_store
                rephrase_system_tpl = await get_prompt_store().get(lang, "rephrase_system")
            except Exception:
                pass
            if rephrase_system_tpl and "{rephrase_rules}" in rephrase_system_tpl:
                try:
                    system = rephrase_system_tpl.format(
                        lang_name=lang_name, rephrase_rules=rephrase_rules)
                except (KeyError, IndexError):
                    system = rephrase_system_tpl
            else:
                system = (
                    f"You are a smart home voice assistant. Speak ONLY {lang_name}.\n"
                    f"{rephrase_rules}\n"
                    f"CRITICAL: All numbers MUST be spelled out as words in {lang_name}.\n"
                    f"CRITICAL: Translate ALL foreign words/names to {lang_name} or transliterate them.\n"
                    f"Output will be read aloud by TTS — no digits, no Latin letters, no symbols."
                )

            # Build context
            messages_ctx = ""
            if self._last_query:
                messages_ctx += f"User said: \"{self._last_query}\"\n"
            if self._last_intent:
                messages_ctx += f"Classified intent: {self._last_intent}\n"
            messages_ctx += f"Default response: \"{default_text}\"\n"
            messages_ctx += "Your rephrased response:"

            # For local providers, add language tag
            if provider in ("ollama", "llamacpp"):
                messages_ctx = f"[{lang_name}] {messages_ctx}"

            rephrased = ""

            if provider == "ollama":
                from system_modules.llm_engine.ollama_client import get_ollama_client
                rephrased = await asyncio.wait_for(
                    get_ollama_client().generate(prompt=messages_ctx, system=system),
                    timeout=10.0,
                )
            elif provider == "llamacpp":
                import httpx
                llamacpp_url = voice_cfg.get("llamacpp_url", "http://localhost:8081")
                messages = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": messages_ctx},
                ]
                async with httpx.AsyncClient(timeout=10) as http:
                    resp = await http.post(
                        f"{llamacpp_url}/v1/chat/completions",
                        json={"messages": messages, "temperature": 0.9, "max_tokens": 256},
                    )
                    resp.raise_for_status()
                    rephrased = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
            else:
                # Cloud provider
                p_cfg = voice_cfg.get("providers", {}).get(provider, {})
                api_key = p_cfg.get("api_key", "")
                model = p_cfg.get("model", "")
                if not api_key or not model:
                    return default_text
                from system_modules.llm_engine.cloud_providers import generate
                rephrased = await asyncio.wait_for(
                    generate(provider, api_key, model, messages_ctx, system, temperature=0.9),
                    timeout=8.0,
                )

            rephrased = rephrased.strip().strip('"').strip("'")
            if rephrased and len(rephrased) < 300:
                from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
                return preprocess_for_tts(rephrased, lang)
        except Exception as exc:
            logger.warning("Rephrase via LLM failed (provider=%s): %s", provider, exc)

        from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
        return preprocess_for_tts(default_text, self._tts_primary_lang or self._detect_lang())

    def _is_rephrase_enabled(self) -> bool:
        """Check if LLM rephrase is enabled in config (default: off)."""
        return self._config.get("rephrase_enabled", False)

    async def _on_voice_event(self, event: Any) -> None:
        if event.type == "voice.speak" and self._tts:
            text = event.payload.get("text", "")
            speech_id = event.payload.get("speech_id")
            if text:
                # Rephrase module response via LLM (if enabled)
                if self._is_rephrase_enabled():
                    text = await self._rephrase_via_llm(text)
                # Full TTS preprocessing: lowercase + numbers
                from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
                text = preprocess_for_tts(text, self._tts_primary_lang).lower()
                self._last_spoken = text  # capture for debug

                done = asyncio.Event()
                await self._enqueue_speech(text, priority=1, done_event=done)
                await done.wait()

                done_payload: dict[str, Any] = {"text": text}
                if speech_id:
                    done_payload["speech_id"] = speech_id
                await self.publish("voice.speak_done", done_payload)
                self._system_speak_done.set()

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        from system_modules.voice_core.tts import get_tts
        from system_modules.voice_core.speaker_id import get_speaker_id
        from system_modules.voice_core.voice_history import get_voice_history
        from system_modules.voice_core.privacy import on_privacy_change

        # Apply saved ALSA levels (mic gain, output volume) — survives container restarts
        try:
            from core.config_writer import read_config as _rc
            _voice = _rc().get("voice", {})
            _input_dev = _voice.get("audio_force_input", "")
            _gain = _voice.get("input_gain")
            if _input_dev and _gain is not None:
                import re as _re
                _m = _re.match(r"(?:plug)?hw:(\d+)", _input_dev)
                if _m:
                    _card = _m.group(1)
                    subprocess.run(
                        ["amixer", "-c", _card, "sset", "Mic", f"{_gain}%"],
                        timeout=3, capture_output=True,
                    )
                    logger.info("Applied mic gain %s%% on card %s", _gain, _card)
        except Exception as exc:
            logger.debug("ALSA gain apply: %s", exc)

        # Create STT provider (Whisper — auto-detected from config)
        try:
            from core.stt import create_stt_provider
            self._stt_provider = create_stt_provider()
            logger.info("STT provider created: %s", type(self._stt_provider).__name__)
        except Exception as e:
            logger.warning("Failed to create STT provider: %s", e)
            self._stt_provider = None

        self._stt = None  # legacy field, kept for API compat

        # Dual TTS: load primary + fallback PiperVoice via new TTSEngine
        from system_modules.voice_core.tts import get_tts_engine
        tts_engine = get_tts_engine()
        primary_voice = self._config.get("tts_voice", "uk_UA-ukrainian_tts-medium")
        fallback_voice = self._config.get("tts_fallback_voice", "en_US-ryan-low")

        # Check for new tts config format (voice.tts.primary/fallback)
        tts_cfg = {}
        try:
            from core.config_writer import read_config
            cfg = read_config()
            tts_cfg = cfg.get("voice", {}).get("tts", {})
            if tts_cfg:
                primary_voice = tts_cfg.get("primary", {}).get("voice", primary_voice)
                fallback_voice = tts_cfg.get("fallback", {}).get("voice", fallback_voice)
                primary_cuda = tts_cfg.get("primary", {}).get("cuda", False)
                fallback_cuda = tts_cfg.get("fallback", {}).get("cuda", False)
            else:
                primary_cuda = False
                fallback_cuda = False
        except Exception:
            primary_cuda = False
            fallback_cuda = False

        tts_engine.load_voices(
            primary=primary_voice,
            fallback=fallback_voice,
            primary_cuda=primary_cuda,
            fallback_cuda=fallback_cuda,
        )
        self._tts = tts_engine
        self._tts_fallback = tts_engine  # same engine, dual voice inside
        # Languages ONLY from Piper config (voice.tts.primary.lang / fallback.lang)
        # No hardcoded defaults — everything from config
        self._tts_primary_lang = tts_cfg.get("primary", {}).get("lang", "") if tts_cfg else ""
        if not self._tts_primary_lang:
            # Extract from voice name: "uk_UA-model" → "uk"
            self._tts_primary_lang = primary_voice.split("_")[0] if "_" in primary_voice else primary_voice.split("-")[0]
        self._tts_fallback_lang = tts_cfg.get("fallback", {}).get("lang", "") if tts_cfg else ""
        if not self._tts_fallback_lang:
            self._tts_fallback_lang = fallback_voice.split("_")[0] if "_" in fallback_voice else fallback_voice.split("-")[0]
        logger.info("TTS languages: primary=%s, fallback=%s (from config)", self._tts_primary_lang, self._tts_fallback_lang)

        # Set default lang from primary TTS voice (until Whisper detects actual language)
        self._lang = self._tts_primary_lang

        self._speaker_id = get_speaker_id()
        self._voice_history = get_voice_history()

        on_privacy_change(self._on_privacy_change)
        self.subscribe(["voice.speak"], self._on_voice_event)

        # Start speech queue worker (serializes all TTS playback)
        self._speech_worker_task = asyncio.create_task(
            self._speech_worker(), name="tts-speech-worker",
        )

        # Start single audio loop
        self._listen_task = asyncio.create_task(self._audio_loop())

        # Start GPIO privacy listener
        from system_modules.voice_core.privacy import gpio_listener_loop
        self._privacy_task = asyncio.create_task(gpio_listener_loop())

        await self.publish("module.started", {"name": self.name})
        logger.info("VoiceCoreModule started")

    async def stop(self) -> None:
        if self._speech_worker_task:
            self._speech_worker_task.cancel()
            await asyncio.gather(self._speech_worker_task, return_exceptions=True)
            self._speech_worker_task = None
        if self._listen_task:
            self._listen_task.cancel()
            await asyncio.gather(self._listen_task, return_exceptions=True)
            self._listen_task = None
        if self._privacy_task:
            self._privacy_task.cancel()
            await asyncio.gather(self._privacy_task, return_exceptions=True)
            self._privacy_task = None
        if self._stt_provider:
            await self._stt_provider.close()
            self._stt_provider = None
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

            # Reload STT provider if config changed
            if "stt_model" in updates:
                try:
                    from core.stt import create_stt_provider
                    if svc._stt_provider:
                        await svc._stt_provider.close()
                    svc._stt_provider = create_stt_provider()
                    logger.info("STT provider reloaded: %s", type(svc._stt_provider).__name__)
                except Exception as e:
                    logger.warning("STT provider reload failed: %s", e)
                if svc._listen_task:
                    svc._listen_task.cancel()
                    await asyncio.sleep(0.5)
                    svc._listen_task = asyncio.create_task(svc._audio_loop())

            if "speaker_threshold" in updates:
                import system_modules.voice_core.speaker_id as sid
                sid.SIMILARITY_THRESHOLD = updates["speaker_threshold"]

            # Apply audio loop thresholds at runtime (no restart needed)
            if "energy_threshold" in updates:
                svc._energy_threshold = updates["energy_threshold"]
            if "min_speech_chunks" in updates:
                svc._min_speech_chunks = updates["min_speech_chunks"]

            # Auto-generate wake word phonetic variants when name changes
            if "wake_word_model" in updates:
                phrase = updates["wake_word_model"].replace("_", " ").lower().strip()
                # Start with rule-based variants (instant)
                variants = generate_wake_variants(phrase)
                # Try LLM for better variants (async, language from TTS config)
                tts_lang = svc._tts_primary_lang or "uk"
                try:
                    llm_variants = await generate_wake_variants_llm(phrase, tts_lang)
                    if llm_variants:
                        # Merge: LLM variants + rule-based, deduplicated
                        combined = list(dict.fromkeys(llm_variants + variants))
                        variants = combined
                except Exception:
                    pass
                svc._config["wake_word_variants"] = variants
                _persist("voice", "wake_word_variants", variants)
                if hasattr(svc, "_wake_variants_cache"):
                    svc._wake_variants_cache = []
                logger.info("Wake word '%s': %d variants", phrase, len(variants))

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
            provider_name = type(svc._stt_provider).__name__ if svc._stt_provider else "none"
            return JSONResponse({
                "model": svc._config["stt_model"],
                "provider": provider_name,
                "lang": svc._lang,
                "available": svc._stt_provider is not None,
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

        @router.get("/audio/debug")
        async def audio_debug() -> JSONResponse:
            """Real-time audio pipeline debug: energy, VAD, state."""
            now = time.monotonic()
            idle_elapsed = now - svc._idle_buffer_start if svc._idle_buffer_start else 0.0
            return JSONResponse({
                "energy": round(svc._last_energy, 1),
                "energy_threshold": svc._energy_threshold,
                "has_speech": svc._last_has_speech,
                "speech_chunks": svc._speech_chunks_in_buffer,
                "min_speech_chunks": svc._min_speech_chunks,
                "state": svc._state,
                "idle_elapsed": round(idle_elapsed, 2),
                "privacy_mode": svc._privacy_mode,
                "arecord_running": svc._arecord_proc is not None,
                "stt_provider": type(svc._stt_provider).__name__ if svc._stt_provider else "none",
                "wake_enabled": svc._config.get("wake_word_enabled", True),
                "wake_phrase": svc._get_wake_phrase(),
            })

        @router.get("/live-log")
        async def live_log(since: float = 0) -> JSONResponse:
            """Get live STT/intent debug log entries since timestamp."""
            entries = [e for e in svc._live_log if e["ts"] > since]
            return JSONResponse({
                "entries": entries,
                "state": svc._state,
                "lang": svc._lang,
                "wake_enabled": svc._config.get("wake_word_enabled", True),
                "wake_phrase": svc._get_wake_phrase(),
                "energy": round(svc._last_energy, 1),
                "energy_threshold": svc._energy_threshold,
                "has_speech": svc._last_has_speech,
                "speech_chunks": svc._speech_chunks_in_buffer,
                "min_speech_chunks": svc._min_speech_chunks,
                "arecord_running": svc._arecord_proc is not None,
                "stt_provider": type(svc._stt_provider).__name__ if svc._stt_provider else "none",
                "privacy_mode": svc._privacy_mode,
            })

        @router.get("/history")
        async def get_history(limit: int = 50) -> JSONResponse:
            if svc._voice_history is None:
                raise HTTPException(503, "Voice history not ready")
            records = await svc._voice_history.get_recent(min(limit, 200))
            return JSONResponse({"records": records})

        @router.get("/intents")
        async def list_intents() -> JSONResponse:
            """List all registered intent definitions from DB."""
            intents: list[dict] = []
            try:
                from core.registry.models import IntentDefinition
                from sqlalchemy import select
                async with svc._db_session() as session:
                    result = await session.execute(
                        select(IntentDefinition).where(IntentDefinition.enabled == True)
                        .order_by(IntentDefinition.priority.desc())
                    )
                    for row in result.scalars():
                        import json as _json
                        intents.append({
                            "module": row.module or "",
                            "intent": row.intent,
                            "description": row.description or "",
                            "priority": row.priority,
                            "params": list(_json.loads(row.params_schema).keys()) if row.params_schema else [],
                            "source": row.source or "system",
                        })
            except Exception as exc:
                logger.warning("intents list error: %s", exc)
            return JSONResponse({"intents": intents, "total": len(intents)})

        @router.get("/patterns")
        async def list_patterns() -> JSONResponse:
            """List all regex patterns from DB."""
            patterns: list[dict] = []
            try:
                from core.registry.models import IntentPattern, IntentDefinition
                from sqlalchemy import select
                async with svc._db_session() as session:
                    result = await session.execute(
                        select(
                            IntentPattern.lang,
                            IntentPattern.pattern,
                            IntentPattern.source,
                            IntentDefinition.intent,
                            IntentDefinition.module,
                            IntentDefinition.priority,
                        )
                        .join(IntentDefinition, IntentPattern.intent_id == IntentDefinition.id)
                        .order_by(IntentDefinition.priority.desc(), IntentPattern.lang)
                    )
                    for row in result:
                        patterns.append({
                            "intent": row.intent or "",
                            "module": row.module or "",
                            "lang": row.lang,
                            "pattern": row.pattern,
                            "source": row.source or "system",
                            "priority": row.priority,
                        })
            except Exception as exc:
                logger.warning("patterns list error: %s", exc)
            return JSONResponse({"patterns": patterns, "total": len(patterns)})

        @router.post("/test-command")
        async def test_command(req: TestCommandRequest) -> JSONResponse:
            """Run text through the full intent pipeline (simulates voice command)."""
            import time as _time
            from system_modules.llm_engine.intent_router import get_intent_router

            start_ts = _time.monotonic()
            text = req.text.strip()
            if not text:
                raise HTTPException(400, "Empty text")

            # Detect language: explicit param > auto-detect from text
            if req.lang:
                lang = req.lang
            else:
                lang = _detect_text_lang(text, svc._tts_primary_lang)
            _tts_engine, tts_lang = svc._get_tts_for_lang(lang)
            result, trace_steps = await get_intent_router().route(
                text, user_id=None, lang=lang, tts_lang=tts_lang, trace=True,
            )

            # Set session context for LLM rephrase
            svc._last_query = text
            svc._last_intent = result.intent

            tts_done = False
            # Cloud LLM intents that map to system modules are handled
            # by the module itself via EventBus (voice.intent → module.handle → module.speak)
            _sys_handled = (
                result.source == "system_module"
                or (result.source == "llm" and svc._is_system_module_intent(result.intent))
            )
            if req.speak and _sys_handled:
                svc._system_speak_done.clear()
                try:
                    await asyncio.wait_for(svc._system_speak_done.wait(), timeout=30.0)
                    tts_done = True
                except asyncio.TimeoutError:
                    pass
            elif req.speak and result.response:
                from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
                tts_text = preprocess_for_tts(result.response, tts_lang)
                await svc.publish("voice.response", {"text": tts_text, "query": text})
                try:
                    await svc._stream_speak(tts_text)
                    tts_done = True
                except Exception as tts_exc:
                    logger.warning("test-command TTS failed: %s", tts_exc)
                await svc.publish("voice.speak_done", {"text": result.response})

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
                "raw_llm": result.raw_llm,
                "spoken_text": svc._last_spoken if tts_done else None,
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
