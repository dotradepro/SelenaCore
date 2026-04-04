"""core.stt.whisper_cpp — whisper.cpp HTTP server provider.

Connects to a running whisper-server instance (whisper.cpp built with CUDA).
Best for Jetson Orin Nano and Linux with CUDA.

Server must be started separately:
    ./whisper-server --model ggml-small.bin --host 0.0.0.0 --port 9000 --language auto
"""
from __future__ import annotations

import io
import logging
import struct
import wave

import httpx

from core.stt.base import STTProvider, STTResult

logger = logging.getLogger(__name__)


class WhisperCppProvider(STTProvider):
    """STT via whisper.cpp HTTP server."""

    def __init__(self, host: str = "http://localhost:9000", language: str | None = None) -> None:
        self._host = host.rstrip("/")
        self._language = language
        self._client = httpx.AsyncClient(timeout=30.0)

    def _load_stt_settings(self) -> dict[str, str]:
        """Load STT settings from config for each request."""
        form_data: dict[str, str] = {
            "response_format": "verbose_json",
        }
        if self._language:
            form_data["language"] = self._language
        try:
            from core.config_writer import read_config
            s = read_config().get("stt", {}).get("settings", {})
            if s:
                for key in ("beam_size", "temperature", "no_speech_threshold",
                             "vad_filter", "vad_min_silence_ms", "vad_speech_pad_ms",
                             "vad_threshold", "condition_on_previous_text"):
                    if key in s:
                        form_data[key] = str(s[key])
                lang = s.get("language")
                if lang and lang != "auto":
                    form_data["language"] = lang
        except Exception:
            pass
        return form_data

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        """Send PCM audio to whisper-server, get text + detected language."""
        wav_bytes = _pcm_to_wav(audio_bytes, sample_rate)
        form_data = self._load_stt_settings()

        try:
            resp = await self._client.post(
                f"{self._host}/inference",
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data=form_data,
            )
            resp.raise_for_status()
            data = resp.json()

            text = data.get("text", "").strip()
            # Filter Whisper artifacts
            if text in ("[BLANK_AUDIO]", "(BLANK_AUDIO)", "[silence]"):
                text = ""
            # verbose_json returns detected_language / language fields
            lang = data.get("detected_language") or data.get("language") or "en"
            # whisper.cpp returns full language name (e.g. "english", "ukrainian")
            if len(lang) > 3:
                lang = _lang_name_to_code(lang)

            return STTResult(text=text, lang=lang, confidence=0.9)

        except httpx.HTTPStatusError as e:
            logger.error("whisper.cpp server error: %s %s", e.response.status_code, e.response.text[:200])
            return STTResult()
        except httpx.ConnectError:
            logger.error("whisper.cpp server not reachable at %s", self._host)
            return STTResult()
        except Exception as e:
            logger.error("whisper.cpp transcription failed: %s: %s", type(e).__name__, e)
            return STTResult()

    async def close(self) -> None:
        await self._client.aclose()


def _pcm_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Convert raw PCM (16-bit signed, mono) to WAV format in memory."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


_LANG_NAMES: dict[str, str] = {
    "ukrainian": "uk", "english": "en", "german": "de",
    "french": "fr", "spanish": "es", "polish": "pl",
    "italian": "it", "portuguese": "pt", "dutch": "nl",
    "czech": "cs", "japanese": "ja", "chinese": "zh",
    "korean": "ko", "russian": "ru", "turkish": "tr",
    "arabic": "ar", "hindi": "hi", "swedish": "sv",
}


def _lang_name_to_code(name: str) -> str:
    """Convert full language name to ISO 639-1 code."""
    return _LANG_NAMES.get(name.lower().strip(), name[:2].lower())
