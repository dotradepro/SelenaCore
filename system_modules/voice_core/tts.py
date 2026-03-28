"""
system_modules/voice_core/tts.py — Piper TTS wrapper

Supports:
  - Local text-to-speech via piper-tts
  - Multiple voices / languages
  - Returns WAV audio bytes
  - Text sanitization for clean synthesis
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MODELS_DIR = os.environ.get("PIPER_MODELS_DIR", "/var/lib/selena/models/piper")
DEFAULT_VOICE = os.environ.get("PIPER_VOICE", "uk_UA-ukrainian_tts-medium")
PIPER_BIN = os.environ.get("PIPER_BIN", "piper")



def sanitize_for_tts(text: str) -> str:
    """Clean text for Piper TTS — remove markdown, special chars, URLs, emoji etc."""
    s = text

    # Remove code blocks (```...```) first
    s = re.sub(r'```[\s\S]*?```', ' ', s)
    # Remove inline code (`...`)
    s = re.sub(r'`[^`]*`', ' ', s)

    # Remove markdown links [text](url) → text  (BEFORE removing URLs!)
    s = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', s)

    # Remove URLs
    s = re.sub(r'https?://\S+', '', s)

    # Remove markdown headers (## Header)
    s = re.sub(r'^#{1,6}\s+', '', s, flags=re.MULTILINE)

    # Remove markdown bold/italic (**text**, *text*, __text__, _text_)
    s = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', s)
    s = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', s)

    # Remove remaining parentheses with URLs or short refs inside
    s = re.sub(r'\(https?://[^)]*\)', '', s)
    s = re.sub(r'\(\s*\S+\.\S+\s*\)', '', s)

    # Remove HTML tags
    s = re.sub(r'<[^>]+>', ' ', s)

    # Remove emoji (Unicode emoji ranges)
    s = re.sub(
        r'[\U0001F600-\U0001F64F'   # emoticons
        r'\U0001F300-\U0001F5FF'     # symbols & pictographs
        r'\U0001F680-\U0001F6FF'     # transport & map
        r'\U0001F1E0-\U0001F1FF'     # flags
        r'\U00002702-\U000027B0'     # dingbats
        r'\U0000FE00-\U0000FE0F'     # variation selectors
        r'\U0001F900-\U0001F9FF'     # supplemental symbols
        r'\U0001FA00-\U0001FA6F'     # chess symbols
        r'\U0001FA70-\U0001FAFF'     # symbols extended
        r'\U00002600-\U000026FF'     # misc symbols
        r'\U0000200D'                # zero width joiner
        r'\U00002B50\U00002B55'      # stars
        r']+', ' ', s
    )

    # Remove bullet points and list markers
    s = re.sub(r'^[\s]*[-•*►▸▹→]\s*', '', s, flags=re.MULTILINE)
    s = re.sub(r'^[\s]*\d+[.)]\s*', '', s, flags=re.MULTILINE)

    # Remove special chars that cause noise (keep letters, digits, basic punctuation)
    s = re.sub(r'[~^|\\{}<>\[\]@#$%&=+`]', ' ', s)

    # Remove stray parentheses with nothing useful inside
    s = re.sub(r'\(\s*\)', '', s)

    # Normalize quotes and dashes
    s = s.replace('«', '"').replace('»', '"')
    s = s.replace('"', '"').replace('"', '"')
    s = s.replace('—', ' — ').replace('–', ' — ')

    # Collapse multiple spaces/newlines
    s = re.sub(r'\n{3,}', '\n\n', s)
    s = re.sub(r'[ \t]{2,}', ' ', s)

    s = s.strip()

    # --- Piper bug workaround: trailing punctuation causes noise/static ---
    # Known issue: https://github.com/home-assistant/core/issues/156603
    # Split into sentences and strip trailing punctuation from each.
    # Piper's VITS model generates noise bursts after . ! ? at sentence boundaries.
    # We split on sentence endings, trim the punct, and rejoin with newlines
    # so Piper uses sentence_silence for natural pauses instead.
    sentences = re.split(r'(?<=[.!?…])\s+', s)
    cleaned = []
    for sent in sentences:
        sent = sent.strip()
        if sent:
            # Strip trailing punctuation that causes noise
            sent = re.sub(r'[.!?…:;]+$', '', sent).strip()
            if sent:
                cleaned.append(sent)
    s = '\n'.join(cleaned) if cleaned else s

    # Piper bug: uppercase letters cause phoneme confusion and garbled audio
    # Convert everything to lowercase for cleaner synthesis
    s = s.lower()

    return s.strip()


class TTSSettings:
    """Piper synthesis parameters."""
    length_scale: float = 1.0       # Speech speed (>1 slower, <1 faster)
    noise_scale: float = 0.667      # Intonation variability
    noise_w_scale: float = 0.8      # Phoneme width variability
    sentence_silence: float = 0.2   # Pause between sentences (sec)
    volume: float = 1.0             # Volume multiplier
    speaker: int = 0                # Speaker ID for multi-speaker models

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            if hasattr(self, k):
                setattr(self, k, v)


def _load_tts_settings() -> TTSSettings:
    """Load TTS settings from config."""
    try:
        from core.config_writer import read_config
        cfg = read_config()
        tts_cfg = cfg.get("voice", {}).get("tts_settings", {})
        return TTSSettings(**tts_cfg)
    except Exception:
        return TTSSettings()


class TTSEngine:
    """Piper TTS wrapper — converts text to WAV bytes."""

    def __init__(self, voice: str = DEFAULT_VOICE) -> None:
        self.voice = voice
        self._lock = asyncio.Lock()

    def _model_path(self, voice: str) -> str:
        return str(Path(MODELS_DIR) / f"{voice}.onnx")

    async def synthesize(self, text: str, voice: str | None = None, settings: TTSSettings | None = None) -> bytes:
        """Convert text to WAV audio bytes using Piper.

        Returns raw WAV bytes, or empty bytes if synthesis failed.
        """
        clean = sanitize_for_tts(text)
        if not clean:
            return b""

        v = voice or self.voice
        model_path = self._model_path(v)
        s = settings or _load_tts_settings()

        async with self._lock:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._synthesize_sync, clean, model_path, s)

    def _synthesize_sync(self, text: str, model_path: str, settings: TTSSettings | None = None) -> bytes:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        s = settings or TTSSettings()

        try:
            cmd = [
                PIPER_BIN, "--model", model_path, "--output_file", tmp_path,
                "--length-scale", str(s.length_scale),
                "--noise-scale", str(s.noise_scale),
                "--noise-w-scale", str(s.noise_w_scale),
                "--sentence-silence", str(s.sentence_silence),
                "--volume", str(s.volume),
                "--speaker", str(s.speaker),
            ]

            # GPU acceleration: add --cuda if available
            try:
                from core.hardware import should_use_gpu, onnxruntime_has_gpu
                if should_use_gpu() and onnxruntime_has_gpu():
                    cmd.append("--cuda")
            except Exception:
                pass
            result = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.error("Piper TTS error: %s", result.stderr.decode()[:200])
                return b""

            # Post-process: trim trailing noise/silence artifact (known Piper bug)
            wav_bytes = self._trim_trailing_noise(tmp_path)
            return wav_bytes if wav_bytes else Path(tmp_path).read_bytes()
        except FileNotFoundError:
            logger.warning("Piper binary not found at '%s'", PIPER_BIN)
            return b""
        except Exception as e:
            logger.error("TTS synthesis error: %s", e)
            return b""
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    @staticmethod
    def _trim_trailing_noise(wav_path: str) -> bytes | None:
        """Trim trailing noise/static from WAV using ffmpeg silenceremove filter.

        Piper VITS models produce noise artifacts at end of utterances after punctuation.
        This trims the last portion if it's noise above speech frequency.
        """
        trimmed_path = wav_path + ".trimmed.wav"
        try:
            # Strategy: pad 150ms silence at end, then reverse-trim silence from end
            # This removes any trailing noise burst while keeping the speech intact
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", wav_path,
                    "-af", (
                        # Pad 100ms of silence at the very end (safety margin)
                        "apad=pad_dur=0.1,"
                        # Reverse → trim leading silence (which is the trailing noise) → reverse back
                        "areverse,silenceremove=start_periods=1:start_silence=0.05:start_threshold=-40dB,areverse"
                    ),
                    "-ar", "16000", "-ac", "1",
                    trimmed_path,
                ],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0 and Path(trimmed_path).exists():
                data = Path(trimmed_path).read_bytes()
                if len(data) > 100:
                    return data
        except Exception as e:
            logger.debug("Audio trim failed (non-critical): %s", e)
        finally:
            Path(trimmed_path).unlink(missing_ok=True)
        return None

    def list_voices(self) -> list[dict]:
        """Discover installed voices by scanning the models directory."""
        models_path = Path(MODELS_DIR)
        if not models_path.is_dir():
            return []
        return [
            {
                "id": f.stem,
                "model": f.name,
                "available": True,
            }
            for f in sorted(models_path.iterdir())
            if f.is_file() and f.suffix == ".onnx"
        ]


_tts: TTSEngine | None = None


def get_tts(voice: str = DEFAULT_VOICE) -> TTSEngine:
    global _tts
    if _tts is None:
        _tts = TTSEngine(voice=voice)
    return _tts
