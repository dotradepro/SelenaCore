"""
system_modules/voice_core/stt.py — Whisper.cpp STT wrapper

Supports:
  - Local transcription via pywhispercpp
  - Streaming chunks via async generator
  - Model selection: tiny, base, small, medium, large
  - Language: ru (default), en, auto
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import AsyncGenerator

logger = logging.getLogger(__name__)

MODELS_DIR = os.environ.get("WHISPER_MODELS_DIR", "/var/lib/selena/models/whisper")
DEFAULT_MODEL = os.environ.get("WHISPER_MODEL", "base")
DEFAULT_LANG = os.environ.get("WHISPER_LANGUAGE", "ru")


class STTEngine:
    """Whisper.cpp STT wrapper using pywhispercpp."""

    def __init__(self, model: str = DEFAULT_MODEL, language: str = DEFAULT_LANG) -> None:
        self.model_name = model
        self.language = language
        self._whisper = None
        self._lock = asyncio.Lock()

    def _load(self) -> None:
        if self._whisper is not None:
            return
        model_path = Path(MODELS_DIR) / f"ggml-{self.model_name}.bin"
        try:
            from pywhispercpp.model import Model
            self._whisper = Model(
                str(model_path) if model_path.exists() else self.model_name,
                n_threads=os.cpu_count() or 4,
            )
            logger.info("Whisper model '%s' loaded", self.model_name)
        except ImportError:
            logger.warning("pywhispercpp not installed — STT unavailable")
        except Exception as e:
            logger.error("Failed to load Whisper model: %s", e)

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Transcribe PCM audio bytes to text.

        audio_bytes: raw 16-bit signed PCM, mono, sample_rate Hz
        """
        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes, sample_rate)

    def _transcribe_sync(self, audio_bytes: bytes, sample_rate: int) -> str:
        self._load()
        if self._whisper is None:
            return ""

        # Write to temp WAV file for whisper
        import wave
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)

            segments = self._whisper.transcribe(
                tmp_path,
                language=self.language if self.language != "auto" else None,
            )
            return " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:
            logger.error("STT transcription error: %s", e)
            return ""
        finally:
            Path(tmp_path).unlink(missing_ok=True)

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


def get_stt(model: str = DEFAULT_MODEL, language: str = DEFAULT_LANG) -> STTEngine:
    global _stt
    if _stt is None:
        _stt = STTEngine(model=model, language=language)
    return _stt
