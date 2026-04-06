"""
system_modules/voice_core/module.py — Voice Core SystemModule.

Single audio loop architecture:
  - One parecord process (PulseAudio) captures mic continuously
  - Vosk STT processes audio in streaming mode (chunk-by-chunk)
  - State machine: IDLE → LISTENING → PROCESSING
    IDLE:       Vosk grammar recognizer (wake word phrases only)
    LISTENING:  Vosk full recognizer, collects command, stops on silence
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

    Used by test-command when Vosk STT is not available.
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

    STT often misrecognizes names as similar-sounding words.
    Uses rule-based substitutions as a fast fallback.
    """
    phrase = phrase.lower().strip()
    if not phrase:
        return []

    variants: set[str] = {phrase}

    # Cyrillic phonetic substitutions (common STT misrecognitions)
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

    # Truncated forms (STT sometimes drops last character)
    for word in phrase.split():
        if len(word) >= 4:
            variants.add(word[:-1])

    variants = {v for v in variants if len(v) >= 3}
    return sorted(variants)


async def generate_wake_variants_llm(phrase: str, lang: str = "uk") -> list[str]:
    """Generate phonetic variants via LLM (cloud provider).

    Asks the LLM to produce words that STT might output when
    someone says the wake phrase. Returns list of variants or empty.
    """
    try:
        from core.llm import llm_call
        from core.lang_utils import lang_code_to_name
        lang_name = lang_code_to_name(lang)

        prompt_text = (
            f'The word "{phrase}" is a voice assistant wake word in {lang_name}. '
            f"When someone says this word, speech recognition (Vosk) sometimes "
            f"transcribes it incorrectly as a similar-sounding word.\n\n"
            f"Generate 15-20 common misrecognition variants that Vosk STT might "
            f"produce when someone says \"{phrase}\". Include:\n"
            f"- Words with similar vowel sounds (е↔и, о↔а, i↔e)\n"
            f"- Words with similar consonant sounds (с↔з, д↔т, г↔х)\n"
            f"- Truncated or slightly different endings\n"
            f"- Common phonetic confusions in {lang_name}\n\n"
            f"Output ONLY the variants, one per line, lowercase, no numbering, "
            f"no explanations. Include the original word first."
        )

        raw = await llm_call(
            prompt_text,
            system=(
                "You are a helpful linguistic assistant. "
                "Output exactly what the user asks, nothing else."
            ),
            temperature=0.3,
        )

        if not raw:
            return []

        # Parse LLM response: one variant per line
        variants = []
        for line in raw.strip().splitlines():
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
        self._pattern_response_spoken = False  # set when pipeline already spoke a template response → suppresses redundant LLM ack
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

        # Detected language (from config — Vosk uses per-language models)
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
            from core.llm import llm_call
            text = await llm_call("wake word activated, greet briefly", prompt_key="rephrase", temperature=0.9, timeout=5.0)
            if not text:
                text = "..."  # silent fallback
            from system_modules.voice_core.tts import sanitize_for_tts
            clean = sanitize_for_tts(text).lower()
            if clean:
                done = asyncio.Event()
                await self._enqueue_speech(clean, priority=0, done_event=done)
                await asyncio.wait_for(done.wait(), timeout=5.0)
        except Exception as exc:
            logger.debug("Wake response failed: %s", exc)

    @staticmethod
    def _drain_pipe(proc: "subprocess.Popen[bytes]") -> None:
        """Drain buffered audio from arecord pipe without blocking.

        During TTS playback the microphone keeps recording into the pipe buffer.
        Draining prevents stale audio (including TTS echo) from being fed to the
        recognizer when command listening starts.
        """
        import select as _sel
        fd = proc.stdout.fileno()
        drained = 0
        while _sel.select([fd], [], [], 0)[0]:
            chunk = proc.stdout.read(BYTES_PER_CHUNK)
            if not chunk:
                break
            drained += len(chunk)
        if drained:
            logger.debug("Drained %d bytes from arecord pipe after TTS", drained)

    def _idle_state(self) -> str:
        """Return the 'resting' state: IDLE if wake word enabled, LISTENING if disabled."""
        return STATE_IDLE if self._config.get("wake_word_enabled", True) else STATE_LISTENING

    def _get_silence_timeout(self) -> float:
        """Dynamic silence timeout based on how long the user has been speaking.

        Short commands (< 1.5s speech) → quick cutoff (base timeout).
        Longer phrases → more patience for pauses between words.
        """
        try:
            from core.config_writer import get_value
            base = float(get_value("voice", "stt_silence_timeout", 1.0))
        except Exception:
            base = 1.0
        speech_sec = self._speech_chunks_in_buffer * 0.25  # 250ms per chunk
        if speech_sec < 1.5:
            return base           # short command → quick reaction
        elif speech_sec < 3.0:
            return base + 0.5     # medium phrase → a bit more patience
        else:
            return base + 1.0     # long phrase → allow pauses between words

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

    @staticmethod
    def _prepare_audio_for_stt(audio_buffer: bytearray, rms_floor: float = 120.0) -> bytes:
        """Strip silent chunks from buffer before sending to STT.

        Removing quiet chunks improves recognition accuracy and
        reduces garbage output like "Кхмммм..." or random words.
        """
        import numpy as np
        result = bytearray()
        for i in range(0, len(audio_buffer) - BYTES_PER_CHUNK + 1, BYTES_PER_CHUNK):
            chunk = audio_buffer[i:i + BYTES_PER_CHUNK]
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples)))
            if rms >= rms_floor:
                result.extend(chunk)
        return bytes(result)

    @staticmethod
    def _speech_ratio(audio_buffer: bytearray, rms_floor: float = 120.0) -> float:
        """Fraction of chunks in buffer that contain speech (0.0–1.0)."""
        import numpy as np
        total = 0
        speech = 0
        for i in range(0, len(audio_buffer) - BYTES_PER_CHUNK + 1, BYTES_PER_CHUNK):
            chunk = audio_buffer[i:i + BYTES_PER_CHUNK]
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(samples * samples)))
            total += 1
            if rms >= rms_floor:
                speech += 1
        return speech / max(total, 1)

    # ── Main audio loop ──────────────────────────────────────────────────

    async def _audio_loop(self) -> None:
        """Single continuous loop: arecord → Vosk streaming → state machine.

        Every audio chunk is fed to Vosk immediately (no buffering).
        Vosk handles endpointing internally — silence chunks are important too.

        States:
        - IDLE: Vosk grammar recognizer (wake word phrases only)
        - LISTENING: Vosk full recognizer → finalize on silence or Vosk endpointer
        - PROCESSING: skip audio (TTS is playing)
        """
        loop = asyncio.get_running_loop()
        provider = self._stt_provider
        if provider is None:
            logger.error("Voice loop: no STT provider available, exiting")
            return

        from core.stt.vosk_provider import VoskProvider
        if not isinstance(provider, VoskProvider):
            logger.error("Voice loop: provider is not VoskProvider, exiting")
            return

        # Privacy gate: do not start arecord at all while privacy is enabled.
        # _on_privacy_change(False) will recreate this task when user disables it.
        if self._privacy_mode:
            logger.info("Voice loop: privacy mode ON — not starting arecord")
            return

        # Wait if mic test is running
        while self._mic_test_active:
            await asyncio.sleep(0.5)

        # Kill any stale arecord processes before starting
        try:
            subprocess.run(["pkill", "-f", "arecord.*S16_LE"], timeout=2,
                           capture_output=True)
            await asyncio.sleep(0.2)
        except Exception:
            pass

        input_device = self._get_input_device()
        cmd = ["arecord", "-t", "raw", "-f", "S16_LE", "-r", "16000", "-c", "1"]
        if input_device:
            cmd.extend(["-D", input_device])

        logger.info("Voice loop: starting arecord (input=%s)", input_device or "default")

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self._arecord_proc = proc
            # Check if arecord started successfully (give it 0.3s)
            await asyncio.sleep(0.3)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode(errors="replace")[:200] if proc.stderr else ""
                logger.error("Voice loop: arecord exited immediately: %s", stderr)
                return
        except Exception as e:
            logger.error("Voice loop: cannot start arecord: %s", e)
            return

        wake_phrase = self._get_wake_phrase()
        wake_enabled = self._config.get("wake_word_enabled", True)
        self._state = STATE_IDLE if wake_enabled else STATE_LISTENING
        await self._broadcast_state(self._state)

        # Speaker embedding buffer (only for voice focus)
        speaker_buffer = bytearray()
        last_speech_time = 0.0
        self._speech_chunks_in_buffer = 0
        listening_start = 0.0
        command_dispatched = False  # guard against duplicate dispatch

        if wake_enabled:
            logger.info("Voice loop: ready, wake phrase='%s' (Vosk grammar)", wake_phrase)
        else:
            logger.info("Voice loop: ready, wake word DISABLED (always listening)")

        try:
            while True:
                if self._privacy_mode:
                    logger.info("Voice loop: privacy mode toggled ON, exiting loop")
                    break
                if self._mic_test_active:
                    logger.info("Voice loop: pausing for mic test")
                    break

                data = await loop.run_in_executor(
                    None, proc.stdout.read, BYTES_PER_CHUNK
                )
                if not data or len(data) < BYTES_PER_CHUNK:
                    logger.warning("Voice loop: arecord stream ended, restarting...")
                    break

                if self._state == STATE_PROCESSING:
                    continue

                # Keep raw audio for Vosk (preprocessor can distort for STT)
                raw_data = data
                # Preprocessor only for energy/VAD detection — NOT for Vosk
                _, rms = self._preprocessor.process(data)
                has_speech = rms > self._energy_threshold

                if not has_speech:
                    self._preprocessor.update_noise_profile(raw_data)

                self._last_energy = rms
                self._last_has_speech = has_speech

                self._audio_debug_counter += 1
                if self._audio_debug_counter % 16 == 0:
                    logger.debug(
                        "Audio: energy=%.0f thr=%d speech=%s state=%s",
                        rms, self._energy_threshold, has_speech, self._state,
                    )

                # ── STATE: IDLE — feed ALL chunks to Vosk grammar ──
                if self._state == STATE_IDLE:
                    if has_speech:
                        speaker_buffer.extend(raw_data)

                    # Feed every chunk (speech + silence) to Vosk grammar
                    partial, final = provider.feed_idle(raw_data)

                    # Wake word triggers ONLY on final (not partial — avoids double activation)
                    if final:
                        self._log_live("stt", {"text": final, "lang": self._lang, "state": "idle"})
                        wake_phrase = self._get_wake_phrase()
                        if self._matches_phrase(final, wake_phrase):
                            logger.info("Voice: wake phrase detected: '%s'", final)
                            self._log_live("wake", {"phrase": wake_phrase})
                            await self.publish("voice.wake_word", {"wake_word": wake_phrase})
                            await self._capture_active_speaker(bytes(speaker_buffer))
                            provider.reset_idle()
                            provider.reset_listening()
                            await self._speak_wake_response()
                            # Drain pipe buffer accumulated during TTS playback
                            # (prevents TTS echo from bleeding into command recognition)
                            self._drain_pipe(proc)
                            provider.reset_listening()
                            self._state = STATE_LISTENING
                            await self._broadcast_state("listening")
                            command_dispatched = False
                            speaker_buffer.clear()
                            self._speech_chunks_in_buffer = 0
                            last_speech_time = 0.0
                            listening_start = time.monotonic()
                        else:
                            logger.debug("Voice idle heard: '%s'", final)

                    # Trim speaker buffer (keep last 3 sec for embedding)
                    max_speaker_bytes = SAMPLE_RATE * 2 * 3
                    if len(speaker_buffer) > max_speaker_bytes:
                        speaker_buffer = speaker_buffer[-max_speaker_bytes:]

                # ── STATE: LISTENING — feed ALL chunks to Vosk full ──
                # (double-check: if _process_command already dispatched, skip)
                elif self._state == STATE_LISTENING:
                    # Speaker gate
                    speaker_ok = await self._preprocessor.check_speaker_async(raw_data)
                    if not speaker_ok and has_speech:
                        continue

                    if has_speech:
                        self._speech_chunks_in_buffer += 1
                        last_speech_time = time.monotonic()

                    # Feed every chunk to Vosk (speech + silence for endpointer)
                    partial, final = provider.feed_listening(raw_data)

                    if partial:
                        self._log_live("partial", {"text": partial, "state": "listening"})

                    # Helper: dispatch command exactly once, reset all state
                    def _dispatch_command(cmd_text: str) -> None:
                        nonlocal last_speech_time, listening_start, command_dispatched
                        if command_dispatched:
                            return  # already dispatched for this listening session
                        command_dispatched = True
                        self._log_live("command", {"text": cmd_text, "lang": self._lang})
                        logger.info("Voice: command recognized: '%s' (lang=%s)", cmd_text, self._lang)
                        self._state = STATE_PROCESSING
                        asyncio.create_task(self._broadcast_state("processing"))
                        self._speech_chunks_in_buffer = 0
                        last_speech_time = 0.0
                        listening_start = 0.0
                        self._preprocessor.clear_active_speaker()
                        provider.reset_listening()
                        asyncio.create_task(self._process_command(cmd_text))

                    # Vosk internal endpointer returned a final result
                    if final and final.strip():
                        _dispatch_command(final.strip())
                        continue

                    # Silence timeout — user stopped speaking, finalize
                    silence_dur = time.monotonic() - last_speech_time if last_speech_time else 0
                    if last_speech_time and silence_dur >= self._get_silence_timeout():
                        text = provider.finalize_listening()
                        if text:
                            _dispatch_command(text)
                        else:
                            self._state = self._idle_state()
                            asyncio.create_task(self._broadcast_state(self._state))
                            self._speech_chunks_in_buffer = 0
                            last_speech_time = 0.0
                            self._preprocessor.clear_active_speaker()
                            provider.reset_listening()

                    # Listening timeout — user said nothing after activation
                    elif listening_start and not last_speech_time:
                        listen_wait = time.monotonic() - listening_start
                        listen_timeout = self._config.get("listen_timeout", 7.0)
                        if listen_wait >= listen_timeout:
                            logger.info("Voice: listening timeout (%.0fs no speech), back to idle", listen_wait)
                            self._log_live("timeout", {
                                "msg": f"Таймаут прослушивания ({listen_timeout:.0f}с) — возврат в ожидание",
                            })
                            self._state = self._idle_state()
                            asyncio.create_task(self._broadcast_state(self._state))
                            self._speech_chunks_in_buffer = 0
                            last_speech_time = 0.0
                            command_dispatched = False
                            self._preprocessor.clear_active_speaker()
                            provider.reset_listening()

                    # Safety: max 15 sec of active listening
                    elif listening_start and (time.monotonic() - listening_start) > 15.0:
                        text = provider.finalize_listening()
                        if text:
                            _dispatch_command(text)
                        else:
                            self._state = self._idle_state()
                            asyncio.create_task(self._broadcast_state(self._state))
                            self._speech_chunks_in_buffer = 0
                            last_speech_time = 0.0
                            self._preprocessor.clear_active_speaker()
                            provider.reset_listening()

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

        # Auto-restart loop (unless module is stopping or privacy is enabled)
        if self._privacy_mode:
            logger.info("Voice loop: not restarting — privacy mode active")
            return
        if self._listen_task and not self._listen_task.cancelled():
            logger.info("Voice loop: restarting in 2s...")
            await asyncio.sleep(2)
            self._listen_task = asyncio.create_task(self._audio_loop())

    # ── Vosk grammar setup ─────────────────────────────────────────────

    def _setup_vosk_grammar(self) -> None:
        """Configure Vosk grammar recognizer with wake word phrases.

        Loads saved wake word variants from config and sets them as grammar
        for the IDLE mode recognizer. Also creates LISTENING recognizer.
        """
        from core.stt.vosk_provider import VoskProvider
        p = self._stt_provider
        if not isinstance(p, VoskProvider):
            return

        # Load wake word variants from config (try both new and legacy formats)
        variants = []
        try:
            from core.config_writer import read_config
            cfg = read_config()
            voice_cfg = cfg.get("voice", {})
            # New format: voice.wake_word.vosk_grammar_variants
            variants = voice_cfg.get("wake_word", {}).get("vosk_grammar_variants", [])
            # Legacy format: voice.wake_word_variants (LLM-generated)
            if not variants:
                variants = voice_cfg.get("wake_word_variants", [])
        except Exception:
            pass

        # Fall back to wake phrase from module config
        if not variants:
            wake = self._get_wake_phrase()
            if wake:
                variants = [wake.lower().replace("_", " ")]

        if variants:
            p.set_grammar(variants)
            logger.info("Vosk grammar set with %d wake word variants", len(variants))

        # Create full-vocabulary recognizer for LISTENING mode
        p.create_listening_recognizer()

    async def _warmup_vosk(self) -> None:
        """Warm up Vosk model: LLM generates greeting → Piper speaks → Vosk transcribes.

        This JIT-primes all internal Vosk structures for faster first recognition.
        Runs as background task, does not block startup.
        """
        from core.stt.vosk_provider import VoskProvider
        p = self._stt_provider
        if not isinstance(p, VoskProvider) or not p.is_ready:
            return

        try:
            # 1. Generate greeting via LLM
            greeting_text = None
            try:
                from core.module_loader.sandbox import get_sandbox
                llm = get_sandbox().get_in_process_module("llm-engine")
                if llm and hasattr(llm, "generate"):
                    lang_name = {"en": "English", "uk": "Ukrainian", "ru": "Russian"}.get(p.lang, p.lang)
                    prompt = (
                        f"Generate ONE short greeting phrase in {lang_name} "
                        f"(2-5 words, like 'hello, ready to help'). "
                        f"Return ONLY the phrase, no quotes, no explanation."
                    )
                    greeting_text = await llm.generate(prompt, max_tokens=30)
            except Exception:
                pass

            if not greeting_text:
                greeting_text = "привіт, система готова" if p.lang == "uk" else "hello, system ready"

            # 2. Synthesize via Piper
            audio_bytes = None
            if self._tts:
                try:
                    audio_bytes = await self._tts.synthesize(greeting_text.strip())
                except Exception:
                    pass

            # 3. Feed to Vosk for warm-up
            loop = asyncio.get_running_loop()
            if audio_bytes:
                # Piper returns WAV — extract raw PCM
                import wave, io
                try:
                    with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                        pcm = wf.readframes(wf.getnframes())
                    await loop.run_in_executor(None, p.warmup, pcm)
                except Exception:
                    await loop.run_in_executor(None, p.warmup, None)
            else:
                await loop.run_in_executor(None, p.warmup, None)

            logger.info("Vosk warm-up complete (lang=%s)", p.lang)
        except Exception as e:
            logger.debug("Vosk warm-up skipped: %s", e)

    # ── Language detection ──────────────────────────────────────────────

    def _detect_lang(self) -> str:
        """Return current language from config (Vosk uses per-language models)."""
        return self._lang

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

        Each step is logged to live monitor for full pipeline visibility.
        """
        start_ts = time.monotonic()
        try:
            logger.info("Voice pipeline: recognized '%s'", text)
            await self.publish("voice.recognized", {"text": text})

            # Route through IntentRouter (includes LLM as Tier 3 fallback)
            stt_lang = self._detect_lang()
            tts_lang = self._tts_primary_lang

            self._log_live("routing", {
                "text": text, "lang": stt_lang,
                "msg": "IntentRouter: поиск интента...",
            })

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

            # Privacy mode is owned by voice-core itself — handle inline.
            # Otherwise the intent would fall into the system_module branch
            # below and hang for 15s waiting for an external module to ack.
            if result.intent in ("privacy_on", "privacy_off"):
                if result.response:
                    from system_modules.voice_core.tts import sanitize_for_tts
                    tts_text = sanitize_for_tts(result.response).lower()
                    if tts_text:
                        done_ev = asyncio.Event()
                        await self._enqueue_speech(tts_text, priority=0, done_event=done_ev)
                        try:
                            await asyncio.wait_for(done_ev.wait(), timeout=8.0)
                        except asyncio.TimeoutError:
                            pass
                from system_modules.voice_core.privacy import set_privacy_mode
                await set_privacy_mode(result.intent == "privacy_on")
                logger.info(
                    "Voice pipeline: %s applied via voice command",
                    result.intent,
                )
                return

            # System modules handle their own TTS via EventBus (voice.speak).
            _is_system_handled = (
                result.source == "system_module"
                or (result.source in ("llm", "cloud") and self._is_system_module_intent(result.intent))
            )
            if _is_system_handled:
                # device.on / device.off defer all speech to device-control's
                # speak_action() so the user hears the TRUTH (right room or
                # not_found) instead of a speculative LLM response that may
                # name a device that doesn't exist.
                _defer_to_module = result.intent in ("device.on", "device.off")
                # Speak the intermediate response (e.g. "Checking weather...")
                # BEFORE waiting for module to complete its action
                if result.response and not _defer_to_module:
                    self._log_live("tts", {
                        "text": result.response[:80],
                        "msg": "Озвучка промежуточного ответа...",
                    })
                    from system_modules.voice_core.tts import sanitize_for_tts
                    tts_text = sanitize_for_tts(result.response).lower()
                    if tts_text:
                        # Mark BEFORE the await — module's _on_voice_intent
                        # subscriber runs concurrently and may call
                        # speak_action() while TTS is still playing. Setting
                        # the flag now ensures _on_voice_event suppresses the
                        # redundant LLM ack regardless of timing.
                        self._pattern_response_spoken = True
                        done_ev = asyncio.Event()
                        await self._enqueue_speech(tts_text, priority=0, done_event=done_ev)
                        try:
                            await asyncio.wait_for(done_ev.wait(), timeout=10.0)
                        except asyncio.TimeoutError:
                            pass

                self._log_live("action", {
                    "intent": result.intent,
                    "msg": "Передано модулю — ожидание выполнения...",
                })
                self._system_speak_done.clear()
                try:
                    await asyncio.wait_for(self._system_speak_done.wait(), timeout=15.0)
                    self._log_live("done", {
                        "intent": result.intent,
                        "msg": "Модуль выполнил и озвучил",
                        "duration_ms": int((time.monotonic() - start_ts) * 1000),
                    })
                except asyncio.TimeoutError:
                    logger.warning("Voice pipeline: system module TTS timeout (15s)")
                    self._log_live("timeout", {
                        "intent": result.intent,
                        "msg": "Таймаут ожидания ответа от модуля (15с)",
                    })
            elif result.response:
                # Speak response directly — no rephrase needed.
                # LLM/cloud responses are already natural language.
                # Cache/system responses are short templates — rephrase wastes time.
                tts_text = result.response
                self._log_live("tts", {
                    "text": tts_text[:80],
                    "msg": "Piper TTS озвучивает...",
                })
                await self.publish("voice.response", {"text": tts_text, "query": text})
                logger.info("Voice pipeline: speaking (tts_lang=%s)...", tts_lang)
                done = asyncio.Event()
                await self._enqueue_speech(tts_text, priority=0, done_event=done)
                await done.wait()
                await self.publish("voice.speak_done", {"text": result.response})
                self._log_live("done", {
                    "msg": "Озвучка завершена",
                    "duration_ms": int((time.monotonic() - start_ts) * 1000),
                })
            else:
                self._log_live("done", {
                    "intent": result.intent,
                    "msg": "Интент без ответа",
                    "duration_ms": int((time.monotonic() - start_ts) * 1000),
                })

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
            self._pattern_response_spoken = False
            self._state = self._idle_state()
            await self._broadcast_state(self._state)

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
                    "http://localhost/api/ui/setup/llm/chat",
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
                if text != "__CHIME__":
                    await self._broadcast_state("speaking")
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
                    if text != "__CHIME__" and not self._privacy_mode:
                        await self._broadcast_state(self._idle_state())
                    if done_event:
                        done_event.set()
                    self._speech_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Speech worker unexpected error: %s", exc)

    # ── TTS + Playback ───────────────────────────────────────────────────

    async def _stream_speak(self, text: str, voice_override: str | None = None) -> None:
        """TTS playback: fetch raw PCM from native Piper server → aplay.

        Piper TTS runs natively on the host as piper-tts.service (GPU-accelerated).
        Single voice — always the primary voice from config, unless
        ``voice_override`` is given (e.g. UI test endpoint testing a non-default voice).
        """
        from system_modules.voice_core.tts import sanitize_for_tts, _load_tts_settings

        clean = sanitize_for_tts(text)
        if not clean:
            return

        # Force lowercase — Piper VITS models garble uppercase letters
        clean = clean.lower()

        # Single voice: override or primary from config
        if voice_override:
            voice = voice_override
        else:
            try:
                from core.config_writer import read_config
                cfg = read_config()
                voice = cfg.get("voice", {}).get("tts", {}).get("primary", {}).get("voice", "") \
                    or (self._tts.voice if self._tts else "")
            except Exception:
                voice = self._tts.voice if self._tts else ""

        settings = _load_tts_settings()
        output_device = self._get_output_device()
        loop = asyncio.get_running_loop()

        tts_result = await self._fetch_tts_raw(clean, voice, settings)
        if not tts_result:
            logger.error("TTS HTTP request failed for voice=%s — playback skipped", voice)
            return
        pcm_data, sample_rate = tts_result
        await loop.run_in_executor(
            None, self._play_raw_pcm, pcm_data, output_device, sample_rate,
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

    # ── Privacy ──────────────────────────────────────────────────────────

    async def _broadcast_state(self, state: str) -> None:
        """Push voice state to all connected UI clients via SyncManager WebSocket.

        state ∈ {idle, listening, processing, speaking, privacy}
        """
        try:
            from core.api.sync_manager import get_sync_manager
            await get_sync_manager().publish("voice.state", {
                "state": state,
                "privacy_mode": self._privacy_mode,
            })
        except Exception as e:
            logger.debug("voice.state broadcast failed: %s", e)

    async def _on_privacy_change(self, enabled: bool) -> None:
        # Update internal flag FIRST so the audio loop sees it on next iteration
        self._privacy_mode = enabled

        if enabled:
            # Hard-stop arecord and cancel the listen task — release the mic.
            if self._arecord_proc is not None:
                try:
                    self._arecord_proc.kill()
                    self._arecord_proc.wait(timeout=2)
                except Exception:
                    pass
                self._arecord_proc = None

            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()
                try:
                    await asyncio.wait_for(self._listen_task, timeout=2)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
                self._listen_task = None

            # Reset Vosk grammar/listening state so leftover audio doesn't bleed through.
            try:
                provider = self._stt_provider
                if provider is not None:
                    provider.reset_idle()
                    provider.reset_listening()
            except Exception:
                pass

            # Belt-and-braces: ensure no stray arecord process is alive
            try:
                subprocess.run(
                    ["pkill", "-f", "arecord.*S16_LE"],
                    timeout=2,
                    capture_output=True,
                )
            except Exception:
                pass

            await self._broadcast_state("privacy")
        else:
            # Recreate the audio loop task — it will start a fresh arecord.
            if self._listen_task is None or self._listen_task.done():
                self._listen_task = asyncio.create_task(self._audio_loop())
            await self._broadcast_state(self._idle_state())

        # Legacy module event for any subscribers (intent router, audit, etc.)
        event_type = "voice.privacy_on" if enabled else "voice.privacy_off"
        await self.publish(event_type, {"privacy_mode": enabled})

    @staticmethod
    def _num2words_available() -> bool:
        try:
            import num2words  # noqa: F401
            return True
        except ImportError:
            return False

    async def _rephrase_via_llm(self, default_text: str) -> str:
        """Ask LLM to rephrase text for natural TTS output."""
        from core.llm import llm_call

        lang = self._tts_primary_lang or self._detect_lang()
        from core.lang_utils import lang_code_to_name
        lang_name = lang_code_to_name(lang)

        ctx = f"[{lang_name}]\n"
        if self._last_query:
            ctx += f"\"{self._last_query}\"\n"
        if self._last_intent:
            ctx += f"{self._last_intent}\n"
        ctx += f"\"{default_text}\""

        result = await llm_call(ctx, prompt_key="rephrase", temperature=0.9, timeout=10.0)
        if result and len(result) < 300:
            from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
            return preprocess_for_tts(result, lang)

        from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
        return preprocess_for_tts(default_text, lang)

    async def _generate_via_llm(self, action_context: dict) -> str:
        """Ask LLM to generate TTS response from action context."""
        from core.llm import llm_call
        import json as _json

        intent = action_context.get("intent", "unknown")
        ctx_parts = {k: v for k, v in action_context.items() if k != "intent"}
        ctx_summary = ", ".join(f"{k}={v}" for k, v in ctx_parts.items()) if ctx_parts else "done"
        fallback_text = f"{intent}: {ctx_summary}"

        lang = self._tts_primary_lang or self._detect_lang()
        from core.lang_utils import lang_code_to_name
        lang_name = lang_code_to_name(lang)

        ctx = f"[{lang_name}]\n"
        if self._last_query:
            ctx += f"\"{self._last_query}\"\n"
        ctx += f"{intent}\n"
        if ctx_parts:
            ctx += f"{_json.dumps(ctx_parts, ensure_ascii=False, default=str)}\n"

        result = await llm_call(ctx, prompt_key="rephrase", temperature=0.9, timeout=10.0)
        if result:
            # Multi-day forecasts and similar rich responses can legitimately
            # exceed a few hundred characters in Ukrainian. Trim only when
            # absurdly long (LLM ramble / hallucination).
            if len(result) > 1200:
                result = result[:1200].rsplit(".", 1)[0] + "."
            from system_modules.voice_core.tts_preprocessor import preprocess_for_tts
            return preprocess_for_tts(result, lang)

        return fallback_text

    async def _on_voice_event(self, event: Any) -> None:
        if event.type == "voice.speak" and self._tts:
            action_ctx = event.payload.get("action_context")
            speech_id = event.payload.get("speech_id")

            # If the pipeline already spoke a pattern/template response for
            # this intent AND the action succeeded, the module's post-action
            # LLM ack is redundant. Errors/not_found must still be spoken so
            # the user is not misled by the intermediate response.
            if action_ctx and self._pattern_response_spoken:
                action_result = str(action_ctx.get("result", "")).lower()
                if action_result in ("", "ok", "success", "done"):
                    self._log_live("skip_ack", {
                        "intent": action_ctx.get("intent", ""),
                        "msg": "Подавлено повторное озвучивание (паттерн уже озвучен)",
                    })
                    done_payload: dict[str, Any] = {"text": ""}
                    if speech_id:
                        done_payload["speech_id"] = speech_id
                    await self.publish("voice.speak_done", done_payload)
                    self._system_speak_done.set()
                    return

            if action_ctx:
                # LLM generates response from structured action context
                text = await self._generate_via_llm(action_ctx)
            else:
                text = event.payload.get("text", "")
                if text:
                    # Rephrase/translate existing text via LLM
                    text = await self._rephrase_via_llm(text)

            if text:
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

        # Create STT provider (Vosk — auto-detected from config)
        try:
            from core.stt import create_stt_provider
            self._stt_provider = create_stt_provider()
            logger.info("STT provider created: %s", type(self._stt_provider).__name__)
            # Set up Vosk grammar for wake word detection in IDLE mode
            self._setup_vosk_grammar()
        except Exception as e:
            logger.warning("Failed to create STT provider: %s", e)
            self._stt_provider = None

        self._stt = None  # legacy field, kept for API compat

        # Single-voice TTS: load primary voice via TTSEngine (HTTP client)
        from system_modules.voice_core.tts import get_tts_engine
        tts_engine = get_tts_engine()
        primary_voice = self._config.get("tts_voice", "uk_UA-ukrainian_tts-medium")

        tts_cfg: dict = {}
        try:
            from core.config_writer import read_config
            cfg = read_config()
            tts_cfg = cfg.get("voice", {}).get("tts", {})
            if tts_cfg:
                primary_voice = tts_cfg.get("primary", {}).get("voice", primary_voice)
        except Exception:
            pass

        tts_engine.load_voices(primary=primary_voice)
        self._tts = tts_engine
        # Primary language: from config, fallback to extraction from voice name
        self._tts_primary_lang = tts_cfg.get("primary", {}).get("lang", "") if tts_cfg else ""
        if not self._tts_primary_lang:
            self._tts_primary_lang = primary_voice.split("_")[0] if "_" in primary_voice else primary_voice.split("-")[0]
        logger.info("TTS language: %s (single voice)", self._tts_primary_lang)

        # Set default lang from primary TTS voice (Vosk uses per-language models)
        self._lang = self._tts_primary_lang

        self._speaker_id = get_speaker_id()
        self._voice_history = get_voice_history()

        on_privacy_change(self._on_privacy_change)
        self.subscribe(["voice.speak"], self._on_voice_event)

        # Start speech queue worker (serializes all TTS playback)
        self._speech_worker_task = asyncio.create_task(
            self._speech_worker(), name="tts-speech-worker",
        )

        # Connect live logging to this module's live monitor
        try:
            from core.llm import set_live_log as set_llm_live_log
            set_llm_live_log(self._log_live)
        except Exception:
            pass
        try:
            from system_modules.llm_engine.intent_router import get_intent_router
            get_intent_router().set_live_log(self._log_live)
        except Exception:
            pass

        # Warm up Vosk model (JIT priming via LLM → Piper → Vosk)
        asyncio.create_task(self._warmup_vosk())

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

        svc._register_health_endpoint(router)

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
                    svc._setup_vosk_grammar()
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
                # Update Vosk grammar with new variants
                svc._setup_vosk_grammar()
                # Restart audio loop to pick up new grammar
                if svc._listen_task:
                    svc._listen_task.cancel()
                    await asyncio.sleep(0.5)
                    svc._listen_task = asyncio.create_task(svc._audio_loop())

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
            vosk_info = {}
            if svc._stt_provider and hasattr(svc._stt_provider, "status"):
                vosk_info = svc._stt_provider.status()
            return JSONResponse({
                "provider": provider_name,
                "lang": svc._lang,
                "available": svc._stt_provider is not None,
                "ready": vosk_info.get("ready", svc._stt_provider is not None),
                "model_path": vosk_info.get("model_path", ""),
                "grammar_phrases": vosk_info.get("grammar_phrases", []),
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
            tts_lang = svc._tts_primary_lang
            result, trace_steps = await get_intent_router().route(
                text, user_id=None, lang=lang, tts_lang=tts_lang, trace=True,
            )

            # Set session context for LLM rephrase
            svc._last_query = text
            svc._last_intent = result.intent

            tts_done = False

            # Privacy mode is owned by voice-core itself — apply inline
            if result.intent in ("privacy_on", "privacy_off"):
                if req.speak and result.response:
                    from system_modules.voice_core.tts import sanitize_for_tts
                    tts_text = sanitize_for_tts(result.response).lower()
                    if tts_text:
                        try:
                            await svc._stream_speak(tts_text)
                            tts_done = True
                        except Exception as tts_exc:
                            logger.warning("test-command privacy TTS failed: %s", tts_exc)
                from system_modules.voice_core.privacy import set_privacy_mode
                await set_privacy_mode(result.intent == "privacy_on")
                duration_ms = int((_time.monotonic() - start_ts) * 1000)
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
                })

            # LLM/Cloud intents that map to system modules are handled by the
            # module itself via EventBus (voice.intent → module.handle → module.speak).
            # Must include both "llm" and "cloud" sources, otherwise the test
            # endpoint speaks result.response while the module ALSO speaks via
            # speak_action() — causing double TTS playback.
            _sys_handled = (
                result.source == "system_module"
                or (result.source in ("llm", "cloud") and svc._is_system_module_intent(result.intent))
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

        svc._register_html_routes(router, __file__)

        @router.websocket("/stream")
        async def audio_stream_ws(websocket: WebSocket) -> None:
            from system_modules.voice_core.webrtc_stream import audio_stream_ws as _handler
            await _handler(websocket)

        return router
