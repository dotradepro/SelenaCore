"""
system_modules/voice_core/tts.py — Piper TTS client (HTTP-only)

Architecture:
  - Piper TTS runs NATIVELY on the host as piper-tts.service (port 5100).
  - The container has NO `piper-tts` package installed; this module only
    speaks to the native server over HTTP.
  - Both voices (primary + fallback) are preloaded by piper-tts.service
    at startup and stay hot in GPU memory.
  - This client just routes synthesis requests with the right voice + settings.

Live voice playback uses VoiceCoreModule._stream_speak() → _fetch_tts_raw()
directly (also HTTP). This TTSEngine is used by:
  1. Vosk warm-up at startup (single greeting phrase)
  2. POST /tts/test endpoint from UI Settings
  3. core/api/routes/voice_engines.py preview endpoints

Usage:
    engine = TTSEngine()
    engine.load_voices(primary="uk_UA-ukrainian_tts-medium",
                       fallback="en_US-amy-low")
    wav_bytes = await engine.synthesize("Привіт", primary_lang="uk")
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import wave
from pathlib import Path

logger = logging.getLogger(__name__)

def _cfg_get(path: str, default: str) -> str:
    """Lookup config-first with env-var override fallback."""
    try:
        from core.config_writer import get_nested
        val = get_nested(path)
        if val:
            return str(val)
    except Exception:
        pass
    return default


MODELS_DIR = os.environ.get(
    "PIPER_MODELS_DIR",
    _cfg_get("voice.tts.models_dir", "/var/lib/selena/models/piper"),
)
DEFAULT_VOICE = os.environ.get(
    "PIPER_VOICE",
    _cfg_get("voice.tts.primary.voice", "uk_UA-ukrainian_tts-medium"),
)
DEFAULT_FALLBACK = os.environ.get(
    "PIPER_FALLBACK_VOICE",
    _cfg_get("voice.tts.fallback.voice", "en_US-amy-low"),
)
PIPER_GPU_URL = os.environ.get(
    "PIPER_GPU_URL",
    _cfg_get("voice.tts.server_url", "http://localhost:5100"),
)


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
    """Piper synthesis parameters."""
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
    """Load TTS settings from config (primary voice settings)."""
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


class TTSEngine:
    """Piper TTS engine — HTTP client to native piper-tts.service.

    Holds voice names and per-voice settings; all synthesis is delegated
    to the native server via POST /synthesize/raw.
    """

    def __init__(self) -> None:
        self._primary_name: str = ""
        self._fallback_name: str = ""
        self._primary_lang: str = "uk"
        self._fallback_lang: str = "en"
        self._primary_settings: dict = {}
        self._fallback_settings: dict = {}
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
        primary_cuda: bool = False,  # noqa: ARG002 — server decides
        fallback_cuda: bool = False,  # noqa: ARG002 — server decides
    ) -> None:
        """Register voice names and per-language settings.

        Note: cuda flags are ignored — the native piper-tts.service decides
        the device for all loaded models. Models are preloaded at server
        startup (see scripts/piper-tts.service).
        """
        self._primary_name = primary
        self._fallback_name = fallback
        self._primary_lang = primary.split("-")[0].split("_")[0]
        self._fallback_lang = fallback.split("-")[0].split("_")[0] if fallback else "en"

        self.reload_settings()

        # Health check — verify the server is up and the requested voices
        # are actually loaded (preloaded by --preload in piper-tts.service).
        try:
            import httpx
            resp = httpx.get(f"{PIPER_GPU_URL}/health", timeout=3)
            if resp.status_code == 200:
                data = resp.json()
                loaded = data.get("loaded_voices", [])
                device = data.get("device", "?")
                self._loaded = bool(self._primary_name)
                logger.info(
                    "TTS using HTTP server at %s (device=%s, loaded_voices=%s)",
                    PIPER_GPU_URL, device, loaded,
                )
                if self._primary_name and self._primary_name not in loaded:
                    logger.warning(
                        "TTS primary voice %s not preloaded — first request will be slow",
                        self._primary_name,
                    )
                if self._fallback_name and self._fallback_name not in loaded:
                    logger.warning(
                        "TTS fallback voice %s not preloaded — first request will be slow",
                        self._fallback_name,
                    )
            else:
                logger.error("Piper HTTP server unhealthy: %s", resp.status_code)
                self._loaded = False
        except Exception as exc:
            logger.error("Piper HTTP server not reachable at %s: %s", PIPER_GPU_URL, exc)
            self._loaded = False

    def get_settings_for_lang(self, lang: str) -> dict:
        """Get TTS synthesis settings for a language."""
        if lang == "en" and self._fallback_settings:
            return self._fallback_settings
        return self._primary_settings or {}

    def reload_settings(self) -> None:
        """Reload per-voice settings from config (called after settings change)."""
        try:
            from core.config_writer import read_config
            cfg = read_config()
            tts_cfg = cfg.get("voice", {}).get("tts", {})
            self._primary_settings = tts_cfg.get("primary", {}).get("settings", {})
            self._fallback_settings = tts_cfg.get("fallback", {}).get("settings", {})
        except Exception:
            pass

    def _select_voice(self, lang: str, voice_override: str | None) -> tuple[str, dict]:
        """Pick voice name + settings dict for a language (or use override)."""
        if voice_override:
            # Explicit voice — use primary settings if it matches primary,
            # fallback settings if it matches fallback, else primary as default.
            if voice_override == self._fallback_name:
                return voice_override, self._fallback_settings or {}
            return voice_override, self._primary_settings or {}
        if lang == "en" and self._fallback_name:
            return self._fallback_name, self._fallback_settings or {}
        return self._primary_name, self._primary_settings or {}

    async def _synthesize_raw_http(
        self, text: str, voice: str, settings: dict,
    ) -> tuple[bytes, int]:
        """POST /synthesize/raw → (pcm_bytes, sample_rate). Empty on failure."""
        import httpx

        payload: dict = {"text": text, "voice": voice}
        for k in (
            "length_scale", "noise_scale", "noise_w_scale",
            "sentence_silence", "speaker", "volume",
        ):
            if k in settings and settings[k] is not None:
                payload[k] = settings[k]
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{PIPER_GPU_URL}/synthesize/raw", json=payload,
                )
                if resp.status_code == 200 and resp.content:
                    sr = int(resp.headers.get("X-Audio-Rate", "22050"))
                    return resp.content, sr
                logger.warning(
                    "Piper /synthesize/raw returned %s for voice=%s",
                    resp.status_code, voice,
                )
        except Exception as exc:
            logger.error("HTTP TTS failed (voice=%s): %s", voice, exc)
        return b"", 22050

    async def synthesize(
        self, text: str, primary_lang: str = "uk",
        voice: str | None = None,
    ) -> bytes:
        """Synthesize text to a single-voice WAV.

        Multi-language splitting is handled upstream in
        VoiceCoreModule._stream_speak (which calls /synthesize/raw per
        utterance directly). This method synthesizes a single phrase with
        a single voice, used for warm-up and UI test endpoints.

        Returns WAV bytes (empty on failure).
        """
        clean = sanitize_for_tts(text)
        if not clean:
            return b""

        chosen_voice, settings = self._select_voice(primary_lang, voice)
        if not chosen_voice:
            logger.error("No TTS voice available")
            return b""

        async with self._lock:
            pcm, sample_rate = await self._synthesize_raw_http(
                clean, chosen_voice, settings,
            )
        if not pcm:
            return b""

        # Wrap raw PCM s16le mono in a WAV container
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        return buf.getvalue()

    def list_voices(self) -> list[dict]:
        """Discover installed voices by scanning the models directory.

        The host's piper models directory is volume-mounted into the
        container as MODELS_DIR (see docker-compose.yml).
        """
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
def get_tts(voice: str = DEFAULT_VOICE) -> TTSEngine:  # noqa: ARG001
    """Backward-compatible: returns the global TTSEngine."""
    return get_tts_engine()
