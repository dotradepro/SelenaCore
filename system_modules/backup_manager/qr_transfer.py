"""
system_modules/backup_manager/qr_transfer.py — QR-code-based secrets transfer between devices

Encodes critical secrets (vault key, device credentials) as an encrypted QR code
so a new device can bootstrap by scanning it from the old device's screen.

Format: JSON payload → AES-256-GCM encrypted → base64 → QR code image
Transfer PIN: 6-digit PIN used as additional password layer (PBKDF2-derived key).
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
from typing import Any

logger = logging.getLogger(__name__)


def _derive_qr_key(pin: str, salt: bytes) -> bytes:
    from hashlib import pbkdf2_hmac
    return pbkdf2_hmac("sha256", pin.encode(), salt, 500_000)


def encode_transfer_qr(payload: dict[str, Any], pin: str) -> bytes:
    """Encrypt payload and encode as PNG QR code bytes.

    Returns PNG image bytes.
    Raises RuntimeError if required libraries are missing.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import qrcode  # type: ignore
        import io
    except ImportError as exc:
        raise RuntimeError(f"Required library missing: {exc}. Install: pip install cryptography qrcode[pil]")

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_qr_key(pin, salt)
    aesgcm = AESGCM(key)

    plaintext = json.dumps(payload).encode()
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)

    # Format: "SELENA:" + base64(salt + nonce + ciphertext)
    encoded = base64.b64encode(salt + nonce + ciphertext).decode()
    qr_data = f"SELENA:{encoded}"

    if len(qr_data) > 2953:  # QR code max capacity
        raise ValueError(f"Payload too large for QR code: {len(qr_data)} chars")

    img = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H)
    img.add_data(qr_data)
    img.make(fit=True)
    image = img.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def decode_transfer_qr(qr_data: str, pin: str) -> dict[str, Any]:
    """Decode and decrypt a QR transfer payload.

    Raises ValueError on invalid format or decryption failure.
    """
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError(f"cryptography library required: {exc}")

    if not qr_data.startswith("SELENA:"):
        raise ValueError("Not a SelenaCore QR transfer code")

    try:
        raw = base64.b64decode(qr_data[7:])
        salt, nonce, ciphertext = raw[:16], raw[16:28], raw[28:]
        key = _derive_qr_key(pin, salt)
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode())
    except Exception as exc:
        raise ValueError(f"QR decode failed (wrong PIN or corrupted data): {exc}") from exc


def generate_transfer_pin() -> str:
    """Generate a cryptographically random 6-digit transfer PIN."""
    return str(secrets.randbelow(1_000_000)).zfill(6)
