"""
system_modules/voice_core/wake_word.py — openWakeWord integration

Listens on the microphone in a background loop and publishes
voice.wake_word events to the EventBus when wake word detected.

Default wake word: "hey_selena" (or configured via WAKE_WORD env)
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable

logger = logging.getLogger(__name__)

WAKE_WORD_MODEL = os.environ.get("WAKE_WORD_MODEL", "hey_selena")
WAKE_WORD_THRESHOLD = float(os.environ.get("WAKE_WORD_THRESHOLD", "0.5"))
SAMPLE_RATE = 16000
CHUNK_SIZE = 1280  # 80ms at 16kHz


class WakeWordDetector:
    """Background wake word detection using openWakeWord."""

    def __init__(
        self,
        model_name: str = WAKE_WORD_MODEL,
        threshold: float = WAKE_WORD_THRESHOLD,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self._oww = None
        self._running = False
        self._callbacks: list[Callable] = []
        self._privacy_mode = False
        self._task: asyncio.Task | None = None

    def on_wake_word(self, callback: Callable) -> None:
        """Register a callback to be called when wake word is detected."""
        self._callbacks.append(callback)

    def set_privacy_mode(self, enabled: bool) -> None:
        """Enable/disable privacy mode — stops listening when enabled."""
        self._privacy_mode = enabled
        logger.info("Wake word detector privacy mode: %s", "ON" if enabled else "OFF")

    def _load_model(self) -> bool:
        try:
            import openwakeword
            from openwakeword.model import Model
            self._oww = Model(wakeword_models=[self.model_name], inference_framework="onnx")
            logger.info("Wake word model '%s' loaded", self.model_name)
            return True
        except ImportError:
            logger.warning("openwakeword not installed — wake word detection unavailable")
            return False
        except Exception as e:
            logger.error("Failed to load wake word model: %s", e)
            return False

    async def start(self) -> None:
        """Start the wake word detection background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._listen_loop())
        logger.info("Wake word detection started")

    async def stop(self) -> None:
        """Stop wake word detection."""
        self._running = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        logger.info("Wake word detection stopped")

    async def _listen_loop(self) -> None:
        if not self._load_model():
            return

        loop = asyncio.get_event_loop()
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            stream = pa.open(
                rate=SAMPLE_RATE,
                channels=1,
                format=pyaudio.paInt16,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
        except Exception as e:
            logger.error("Cannot open audio stream for wake word: %s", e)
            return

        try:
            while self._running:
                if self._privacy_mode:
                    await asyncio.sleep(0.5)
                    continue

                data = await loop.run_in_executor(
                    None, stream.read, CHUNK_SIZE, False
                )
                import numpy as np
                audio_int16 = np.frombuffer(data, dtype=np.int16)
                audio_float = audio_int16.astype(np.float32) / 32768.0

                prediction = self._oww.predict(audio_float)
                for wake_word, score in prediction.items():
                    if score >= self.threshold:
                        logger.info(
                            "Wake word '%s' detected (score=%.2f)", wake_word, score
                        )
                        await self._trigger(wake_word, float(score))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Wake word listener error: %s", e)
        finally:
            try:
                stream.stop_stream()
                stream.close()
                pa.terminate()
            except Exception:
                pass

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


def get_wake_word_detector() -> WakeWordDetector:
    global _detector
    if _detector is None:
        _detector = WakeWordDetector()
    return _detector
