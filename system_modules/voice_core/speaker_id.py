"""
system_modules/voice_core/speaker_id.py — Speaker ID using resemblyzer

Provides:
  - Voice embedding enrollment
  - Speaker verification against enrolled embeddings
  - Persistent embeddings stored as numpy arrays
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EMBEDDINGS_DIR = os.environ.get("SPEAKER_EMBEDDINGS_DIR", "/var/lib/selena/speaker_embeddings")
SIMILARITY_THRESHOLD = float(os.environ.get("SPEAKER_THRESHOLD", "0.75"))
SAMPLE_RATE = 16000


class SpeakerID:
    """Speaker identification and verification via resemblyzer."""

    def __init__(self) -> None:
        self._encoder = None
        self._embeddings: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._load_embeddings()

    def _load_model(self) -> bool:
        if self._encoder is not None:
            return True
        try:
            from resemblyzer import VoiceEncoder
            self._encoder = VoiceEncoder()
            logger.info("Speaker ID model loaded")
            return True
        except ImportError:
            logger.warning("resemblyzer not installed — speaker ID unavailable")
            return False
        except Exception as e:
            logger.error("Failed to load speaker ID model: %s", e)
            return False

    def _load_embeddings(self) -> None:
        path = Path(EMBEDDINGS_DIR)
        if not path.exists():
            return
        try:
            import numpy as np
        except ImportError:
            return
        for emb_file in path.glob("*.npy"):
            user_id = emb_file.stem
            try:
                self._embeddings[user_id] = np.load(str(emb_file))
                logger.debug("Loaded embedding for user '%s'", user_id)
            except Exception as e:
                logger.warning("Failed to load embedding for %s: %s", user_id, e)

    def _save_embedding(self, user_id: str, embedding: Any) -> None:
        import numpy as np
        path = Path(EMBEDDINGS_DIR)
        path.mkdir(parents=True, exist_ok=True)
        np.save(str(path / f"{user_id}.npy"), embedding)

    def _audio_to_float(self, audio_bytes: bytes) -> Any:
        import numpy as np
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        return audio_int16.astype(np.float32) / 32768.0

    def _compute_embedding(self, audio_float: Any) -> Any | None:
        if not self._load_model():
            return None
        try:
            from resemblyzer import preprocess_wav
            wav = preprocess_wav(audio_float, source_sr=SAMPLE_RATE)
            return self._encoder.embed_utterance(wav)
        except Exception as e:
            logger.error("Embedding computation error: %s", e)
            return None

    async def enroll(self, user_id: str, audio_bytes: bytes) -> bool:
        """Enroll a speaker by computing and storing their voice embedding.

        Returns True on success.
        """
        if not user_id or not audio_bytes:
            return False

        loop = asyncio.get_event_loop()
        async with self._lock:
            audio_float = self._audio_to_float(audio_bytes)
            embedding = await loop.run_in_executor(None, self._compute_embedding, audio_float)
            if embedding is None:
                return False

            self._embeddings[user_id] = embedding
            self._save_embedding(user_id, embedding)
            logger.info("Speaker '%s' enrolled", user_id)
            return True

    async def identify(self, audio_bytes: bytes) -> str | None:
        """Identify the speaker in audio_bytes.

        Returns user_id of the best match if similarity >= threshold, else None.
        """
        if not self._embeddings or not audio_bytes:
            return None

        import numpy as np

        loop = asyncio.get_event_loop()
        audio_float = self._audio_to_float(audio_bytes)
        embedding = await loop.run_in_executor(None, self._compute_embedding, audio_float)
        if embedding is None:
            return None

        best_user: str | None = None
        best_score = 0.0

        for user_id, stored in self._embeddings.items():
            # Cosine similarity
            sim = float(np.dot(embedding, stored) / (np.linalg.norm(embedding) * np.linalg.norm(stored) + 1e-9))
            if sim > best_score:
                best_score = sim
                best_user = user_id

        if best_score >= SIMILARITY_THRESHOLD:
            logger.info("Speaker identified as '%s' (score=%.2f)", best_user, best_score)
            return best_user

        logger.debug("No speaker match (best=%.2f, threshold=%.2f)", best_score, SIMILARITY_THRESHOLD)
        return None

    def list_enrolled(self) -> list[str]:
        return list(self._embeddings.keys())

    def remove_enrollment(self, user_id: str) -> bool:
        if user_id in self._embeddings:
            del self._embeddings[user_id]
            emb_file = Path(EMBEDDINGS_DIR) / f"{user_id}.npy"
            emb_file.unlink(missing_ok=True)
            logger.info("Enrollment for '%s' removed", user_id)
            return True
        return False


_speaker_id: SpeakerID | None = None


def get_speaker_id() -> SpeakerID:
    global _speaker_id
    if _speaker_id is None:
        _speaker_id = SpeakerID()
    return _speaker_id
