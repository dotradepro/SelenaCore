"""core.stt.faster_whisper — faster-whisper provider (Python, CPU/CUDA).

Best for Raspberry Pi (CPU) and Linux desktops (CUDA).
Uses CTranslate2 under the hood — efficient int8/float16 inference.

Install: pip install faster-whisper
"""
from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import Any

from core.stt.base import STTProvider, STTResult

logger = logging.getLogger(__name__)


class FasterWhisperProvider(STTProvider):
    """STT via faster-whisper (local, in-process)."""

    def __init__(
        self,
        model: str = "small",
        device: str = "auto",
        compute_type: str = "auto",
    ) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._model: Any = None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        from faster_whisper import WhisperModel

        device = self._device
        compute_type = self._compute_type

        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"

        logger.info(
            "Loading faster-whisper model=%s device=%s compute=%s",
            self._model_name, device, compute_type,
        )
        self._model = WhisperModel(
            self._model_name, device=device, compute_type=compute_type
        )
        logger.info("faster-whisper model loaded")

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        self._ensure_model()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._transcribe_sync, audio_bytes, sample_rate)

    def _transcribe_sync(self, audio_bytes: bytes, sample_rate: int) -> STTResult:
        try:
            # faster-whisper needs a file-like WAV
            wav_buf = io.BytesIO()
            with wave.open(wav_buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(audio_bytes)
            wav_buf.seek(0)

            segments, info = self._model.transcribe(
                wav_buf,
                language=None,  # auto-detect
                beam_size=5,
                best_of=1,
                temperature=0.0,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=400,
                    threshold=0.5,
                ),
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )

            text_parts = [seg.text.strip() for seg in segments]
            text = " ".join(text_parts).strip()

            # Filter Whisper hallucination artifacts
            if text in ("[BLANK_AUDIO]", "(BLANK_AUDIO)", "[silence]", ""):
                return STTResult()

            lang = info.language if info.language else "en"
            confidence = info.language_probability if hasattr(info, "language_probability") else 0.9

            return STTResult(text=text, lang=lang, confidence=confidence)

        except Exception as e:
            logger.error("faster-whisper transcription error: %s", e)
            return STTResult()

    async def close(self) -> None:
        self._model = None
