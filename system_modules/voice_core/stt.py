"""
system_modules/voice_core/stt.py — Vosk STT wrapper

Supports:
  - Local transcription via vosk
  - Streaming chunks via async generator
  - Model selection: vosk-model-small-uk, vosk-model-small-ru, vosk-model-small-en-us, etc.
  - Language: uk (default), ru, en
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

MODELS_DIR = os.environ.get("VOSK_MODELS_DIR", "/var/lib/selena/models/vosk")
DEFAULT_MODEL = os.environ.get("VOSK_MODEL", "vosk-model-small-uk")


class STTEngine:
    """Vosk STT wrapper — offline speech recognition."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model_name = model
        self._model = None
        self._lock = asyncio.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        model_path = Path(MODELS_DIR) / self.model_name
        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)  # suppress verbose logs
            if model_path.is_dir():
                self._model = Model(str(model_path))
            else:
                # Vosk can download by model name
                self._model = Model(model_name=self.model_name)
            logger.info("Vosk model '%s' loaded", self.model_name)
        except ImportError:
            logger.warning("vosk not installed — STT unavailable")
        except Exception as e:
            logger.error("Failed to load Vosk model: %s", e)

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe PCM audio bytes to text.

        audio_bytes: raw 16-bit signed PCM, mono, sample_rate Hz
        """
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes, sample_rate)

    def _transcribe_sync(self, audio_bytes: bytes, sample_rate: int) -> str:
        self._load()
        if self._model is None:
            return ""

        try:
            from vosk import KaldiRecognizer
            rec = KaldiRecognizer(self._model, sample_rate)
            rec.AcceptWaveform(audio_bytes)
            result = json.loads(rec.FinalResult())
            return result.get("text", "").strip()
        except Exception as e:
            logger.error("STT transcription error: %s", e)
            return ""

    async def stream_transcribe(
        self, audio_stream: AsyncGenerator[bytes, None], chunk_sec: float = 3.0, sample_rate: int = 16000
    ) -> AsyncGenerator[str, None]:
        """Streaming transcription — yields partial transcription for each audio chunk."""
        chunk_size = int(sample_rate * chunk_sec * 2)  # 16-bit = 2 bytes per sample
        buffer = b""

        async for chunk in audio_stream:
            buffer += chunk
            while len(buffer) >= chunk_size:
                segment = buffer[:chunk_size]
                buffer = buffer[chunk_size:]
                text = await self.transcribe(segment, sample_rate)
                if text:
                    yield text

        if buffer:
            text = await self.transcribe(buffer, sample_rate)
            if text:
                yield text


# Default singleton
_stt: STTEngine | None = None


def get_stt(model: str = DEFAULT_MODEL) -> STTEngine:
    global _stt
    if _stt is None:
        _stt = STTEngine(model=model)
    return _stt
