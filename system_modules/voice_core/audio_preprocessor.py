"""
system_modules/voice_core/audio_preprocessor.py — Real-time audio preprocessing.

Pipeline (per 250ms chunk):
  1. High-pass IIR filter (80Hz) — removes DC offset, mains hum, rumble
  2. Spectral gating — subtracts noise profile learned during silence
  3. AGC — normalizes amplitude to consistent level
  4. Fast RMS — returns energy alongside cleaned audio

All stages use numpy only (no extra dependencies).
"""
from __future__ import annotations

import logging
import os

import numpy as np

logger = logging.getLogger(__name__)

# Defaults (overridable via env or config)
_DEFAULT_HIGHPASS_FREQ = 80       # Hz
_DEFAULT_NOISE_ALPHA = 1.5        # oversubtraction factor
_DEFAULT_AGC_TARGET = 1000.0      # target RMS for speech (moderate boost)
_DEFAULT_AGC_MAX_GAIN = 8.0       # max amplification factor
_DEFAULT_SPECTRAL_FLOOR = 0.05    # minimum fraction of original magnitude
_DEFAULT_NOISE_DECAY = 0.95       # exponential moving average for noise profile
_DEFAULT_MIN_NOISE_FRAMES = 8     # minimum silence frames before profile is usable


class AudioPreprocessor:
    """Real-time audio preprocessor for voice recognition.

    Processes raw PCM S16_LE mono chunks and returns cleaned PCM + RMS energy.
    Thread-safe for single-producer (audio loop) usage.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        chunk_size: int = 4000,
        enabled: bool = True,
    ) -> None:
        self._sr = sample_rate
        self._chunk_size = chunk_size
        self.enabled = enabled

        # ── Config ──
        self.highpass_freq: float = float(os.getenv(
            "AUDIO_PREPROCESS_HIGHPASS_FREQ", str(_DEFAULT_HIGHPASS_FREQ)))
        self.noise_alpha: float = float(os.getenv(
            "AUDIO_PREPROCESS_NOISE_ALPHA", str(_DEFAULT_NOISE_ALPHA)))
        self.agc_target: float = float(os.getenv(
            "AUDIO_PREPROCESS_AGC_TARGET", str(_DEFAULT_AGC_TARGET)))
        self.agc_max_gain: float = _DEFAULT_AGC_MAX_GAIN
        self.spectral_floor: float = _DEFAULT_SPECTRAL_FLOOR
        self.noise_decay: float = _DEFAULT_NOISE_DECAY

        # ── High-pass IIR state ──
        self._hp_alpha = self._compute_hp_alpha(self.highpass_freq, sample_rate)
        self._hp_prev_x: float = 0.0
        self._hp_prev_y: float = 0.0

        # ── Spectral gating state ──
        self._n_fft = chunk_size
        self._noise_profile: np.ndarray | None = None  # average |FFT| of noise
        self._noise_frame_count: int = 0

        # ── Speaker gate state (Stage 2) ──
        self._active_speaker_embedding: np.ndarray | None = None
        self._speaker_gate_enabled: bool = os.getenv(
            "AUDIO_PREPROCESS_SPEAKER_GATE", "true").lower() == "true"
        self._speaker_similarity_threshold: float = 0.65
        self._speaker_buffer = bytearray()
        self._speaker_check_interval: int = 4  # check every N chunks (~1s)
        self._speaker_chunk_counter: int = 0
        self._speaker_match: bool = True  # assume match until proven otherwise

        logger.info(
            "AudioPreprocessor: hp=%dHz noise_alpha=%.1f agc_target=%.0f enabled=%s",
            self.highpass_freq, self.noise_alpha, self.agc_target, self.enabled,
        )

    @staticmethod
    def _compute_hp_alpha(cutoff_hz: float, sample_rate: int) -> float:
        """Compute alpha for first-order IIR high-pass filter."""
        rc = 1.0 / (2.0 * np.pi * cutoff_hz)
        dt = 1.0 / sample_rate
        return rc / (rc + dt)

    # ── Public API ────────────────────────────────────────────────────────

    def process(self, pcm_chunk: bytes) -> tuple[bytes, float]:
        """Process a raw PCM chunk. Returns (cleaned_pcm_bytes, rms_energy).

        If preprocessing is disabled, returns original bytes with fast RMS.
        """
        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float64)

        if not self.enabled:
            rms = self._fast_rms(samples)
            return pcm_chunk, rms

        # 1. High-pass filter
        samples = self._highpass(samples)

        # 2. Spectral gating (if noise profile available)
        if self._noise_profile is not None and self._noise_frame_count >= _DEFAULT_MIN_NOISE_FRAMES:
            samples = self._spectral_gate(samples)

        # 3. AGC
        samples = self._agc(samples)

        # Compute RMS on cleaned signal
        rms = self._fast_rms(samples)

        # Convert back to int16 PCM
        out = np.clip(samples, -32767, 32767).astype(np.int16)
        return out.tobytes(), rms

    def update_noise_profile(self, pcm_chunk: bytes) -> None:
        """Update noise profile from a silence chunk. Call when has_speech=False."""
        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float64)
        # Apply highpass first (consistent with process path)
        samples = self._highpass_readonly(samples)
        magnitude = np.abs(np.fft.rfft(samples, n=self._n_fft))

        if self._noise_profile is None:
            self._noise_profile = magnitude.copy()
            self._noise_frame_count = 1
        else:
            decay = self.noise_decay
            self._noise_profile = decay * self._noise_profile + (1.0 - decay) * magnitude
            self._noise_frame_count += 1

    def set_active_speaker(self, embedding: np.ndarray) -> None:
        """Set the voice embedding of the person who said the wake word."""
        self._active_speaker_embedding = embedding
        self._speaker_match = True
        self._speaker_buffer.clear()
        self._speaker_chunk_counter = 0
        logger.info("AudioPreprocessor: active speaker set")

    def clear_active_speaker(self) -> None:
        """Clear active speaker (return to IDLE)."""
        self._active_speaker_embedding = None
        self._speaker_match = True
        self._speaker_buffer.clear()
        self._speaker_chunk_counter = 0

    async def check_speaker_async(self, pcm_chunk: bytes) -> bool:
        """Accumulate audio and check speaker match periodically.

        Returns True if the current speaker matches the active speaker
        (or if speaker gate is disabled / no active speaker set).
        """
        if not self._speaker_gate_enabled or self._active_speaker_embedding is None:
            return True

        self._speaker_buffer.extend(pcm_chunk)
        self._speaker_chunk_counter += 1

        # Check every N chunks (~1 second at 250ms/chunk)
        if self._speaker_chunk_counter >= self._speaker_check_interval:
            self._speaker_chunk_counter = 0
            # Need at least 1.5s of audio for reliable embedding
            min_bytes = self._sr * 2 * 1.5  # 1.5 seconds
            if len(self._speaker_buffer) >= min_bytes:
                import asyncio
                loop = asyncio.get_running_loop()
                audio_bytes = bytes(self._speaker_buffer)
                self._speaker_buffer.clear()
                similarity = await loop.run_in_executor(
                    None, self._compute_speaker_similarity, audio_bytes,
                )
                if similarity is not None:
                    self._speaker_match = similarity >= self._speaker_similarity_threshold
                    if not self._speaker_match:
                        logger.debug(
                            "AudioPreprocessor: speaker mismatch (sim=%.2f < %.2f)",
                            similarity, self._speaker_similarity_threshold,
                        )

        return self._speaker_match

    # ── Private methods ───────────────────────────────────────────────────

    @staticmethod
    def _fast_rms(samples: np.ndarray) -> float:
        """Fast RMS calculation using numpy."""
        if len(samples) == 0:
            return 0.0
        return float(np.sqrt(np.mean(samples * samples)))

    def _highpass(self, samples: np.ndarray) -> np.ndarray:
        """Apply first-order IIR high-pass filter (vectorized with state)."""
        # Compute differences: x[n] - x[n-1]
        diff = np.empty_like(samples)
        diff[0] = samples[0] - self._hp_prev_x
        diff[1:] = samples[1:] - samples[:-1]

        # IIR: y[n] = alpha * (y[n-1] + diff[n])
        # This is recursive so we use a loop but on the diff signal
        alpha = self._hp_alpha
        out = np.empty_like(samples)
        y = self._hp_prev_y
        for i in range(len(diff)):
            y = alpha * (y + diff[i])
            out[i] = y

        self._hp_prev_x = float(samples[-1])
        self._hp_prev_y = float(y)
        return out

    def _highpass_readonly(self, samples: np.ndarray) -> np.ndarray:
        """High-pass filter without updating state (for noise profiling)."""
        diff = np.empty_like(samples)
        diff[0] = samples[0]
        diff[1:] = samples[1:] - samples[:-1]

        alpha = self._hp_alpha
        out = np.empty_like(samples)
        y = 0.0
        for i in range(len(diff)):
            y = alpha * (y + diff[i])
            out[i] = y
        return out

    def _spectral_gate(self, samples: np.ndarray) -> np.ndarray:
        """Spectral subtraction using learned noise profile."""
        spectrum = np.fft.rfft(samples, n=self._n_fft)
        magnitude = np.abs(spectrum)
        phase = np.angle(spectrum)

        # Subtract noise with oversubtraction factor
        cleaned_mag = magnitude - self.noise_alpha * self._noise_profile

        # Floor: keep at least spectral_floor fraction of original
        floor = self.spectral_floor * magnitude
        cleaned_mag = np.maximum(cleaned_mag, floor)

        # Reconstruct
        cleaned = cleaned_mag * np.exp(1j * phase)
        result = np.fft.irfft(cleaned, n=self._n_fft)
        return result[:len(samples)]

    def _agc(self, samples: np.ndarray) -> np.ndarray:
        """Automatic gain control — normalize RMS to target level.

        Only amplifies when signal is clearly above noise floor (post-gating).
        Below speech_floor → no amplification (prevents boosting noise).
        Above speech_floor → boost to agc_target with max gain limit.
        """
        rms = self._fast_rms(samples)
        # Don't amplify residual noise — only real speech gets boosted
        # Post spectral-gate noise is ~1-20 RMS, speech is 50+
        if rms < 50.0:
            return samples
        gain = self.agc_target / rms
        gain = min(gain, self.agc_max_gain)
        return samples * gain

    def _compute_speaker_similarity(self, audio_bytes: bytes) -> float | None:
        """Compute cosine similarity with active speaker (runs in executor)."""
        if self._active_speaker_embedding is None:
            return None
        try:
            from system_modules.voice_core.speaker_id import get_speaker_id
            sid = get_speaker_id()
            audio_float = sid._audio_to_float(audio_bytes)
            embedding = sid._compute_embedding(audio_float)
            if embedding is None:
                return None
            stored = self._active_speaker_embedding
            sim = float(np.dot(embedding, stored) / (
                np.linalg.norm(embedding) * np.linalg.norm(stored) + 1e-9
            ))
            return sim
        except Exception as e:
            logger.debug("Speaker similarity error: %s", e)
            return None


# ── Singleton ─────────────────────────────────────────────────────────────

_preprocessor: AudioPreprocessor | None = None


def get_audio_preprocessor(
    sample_rate: int = 16000,
    chunk_size: int = 4000,
) -> AudioPreprocessor:
    global _preprocessor
    if _preprocessor is None:
        enabled = os.getenv("AUDIO_PREPROCESS_ENABLED", "true").lower() == "true"
        _preprocessor = AudioPreprocessor(
            sample_rate=sample_rate,
            chunk_size=chunk_size,
            enabled=enabled,
        )
    return _preprocessor
