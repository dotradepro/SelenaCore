"""core.stt.vosk_provider — Vosk STT provider (offline, streaming).

Lightweight offline speech recognition via Vosk (Kaldi-based).
Supports two recognizer modes:
  - IDLE: grammar-restricted (wake word phrases + [unk])
  - LISTENING: full vocabulary recognition

Install: pip install vosk
Models: https://alphacephei.com/vosk/models
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from core.stt.base import STTProvider, STTResult

logger = logging.getLogger(__name__)

# Default chunk size for feeding audio to Vosk (bytes)
_CHUNK_BYTES = 4000


class VoskProvider(STTProvider):
    """STT via Vosk — offline, streaming, grammar-aware.

    Two operating modes via separate KaldiRecognizer instances:
    - IDLE mode: grammar-restricted recognizer (only wake word phrases)
    - LISTENING mode: full-vocabulary recognizer (command recognition)

    One VoskModel is loaded once and shared by both recognizers.
    """

    def __init__(self, model_path: str, lang: str = "en", sample_rate: int = 16000) -> None:
        self._model_path = model_path
        self._lang = lang
        self._sample_rate = sample_rate
        self._model: Any = None
        self._idle_rec: Any = None
        self._listen_rec: Any = None
        self._grammar_phrases: list[str] = []
        self._loading = False
        self._ready = False

    # ── Model lifecycle ──────────────────────────────────────────────────

    def load_model(self) -> None:
        """Load Vosk model from disk. Call once on startup or model switch."""
        import vosk
        vosk.SetLogLevel(-1)  # suppress Kaldi logs

        if not os.path.isdir(self._model_path):
            raise FileNotFoundError(f"Vosk model directory not found: {self._model_path}")

        self._loading = True
        self._ready = False
        logger.info("Loading Vosk model from %s (lang=%s)", self._model_path, self._lang)

        try:
            self._model = vosk.Model(self._model_path)
            self._ready = True
            logger.info("Vosk model loaded successfully")
        except Exception as e:
            logger.error("Failed to load Vosk model: %s", e)
            raise
        finally:
            self._loading = False

    def _ensure_model(self) -> bool:
        """Ensure model is loaded. Returns False if unavailable."""
        if self._model is not None:
            return True
        try:
            self.load_model()
            return self._model is not None
        except Exception:
            return False

    async def reload_model(self, model_path: str, lang: str) -> None:
        """Hot-swap model: unload current, load new one.

        Called when user selects a different model via UI.
        During reload, transcribe() returns empty results.
        """
        self._ready = False
        self._loading = True
        self._idle_rec = None
        self._listen_rec = None

        # Unload old model
        self._model = None

        # Load new model in executor (blocking I/O)
        self._model_path = model_path
        self._lang = lang
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self.load_model)
            # Restore grammar if we had one
            if self._grammar_phrases:
                self.set_grammar(self._grammar_phrases)
        except Exception as e:
            logger.error("Model reload failed: %s", e)
            self._loading = False

    @property
    def is_ready(self) -> bool:
        return self._ready and self._model is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def lang(self) -> str:
        return self._lang

    @property
    def model_path(self) -> str:
        return self._model_path

    # ── IDLE mode (grammar-restricted) ───────────────────────────────────

    def set_grammar(self, phrases: list[str]) -> None:
        """Set wake word grammar for IDLE mode recognizer.

        Args:
            phrases: list of wake word phrases (e.g. ["selena", "селена", "hey selena"])
                     [unk] is added automatically for non-matching audio.
        """
        import vosk

        if not self._ensure_model():
            return

        self._grammar_phrases = phrases
        grammar = json.dumps(phrases + ["[unk]"], ensure_ascii=False)
        self._idle_rec = vosk.KaldiRecognizer(self._model, self._sample_rate, grammar)
        logger.info("Vosk IDLE grammar set: %s", phrases)

    def reset_idle(self) -> None:
        """Reset IDLE recognizer state (after wake word detected)."""
        if self._idle_rec is not None:
            self._idle_rec.Reset()

    def feed_idle(self, chunk: bytes) -> tuple[str | None, str | None]:
        """Feed audio chunk to IDLE (grammar) recognizer.

        Returns:
            (partial_text, final_text) — partial is updated per-chunk,
            final is set when Vosk detects end of utterance.
            Both can be None.
        """
        if self._idle_rec is None:
            return None, None

        if self._idle_rec.AcceptWaveform(chunk):
            result = json.loads(self._idle_rec.Result())
            text = result.get("text", "").strip()
            # Filter [unk] results
            if text and text != "[unk]":
                return None, text
            return None, None

        partial = json.loads(self._idle_rec.PartialResult())
        partial_text = partial.get("partial", "").strip()
        if partial_text and partial_text != "[unk]":
            return partial_text, None
        return None, None

    # ── LISTENING mode (full vocabulary) ─────────────────────────────────

    def create_listening_recognizer(self) -> None:
        """Create full-vocabulary recognizer for LISTENING mode."""
        import vosk

        if not self._ensure_model():
            return

        self._listen_rec = vosk.KaldiRecognizer(self._model, self._sample_rate)
        self._listen_rec.SetWords(True)
        self._listen_rec.SetPartialWords(True)

    def reset_listening(self) -> None:
        """Reset LISTENING recognizer for new utterance."""
        if self._listen_rec is not None:
            self._listen_rec.Reset()

    def feed_listening(self, chunk: bytes) -> tuple[str | None, str | None]:
        """Feed audio chunk to LISTENING (full) recognizer.

        Returns:
            (partial_text, final_text) — partial updates while speaking,
            final when Vosk detects end of utterance.
        """
        if self._listen_rec is None:
            self.create_listening_recognizer()
            if self._listen_rec is None:
                return None, None

        if self._listen_rec.AcceptWaveform(chunk):
            result = json.loads(self._listen_rec.Result())
            return None, result.get("text", "").strip()

        partial = json.loads(self._listen_rec.PartialResult())
        return partial.get("partial", "").strip() or None, None

    def finalize_listening(self) -> str:
        """Get final result from LISTENING recognizer (call after silence timeout)."""
        if self._listen_rec is None:
            return ""
        result = json.loads(self._listen_rec.FinalResult())
        return result.get("text", "").strip()

    # ── Batch transcribe (STTProvider interface) ─────────────────────────

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> STTResult:
        """Batch transcription — feeds all audio at once.

        Used by:
        - WebRTC stream endpoint
        - STT test endpoint
        - Any caller expecting the standard STTProvider interface
        """
        if not self._ensure_model():
            return STTResult()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._transcribe_sync, audio_bytes, sample_rate
        )

    def _transcribe_sync(self, audio_bytes: bytes, sample_rate: int) -> STTResult:
        """Synchronous batch transcription."""
        import vosk

        try:
            rec = vosk.KaldiRecognizer(self._model, sample_rate)
            rec.SetWords(True)

            # Feed audio in chunks
            for i in range(0, len(audio_bytes), _CHUNK_BYTES):
                rec.AcceptWaveform(audio_bytes[i:i + _CHUNK_BYTES])

            result = json.loads(rec.FinalResult())
            text = result.get("text", "").strip()

            if not text:
                return STTResult()

            return STTResult(text=text, lang=self._lang, confidence=1.0)

        except Exception as e:
            logger.error("Vosk batch transcription error: %s", e)
            return STTResult()

    # ── Warm-up ──────────────────────────────────────────────────────────

    def warmup(self, audio_bytes: bytes | None = None) -> None:
        """JIT warm-up: feed a short audio sample to initialize internal structures.

        Args:
            audio_bytes: PCM 16-bit mono audio at self._sample_rate.
                         If None, generates 1 second of silence.
        """
        if not self._ensure_model():
            return

        import vosk

        if audio_bytes is None:
            # 1 second of silence (16kHz, 16-bit, mono)
            audio_bytes = b"\x00\x00" * self._sample_rate

        rec = vosk.KaldiRecognizer(self._model, self._sample_rate)
        for i in range(0, len(audio_bytes), _CHUNK_BYTES):
            rec.AcceptWaveform(audio_bytes[i:i + _CHUNK_BYTES])
        rec.FinalResult()
        logger.info("Vosk model warmed up")

    # ── Cleanup ──────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Release model and recognizers."""
        self._idle_rec = None
        self._listen_rec = None
        self._model = None
        self._ready = False
        logger.info("Vosk provider closed")

    # ── Status ───────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """Return provider status for API/UI."""
        return {
            "provider": "vosk",
            "model_path": self._model_path,
            "lang": self._lang,
            "ready": self._ready,
            "loading": self._loading,
            "grammar_phrases": self._grammar_phrases,
            "sample_rate": self._sample_rate,
        }
