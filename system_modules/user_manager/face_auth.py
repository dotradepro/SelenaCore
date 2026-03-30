"""
system_modules/user_manager/face_auth.py — Face ID via face_recognition (dlib)

Enrollment:
  - Capture face image from browser webcam (JPEG bytes)
  - Compute 128-d face encoding via face_recognition
  - Store encoding as numpy array on disk

Verification:
  - Compare captured encoding against enrolled user encodings
  - Returns matched user_id or None
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

ENCODINGS_DIR = os.environ.get("FACE_ENCODINGS_DIR", "/var/lib/selena/face_encodings")
TOLERANCE = float(os.environ.get("FACE_TOLERANCE", "0.5"))  # lower = stricter


def _load_lib():
    try:
        import face_recognition
        return face_recognition
    except ImportError:
        logger.warning("face_recognition not installed — Face ID unavailable")
        return None


def _jpeg_to_array(jpeg_bytes: bytes):
    """Convert JPEG bytes to numpy array for face_recognition."""
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    return np.array(img)


def _encoding_path(user_id: str) -> Path:
    return Path(ENCODINGS_DIR) / f"{user_id}.npy"


def enroll(user_id: str, jpeg_bytes: bytes) -> bool:
    """Enroll a user's face from JPEG bytes.

    Returns True if a face was found and enrollment succeeded.
    """
    fr = _load_lib()
    if fr is None:
        return False

    try:
        img_array = _jpeg_to_array(jpeg_bytes)
        encodings = fr.face_encodings(img_array)
        if not encodings:
            logger.warning("No face detected in enrollment image for user %s", user_id)
            return False

        import numpy as np
        Path(ENCODINGS_DIR).mkdir(parents=True, exist_ok=True)
        np.save(str(_encoding_path(user_id)), encodings[0])
        logger.info("Face enrolled for user %s", user_id)
        return True
    except Exception as e:
        logger.error("Face enrollment error for %s: %s", user_id, e)
        return False


def identify(jpeg_bytes: bytes) -> str | None:
    """Identify a person from a face image.

    Returns matched user_id or None.
    Loads all enrolled encodings and finds the closest match.
    """
    fr = _load_lib()
    if fr is None:
        return None

    try:
        img_array = _jpeg_to_array(jpeg_bytes)
        unknown_encodings = fr.face_encodings(img_array)
        if not unknown_encodings:
            return None
        unknown = unknown_encodings[0]
    except Exception as e:
        logger.error("Face encoding error: %s", e)
        return None

    # Load all enrolled encodings
    enc_dir = Path(ENCODINGS_DIR)
    if not enc_dir.exists():
        return None

    best_user: str | None = None
    best_distance = 1.0

    for enc_file in enc_dir.glob("*.npy"):
        user_id = enc_file.stem
        try:
            import numpy as np
            known = np.load(str(enc_file))
            distance = fr.face_distance([known], unknown)[0]
            if distance < best_distance:
                best_distance = distance
                best_user = user_id
        except Exception as e:
            logger.warning("Failed to load encoding for %s: %s", user_id, e)

    if best_user and best_distance <= TOLERANCE:
        logger.info("Face identified as '%s' (distance=%.3f)", best_user, best_distance)
        return best_user

    return None


def remove_enrollment(user_id: str) -> bool:
    enc_file = _encoding_path(user_id)
    if enc_file.exists():
        enc_file.unlink()
        logger.info("Face enrollment removed for %s", user_id)
        return True
    return False


def list_enrolled() -> list[str]:
    enc_dir = Path(ENCODINGS_DIR)
    if not enc_dir.exists():
        return []
    return [f.stem for f in enc_dir.glob("*.npy")]
