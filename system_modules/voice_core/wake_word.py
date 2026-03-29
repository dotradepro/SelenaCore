"""
system_modules/voice_core/wake_word.py — Vosk-based wake word detection

Listens on the microphone via parecord (PulseAudio) and uses Vosk with
a restricted grammar to detect the activation phrase in real-time.

Vosk works reliably on ARM/aarch64 (Jetson, Raspberry Pi) unlike
openWakeWord ONNX models which produce zero scores on these platforms.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from typing import Callable

logger = logging.getLogger(__name__)

WAKE_WORD_PHRASE = os.environ.get("WAKE_WORD_MODEL", "hey jarvis")
WAKE_WORD_THRESHOLD = float(os.environ.get("WAKE_WORD_THRESHOLD", "0.5"))
SAMPLE_RATE = 16000
CHUNK_SIZE = 4000  # 250ms at 16kHz


class WakeWordDetector:
    """Background wake word detection using Vosk grammar matching."""

    def __init__(
        self,
        model_name: str = WAKE_WORD_PHRASE,
        threshold: float = WAKE_WORD_THRESHOLD,
    ) -> None:
        # model_name is reused as the activation phrase
        self.model_name = model_name.replace("_", " ").lower().strip()
        self.threshold = threshold
        self._running = False
        self._callbacks: list[Callable] = []
        self._privacy_mode = False
        self._task: asyncio.Task | None = None

    def on_wake_word(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def set_privacy_mode(self, enabled: bool) -> None:
        self._privacy_mode = enabled
        logger.info("Wake word detector privacy mode: %s", "ON" if enabled else "OFF")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("Wake word detection started (phrase='%s')", self.model_name)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        logger.info("Wake word detection stopped")

    async def _listen_loop(self) -> None:
        loop = asyncio.get_event_loop()

        # Load Vosk model
        try:
            from system_modules.voice_core.stt import get_stt
            stt = get_stt()
            stt._load()
            if stt._model is None:
                logger.error("Wake word: Vosk model not available")
                return
        except Exception as e:
            logger.error("Wake word: cannot load Vosk: %s", e)
            return

        from vosk import KaldiRecognizer

        # Full recognizer (no grammar restriction) — works with any phrase
        # in the model's language. Grammar mode is too restrictive and fails
        # when the phrase contains words not in Vosk's vocabulary.
        rec = KaldiRecognizer(stt._model, SAMPLE_RATE)
        rec.SetWords(False)

        # Use configured input device, fallback to best available
        pa_device = None
        try:
            from core.config_writer import get_value
            pa_device = get_value("voice", "audio_force_input")
        except Exception:
            pass
        if not pa_device:
            try:
                from system_modules.voice_core.audio_manager import get_best_input
                best = get_best_input()
                if best:
                    pa_device = best.id
            except Exception:
                pass
        if pa_device:
            logger.info("Wake word: using input '%s'", pa_device)

        cmd = ["parecord", "--raw", "--format=s16le", "--rate=16000", "--channels=1"]
        if pa_device:
            cmd.append("--device=" + pa_device)

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            logger.info("Wake word: listening via parecord")
        except Exception as e:
            logger.error("Wake word: cannot start parecord: %s", e)
            return

        BYTES_PER_CHUNK = CHUNK_SIZE * 2

        try:
            while self._running:
                if self._privacy_mode:
                    await asyncio.sleep(0.5)
                    continue

                data = await loop.run_in_executor(
                    None, proc.stdout.read, BYTES_PER_CHUNK
                )
                if not data or len(data) < BYTES_PER_CHUNK:
                    logger.warning("Wake word: parecord stream ended")
                    break

                if rec.AcceptWaveform(data):
                    result = json.loads(rec.Result())
                    text = result.get("text", "").strip()
                    if text:
                        logger.info("Wake word heard: '%s'", text)
                        if self._matches_wake_phrase(text):
                            logger.info("Wake word MATCH: '%s'", text)
                            await self._trigger(self.model_name, 1.0)
                            rec = KaldiRecognizer(stt._model, SAMPLE_RATE)
                            rec.SetWords(False)
                else:
                    partial = json.loads(rec.PartialResult())
                    partial_text = partial.get("partial", "").strip()
                    if partial_text and self._matches_wake_phrase(partial_text):
                        logger.info("Wake word MATCH (partial): '%s'", partial_text)
                        await self._trigger(self.model_name, 1.0)
                        rec = KaldiRecognizer(stt._model, SAMPLE_RATE)
                        rec.SetWords(False)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Wake word listener error: %s", e)
        finally:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass

    def _matches_wake_phrase(self, text: str) -> bool:
        """Check if recognized text contains the wake phrase.

        Uses fuzzy matching: each wake word must appear as a prefix of
        some word in the text (min 3 chars). This handles Vosk recognizing
        'галушко' when user says 'галоша', or 'селен' for 'селена'.
        """
        t = text.lower().strip()
        phrase = self.model_name

        # Exact substring match
        if phrase in t:
            return True

        # Fuzzy: first 3 chars of each wake word must match start of some text word
        text_words = t.split()
        for pw in phrase.split():
            prefix = pw[:3]
            if not any(tw.startswith(prefix) for tw in text_words):
                return False
        return True

    async def _trigger(self, wake_word: str, score: float) -> None:
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(wake_word, score)
                else:
                    callback(wake_word, score)
            except Exception as e:
                logger.error("Wake word callback error: %s", e)


_detector: WakeWordDetector | None = None


def get_wake_word_detector(
    phrase: str | None = None,
    threshold: float | None = None,
) -> WakeWordDetector:
    global _detector
    if _detector is None:
        _detector = WakeWordDetector(
            model_name=phrase or WAKE_WORD_PHRASE,
            threshold=threshold if threshold is not None else WAKE_WORD_THRESHOLD,
        )
    return _detector
