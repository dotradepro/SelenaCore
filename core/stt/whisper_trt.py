"""core.stt.whisper_trt — WhisperTRT provider (Jetson native TensorRT).

Best for Jetson Orin Nano/Xavier with CUDA 12.6+.
Uses NVIDIA TensorRT — ~3x faster than PyTorch, 60% less memory.

Prerequisites (native, no Docker):
    sudo apt install -y tensorrt-libs libnvinfer-dev
    pip3 install torch2trt whisper-trt
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import Any

import numpy as np

from core.stt.base import STTProvider, STTResult

logger = logging.getLogger(__name__)


class WhisperTRTProvider(STTProvider):
    """STT via WhisperTRT (local TensorRT-optimized, Jetson native)."""

    def __init__(self, model: str = "small") -> None:
        self._model_name = model
        self._model: Any = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        from whisper_trt import load_trt_model

        logger.info(
            "Loading WhisperTRT model=%s (first load builds TensorRT engine ~30-60s)",
            self._model_name,
        )
        self._model = load_trt_model(self._model_name)
        logger.info("WhisperTRT model loaded")

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        self._ensure_model()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes, sample_rate)

    def _transcribe_sync(self, audio_bytes: bytes, sample_rate: int) -> STTResult:
        try:
            # WhisperTRT accepts numpy float32 array (same as openai-whisper)
            audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

            result = self._model.transcribe(audio)

            text = result.get("text", "").strip()

            # Filter artifacts
            if text in ("[BLANK_AUDIO]", "(BLANK_AUDIO)", "[silence]", ""):
                return STTResult()

            lang = result.get("language", "en")
            if len(lang) > 3:
                lang = _lang_name_to_code(lang)

            return STTResult(text=text, lang=lang, confidence=0.95)

        except Exception as e:
            logger.error("WhisperTRT transcription error: %s", e)
            return STTResult()

    async def close(self) -> None:
        self._model = None


def _lang_name_to_code(name: str) -> str:
    from core.lang_utils import lang_name_to_code
    return lang_name_to_code(name)
