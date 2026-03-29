"""
system_modules/notify_push/vapid.py — VAPID key management for Web Push

Generates, loads, and manages VAPID keys (Voluntary Application Server
Identification, RFC 8292) used for authenticating Web Push messages.

Keys stored at:
  /secure/vapid_private.pem  — ECDSA P-256 private key
  /secure/vapid_public.pem   — corresponding public key (URL-safe base64)
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

VAPID_PRIVATE_PATH = Path(os.environ.get("VAPID_PRIVATE_KEY", "/secure/vapid_private.pem"))
VAPID_PUBLIC_PATH = Path(os.environ.get("VAPID_PUBLIC_KEY_FILE", "/secure/vapid_public.pem"))


def get_or_create_vapid_keys() -> tuple[str, str]:
    """Return (private_key_pem, public_key_urlsafe_b64).

    Generates new ECDSA P-256 keys if they don't exist yet.
    """
    if VAPID_PRIVATE_PATH.exists() and VAPID_PUBLIC_PATH.exists():
        return _load_existing_keys()
    return _generate_keys()


def _load_existing_keys() -> tuple[str, str]:
    """Load existing VAPID keys from disk."""
    private_pem = VAPID_PRIVATE_PATH.read_text().strip()
    public_b64 = VAPID_PUBLIC_PATH.read_text().strip()
    logger.debug("VAPID keys loaded from %s", VAPID_PRIVATE_PATH.parent)
    return private_pem, public_b64


def _generate_keys() -> tuple[str, str]:
    """Generate new ECDSA P-256 VAPID key pair."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        raise RuntimeError("cryptography library required for VAPID key generation")

    private_key = ec.generate_private_key(ec.SECP256R1())

    # Serialize private key as PEM
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    # Public key as uncompressed point (65 bytes), URL-safe base64
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(public_bytes).decode().rstrip("=")

    # Write to disk
    VAPID_PRIVATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    VAPID_PRIVATE_PATH.write_text(private_pem)
    VAPID_PRIVATE_PATH.chmod(0o600)
    VAPID_PUBLIC_PATH.write_text(public_b64)
    VAPID_PUBLIC_PATH.chmod(0o644)

    logger.info("VAPID keys generated at %s", VAPID_PRIVATE_PATH.parent)
    return private_pem, public_b64


def get_public_key() -> str:
    """Return the VAPID public key as URL-safe base64 (for browser subscription)."""
    _, public_b64 = get_or_create_vapid_keys()
    return public_b64
