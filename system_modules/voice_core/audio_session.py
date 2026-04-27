"""Per-satellite audio session state.

Each ESP32 satellite has its own `SatelliteAudioSession` with a dedicated
Vosk `KaldiRecognizer` fed from `satellite.audio_chunk` events. The wake
word was already detected on-device, so the session starts directly in
LISTENING — we skip the hub-side IDLE grammar pass for satellites.

The local microphone does NOT use this class; it keeps its existing state
machine in `VoiceCoreModule._audio_loop`. Satellites run on a parallel
code path so regressions are impossible.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SatelliteAudioSession:
    session_id: str
    device_id: str
    location: str | None
    recognizer: Any  # vosk.KaldiRecognizer, typed Any so tests without vosk load
    started_at: float = field(default_factory=time.monotonic)
    last_chunk_at: float = field(default_factory=time.monotonic)
    finalized: bool = False
    # Clarification state — when the router asks "which room?" we keep the
    # satellite's mic open and attach the pending context here. The next
    # AUDIO_END for this session routes through route_clarification instead
    # of the full route pipeline.
    pending_clarification: dict | None = None
    clarification_deadline: float = 0.0

    def feed(self, pcm_data: bytes) -> tuple[str | None, str | None]:
        """Feed a PCM chunk. Returns (partial, final).

        `final` is set when Vosk's endpointer decides the utterance is
        complete mid-stream; we don't usually rely on it for satellites
        because the ESP32 sends an explicit AUDIO_END frame.
        """
        self.last_chunk_at = time.monotonic()
        partial: str | None = None
        final: str | None = None
        try:
            if self.recognizer.AcceptWaveform(pcm_data):
                import json
                result = json.loads(self.recognizer.Result() or "{}")
                final = (result.get("text") or "").strip() or None
            else:
                import json
                p = json.loads(self.recognizer.PartialResult() or "{}")
                partial = (p.get("partial") or "").strip() or None
        except Exception:
            logger.exception("Satellite %s: Vosk feed failed", self.session_id)
        return partial, final

    def finalize(self) -> str:
        """Force-close the utterance and return the final transcript."""
        if self.finalized:
            return ""
        self.finalized = True
        try:
            import json
            result = json.loads(self.recognizer.FinalResult() or "{}")
            return (result.get("text") or "").strip()
        except Exception:
            logger.exception("Satellite %s: Vosk finalize failed", self.session_id)
            return ""

    def reset_for_clarification(self) -> None:
        """Prepare the session to receive a clarification reply on the same
        recognizer. Resets Vosk's internal state so a fresh utterance can
        be recognized without allocating a new KaldiRecognizer.
        """
        try:
            if hasattr(self.recognizer, "Reset"):
                self.recognizer.Reset()
        except Exception:
            logger.debug("Recognizer reset failed — subsequent transcripts may drift")
        self.finalized = False
        self.last_chunk_at = time.monotonic()


def create_session_recognizer(stt_provider: Any, sample_rate: int = 16000) -> Any | None:
    """Build a fresh KaldiRecognizer sharing the provider's already-loaded model.

    Returns None if vosk isn't installed or the model isn't ready.
    """
    model = getattr(stt_provider, "_model", None)
    if model is None:
        return None
    try:
        import vosk
        return vosk.KaldiRecognizer(model, sample_rate)
    except Exception:
        logger.exception("Failed to create per-session KaldiRecognizer")
        return None
