"""core.stt.openai_stt — OpenAI Whisper API provider (cloud).

For any hardware with internet. Uses OpenAI whisper-1 model.
Requires OPENAI_API_KEY in env or config.
"""
from __future__ import annotations

import io
import logging
import wave

import httpx

from core.stt.base import STTProvider, STTResult

logger = logging.getLogger(__name__)


class OpenAIWhisperProvider(STTProvider):
    """STT via OpenAI Whisper API."""

    def __init__(self, api_key: str, model: str = "whisper-1") -> None:
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_bytes)
        wav_bytes = wav_buf.getvalue()

        try:
            resp = await self._client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={
                    "model": self._model,
                    "response_format": "verbose_json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            text = data.get("text", "").strip()
            lang = data.get("language", "en")
            # OpenAI returns full name like "ukrainian"
            if len(lang) > 3:
                from core.stt.whisper_cpp import _lang_name_to_code
                lang = _lang_name_to_code(lang)

            return STTResult(text=text, lang=lang, confidence=0.95)

        except httpx.HTTPStatusError as e:
            logger.error("OpenAI Whisper API error: %s", e.response.status_code)
            return STTResult()
        except Exception as e:
            logger.error("OpenAI Whisper transcription failed: %s", e)
            return STTResult()

    async def close(self) -> None:
        await self._client.aclose()
