"""
system_modules/voice_core/tts.py — Piper TTS client (HTTP-only, single voice)

Architecture:
  - Piper TTS runs NATIVELY on the host as piper-tts.service (port 5100).
  - The container has NO `piper-tts` package installed; this module only
    speaks to the native server over HTTP.
  - Single primary voice is preloaded by piper-tts.service at startup
    and stays hot in GPU memory.
  - This client just routes synthesis requests to the primary voice
    (or to an explicit voice override for UI test endpoints).

Live voice playback uses VoiceCoreModule._stream_speak() → _fetch_tts_raw()
directly (also HTTP). This TTSEngine is used by:
  1. Vosk warm-up at startup (single greeting phrase)
  2. POST /tts/test endpoint from UI Settings
  3. core/api/routes/voice_engines.py preview endpoints

Usage:
    engine = TTSEngine()
    engine.load_voices(primary="uk_UA-ukrainian_tts-medium")
    wav_bytes = await engine.synthesize("Привіт")
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

MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper")
DEFAULT_VOICE = os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium")
PIPER_GPU_URL = os.environ.get("PIPER_GPU_URL", "http://localhost:5100")


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

    Holds the primary voice name and its synthesis settings; all
    synthesis is delegated to the native server via POST /synthesize/raw.
    Single voice — no language splitting, no fallback voice.
    """

    def __init__(self) -> None:
        self._primary_name: str = ""
        self._primary_lang: str = "uk"
        self._primary_settings: dict = {}
        self._lock = asyncio.Lock()
        self._loaded = False

    @property
    def voice(self) -> str:
        """Primary voice name (backward compat)."""
        return self._primary_name

    def load_voices(self, primary: str = DEFAULT_VOICE, **_kwargs) -> None:
        """Register the primary voice and load its settings.

        Extra kwargs (legacy ``fallback``, ``primary_cuda``, etc.) are
        accepted and ignored for backward compatibility — the native
        piper-tts.service is the single source of truth for what is
        loaded and on which device.
        """
        self._primary_name = primary
        self._primary_lang = primary.split("-")[0].split("_")[0]

        self.reload_settings()

        # Health check — verify the server is up and primary voice is loaded
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
            else:
                logger.error("Piper HTTP server unhealthy: %s", resp.status_code)
                self._loaded = False
        except Exception as exc:
            logger.error("Piper HTTP server not reachable at %s: %s", PIPER_GPU_URL, exc)
            self._loaded = False

    def get_settings_for_lang(self, lang: str) -> dict:  # noqa: ARG002 — single voice
        """Get TTS synthesis settings (single voice — lang argument ignored)."""
        return self._primary_settings or {}

    def reload_settings(self) -> None:
        """Reload primary voice settings from config (called after settings change)."""
        try:
            from core.config_writer import read_config
            cfg = read_config()
            tts_cfg = cfg.get("voice", {}).get("tts", {})
            self._primary_settings = tts_cfg.get("primary", {}).get("settings", {})
        except Exception:
            pass

    def _select_voice(self, voice_override: str | None) -> tuple[str, dict]:
        """Pick voice name + settings dict — primary unless explicit override."""
        if voice_override and voice_override != self._primary_name:
            return voice_override, self._primary_settings or {}
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
        self, text: str, primary_lang: str = "uk",  # noqa: ARG002 — single voice
        voice: str | None = None,
    ) -> bytes:
        """Synthesize text to a WAV using the primary voice.

        Single-voice synthesis. The ``primary_lang`` argument is kept
        for backward compatibility but ignored — the primary voice is
        always used unless ``voice`` explicitly overrides it.

        Returns WAV bytes (empty on failure).
        """
        clean = sanitize_for_tts(text)
        if not clean:
            return b""

        chosen_voice, settings = self._select_voice(voice)
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
