"""core.stt.base — STT provider interface and result dataclass."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class STTResult:
    """Result of speech-to-text transcription."""

    text: str = ""
    lang: str = "en"         # ISO 639-1 code detected by the STT engine
    confidence: float = 0.0  # 0.0-1.0, provider-dependent


class STTProvider(ABC):
    """Abstract STT provider — all backends implement this interface."""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        """Transcribe raw PCM audio (16-bit signed, mono) to text + language.

        Args:
            audio_bytes: raw PCM audio data
            sample_rate: sample rate in Hz (default 16000)

        Returns:
            STTResult with text, detected language code, and confidence.
        """

    async def close(self) -> None:
        """Release resources. Called on module stop."""
