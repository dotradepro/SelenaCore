"""
system_modules/voice_core/tts.py — Piper TTS wrapper

Supports:
  - Local text-to-speech via piper-tts
  - Multiple voices / languages
  - Returns WAV audio bytes
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper")
DEFAULT_VOICE = os.environ.get("PIPER_VOICE", "ru_RU-irina-medium")
PIPER_BIN = os.environ.get("PIPER_BIN", "piper")

# Available voices and their model files
AVAILABLE_VOICES: dict[str, str] = {
    "ru_RU-irina-medium": "ru_RU-irina-medium.onnx",
    "ru_RU-ruslan-medium": "ru_RU-ruslan-medium.onnx",
    "en_US-amy-medium": "en_US-amy-medium.onnx",
    "en_US-ryan-high": "en_US-ryan-high.onnx",
}


class TTSEngine:
    """Piper TTS wrapper — converts text to WAV bytes."""

    def __init__(self, voice: str = DEFAULT_VOICE) -> None:
        self.voice = voice
        self._lock = asyncio.Lock()

    def _model_path(self, voice: str) -> str:
        model_file = AVAILABLE_VOICES.get(voice, f"{voice}.onnx")
        return str(Path(MODELS_DIR) / model_file)

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Convert text to WAV audio bytes using Piper.

        Returns raw WAV bytes, or empty bytes if synthesis failed.
        """
        if not text.strip():
            return b""

        v = voice or self.voice
        model_path = self._model_path(v)

        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._synthesize_sync, text, model_path)

    def _synthesize_sync(self, text: str, model_path: str) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cmd = [PIPER_BIN, "--model", model_path, "--output_file", tmp_path]
            result = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.error("Piper TTS error: %s", result.stderr.decode()[:200])
                return b""
            return Path(tmp_path).read_bytes()
        except FileNotFoundError:
            logger.warning("Piper binary not found at '%s'", PIPER_BIN)
            return b""
        except Exception as e:
            logger.error("TTS synthesis error: %s", e)
            return b""
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def list_voices(self) -> list[dict]:
        return [
            {
                "id": voice_id,
                "model": model_file,
                "available": Path(MODELS_DIR, model_file).exists(),
            }
            for voice_id, model_file in AVAILABLE_VOICES.items()
        ]


_tts: TTSEngine | None = None


def get_tts(voice: str = DEFAULT_VOICE) -> TTSEngine:
    global _tts
    if _tts is None:
        _tts = TTSEngine(voice=voice)
    return _tts
