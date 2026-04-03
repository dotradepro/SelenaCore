"""
system_modules/voice_core/tts.py — Piper TTS engine (piper1-gpl)

Architecture:
  - Two PiperVoice objects loaded at startup, both hot in memory
  - Primary voice (e.g. uk, GPU) for main language
  - Fallback voice (en, CPU) for Latin/English segments
  - split_by_language() segments text → each segment uses the right voice
  - Switching between voices = Python variable, 0ms overhead

RAM: uk medium ~65MB (GPU) + en low ~5MB (CPU) = ~70MB total

Usage:
    engine = TTSEngine()
    engine.load_voices(primary="uk_UA-ukrainian_tts-medium", fallback="en_US-ryan-low")
    wav_bytes = await engine.synthesize("Вмикаю WiFi підключено", primary_lang="uk")
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import wave
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper")
DEFAULT_VOICE = os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium")
DEFAULT_FALLBACK = os.environ.get("PIPER_FALLBACK_VOICE", "en_US-ryan-low")


def sanitize_for_tts(text: str) -> str:
    """Clean text for Piper TTS — remove markdown, special chars, URLs, emoji etc."""
    s = text

    # Remove code blocks and inline code
    s = re.sub(r'```[\s\S]*?```', ' ', s)
    s = re.sub(r'`[^`]*`', ' ', s)

    # Remove markdown links [text](url) → text
    s = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', s)

    # Remove URLs
    s = re.sub(r'https?://\S+', '', s)

    # Remove markdown headers, bold/italic
    s = re.sub(r'^#{1,6}\s+', '', s, flags=re.MULTILINE)
    s = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', s)
    s = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', s)

    # Remove HTML tags
    s = re.sub(r'<[^>]+>', ' ', s)

    # Remove emoji
    s = re.sub(
        r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
        r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0000FE00-\U0000FE0F'
        r'\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF'
        r'\U00002600-\U000026FF\U0000200D\U00002B50\U00002B55]+', ' ', s,
    )

    # Remove bullet points and special chars
    s = re.sub(r'^[\s]*[-•*►▸▹→]\s*', '', s, flags=re.MULTILINE)
    s = re.sub(r'[~^|\\{}<>\[\]@#$%&=+`]', ' ', s)

    # Normalize quotes and dashes
    s = s.replace('«', '"').replace('»', '"')
    s = s.replace('"', '"').replace('"', '"')
    s = s.replace('—', ' — ').replace('–', ' — ')

    # Collapse whitespace
    s = re.sub(r'\n{3,}', '\n\n', s)
    s = re.sub(r'[ \t]{2,}', ' ', s)

    # Piper bug workaround: trailing punctuation causes noise
    sentences = re.split(r'(?<=[.!?…])\s+', s.strip())
    cleaned = []
    for sent in sentences:
        sent = sent.strip()
        if sent:
            sent = re.sub(r'[.!?…:;]+$', '', sent).strip()
            if sent:
                cleaned.append(sent)
    s = '\n'.join(cleaned) if cleaned else s

    s = s.lower()
    return s.strip()


class TTSSettings:
    """Piper synthesis parameters (backward compat)."""
    length_scale: float = 1.0
    noise_scale: float = 0.667
    noise_w_scale: float = 0.8
    sentence_silence: float = 0.2
    volume: float = 1.0
    speaker: int = 0

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)


def _load_tts_settings() -> TTSSettings:
    """Load TTS settings from config."""
    try:
        from core.config_writer import read_config
        cfg = read_config()
        # New format: voice.tts.primary.settings
        tts_cfg = cfg.get("voice", {}).get("tts", {}).get("primary", {}).get("settings", {})
        if tts_cfg:
            return TTSSettings(**tts_cfg)
        # Old format: voice.tts_settings
        old = cfg.get("voice", {}).get("tts_settings", {})
        if old:
            return TTSSettings(**old)
    except Exception:
        pass
    return TTSSettings()


# Backward compat: also export PIPER_BIN
PIPER_BIN = os.environ.get("PIPER_BIN", "piper")


class TTSEngine:
    """Piper TTS engine with dual voices (primary + fallback).

    Both voices are loaded once at startup and stay hot in memory.
    Switching between them is instant (Python variable selection).
    """

    def __init__(self) -> None:
        self._primary_voice: Any = None  # PiperVoice
        self._fallback_voice: Any = None  # PiperVoice
        self._primary_name: str = ""
        self._fallback_name: str = ""
        self._primary_lang: str = "uk"
        self._fallback_lang: str = "en"
        self._primary_sample_rate: int = 22050
        self._fallback_sample_rate: int = 22050
        self._lock = asyncio.Lock()
        self._loaded = False

    @property
    def voice(self) -> str:
        """Primary voice name (backward compat)."""
        return self._primary_name

    def load_voices(
        self,
        primary: str = DEFAULT_VOICE,
        fallback: str = DEFAULT_FALLBACK,
        primary_cuda: bool = False,
        fallback_cuda: bool = False,
    ) -> None:
        """Load both TTS voices.

        Strategy: try piper1-gpl Python API first, fall back to HTTP server.
        """
        self._primary_name = primary
        self._fallback_name = fallback
        self._primary_lang = primary.split("-")[0].split("_")[0]
        self._fallback_lang = fallback.split("-")[0].split("_")[0] if fallback else "en"

        # Try piper1-gpl Python API
        try:
            from piper import PiperVoice
            models_dir = Path(MODELS_DIR)

            primary_path = models_dir / f"{primary}.onnx"
            if primary_path.exists():
                self._primary_voice = PiperVoice.load(str(primary_path), use_cuda=primary_cuda)
                self._primary_sample_rate = self._primary_voice.config.sample_rate
                logger.info("TTS primary loaded (piper1-gpl): %s cuda=%s", primary, primary_cuda)

            fallback_path = models_dir / f"{fallback}.onnx"
            if fallback_path.exists():
                self._fallback_voice = PiperVoice.load(str(fallback_path), use_cuda=fallback_cuda)
                self._fallback_sample_rate = self._fallback_voice.config.sample_rate
                logger.info("TTS fallback loaded (piper1-gpl): %s", fallback)

            self._loaded = self._primary_voice is not None
            if self._loaded:
                self._use_http = False
                return
        except ImportError:
            logger.info("piper1-gpl not installed, trying HTTP server")
        except Exception as exc:
            logger.warning("piper1-gpl load failed: %s, trying HTTP server", exc)

        # Fallback: use Piper HTTP server (GPU-accelerated, runs on host)
        self._use_http = True
        gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
        try:
            import httpx
            resp = httpx.get(f"{gpu_url}/health", timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                loaded = data.get("loaded_voices", [])
                self._loaded = True
                logger.info("TTS using HTTP server at %s (voices: %s)", gpu_url, loaded)
            else:
                logger.warning("Piper HTTP server unhealthy: %s", resp.status_code)
        except Exception as exc:
            logger.warning("Piper HTTP server not available: %s", exc)

    def get_voice_for_lang(self, lang: str) -> tuple[Any, int]:
        """Select voice for a language segment.

        Returns (PiperVoice, sample_rate). Falls back to primary if no match.
        """
        if lang == "en" and self._fallback_voice:
            return self._fallback_voice, self._fallback_sample_rate
        if self._primary_voice:
            return self._primary_voice, self._primary_sample_rate
        if self._fallback_voice:
            return self._fallback_voice, self._fallback_sample_rate
        raise RuntimeError("No TTS voices loaded")

    async def synthesize(
        self, text: str, primary_lang: str = "uk",
        voice: str | None = None,
    ) -> bytes:
        """Synthesize text to WAV bytes using split_by_language.

        Mixed-language text is split into segments, each synthesized
        by the appropriate voice. WAV chunks are concatenated.

        Returns WAV bytes or empty bytes on failure.
        """
        from system_modules.voice_core.tts_preprocessor import split_by_language

        clean = sanitize_for_tts(text)
        if not clean:
            return b""

        # HTTP server mode
        if getattr(self, '_use_http', False):
            return await self._synthesize_http(clean, voice or self._primary_name)

        segments = split_by_language(clean, primary_lang)
        if not segments:
            return b""

        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self._synthesize_segments_sync, segments,
            )

    async def _synthesize_http(self, text: str, voice: str) -> bytes:
        """Synthesize via Piper HTTP server (fallback)."""
        try:
            import httpx
            gpu_url = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{gpu_url}/synthesize", json={
                    "text": text, "voice": voice,
                })
                if resp.status_code == 200 and resp.content:
                    return resp.content
        except Exception as exc:
            logger.error("HTTP TTS failed: %s", exc)
        return b""

    def _synthesize_segments_sync(self, segments: list) -> bytes:
        """Synthesize segments and concatenate WAV output."""
        from piper import SynthesisConfig

        all_pcm = bytearray()
        sample_rate = self._primary_sample_rate
        sample_width = 2  # 16-bit
        channels = 1

        for seg in segments:
            voice, sr = self.get_voice_for_lang(seg.lang)
            sample_rate = sr  # use last segment's rate (usually same)

            config = SynthesisConfig(
                volume=0.9,
            )

            for chunk in voice.synthesize(seg.text, syn_config=config):
                all_pcm.extend(chunk.audio_int16_bytes)

        if not all_pcm:
            return b""

        # Build WAV
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(bytes(all_pcm))

        return buf.getvalue()

    def synthesize_to_pcm(self, text: str, lang: str = "uk") -> tuple[bytes, int] | None:
        """Synthesize single-language text to raw PCM (for streaming playback).

        Returns (pcm_bytes, sample_rate) or None.
        """
        clean = sanitize_for_tts(text)
        if not clean:
            return None

        try:
            voice, sr = self.get_voice_for_lang(lang)
            from piper import SynthesisConfig

            pcm = bytearray()
            config = SynthesisConfig(volume=0.9)
            for chunk in voice.synthesize(clean, syn_config=config):
                pcm.extend(chunk.audio_int16_bytes)

            return bytes(pcm), sr if pcm else None
        except Exception as exc:
            logger.error("TTS PCM synthesis failed: %s", exc)
            return None

    def list_voices(self) -> list[dict]:
        """Discover installed voices by scanning the models directory."""
        models_path = Path(MODELS_DIR)
        if not models_path.is_dir():
            return []
        return [
            {"id": f.stem, "model": f.name, "available": True}
            for f in sorted(models_path.iterdir())
            if f.is_file() and f.suffix == ".onnx"
        ]


# ── Singleton ────────────────────────────────────────────────────────────

_engine: TTSEngine | None = None


def get_tts_engine() -> TTSEngine:
    """Get or create the global TTSEngine singleton."""
    global _engine
    if _engine is None:
        _engine = TTSEngine()
    return _engine


# Backward compat alias
def get_tts(voice: str = DEFAULT_VOICE) -> TTSEngine:
    """Backward-compatible: returns the global TTSEngine."""
    return get_tts_engine()
