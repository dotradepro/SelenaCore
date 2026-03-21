"""
system_modules/backup_manager/cloud_backup.py — E2E encrypted cloud backup

Encrypts backup archives with PBKDF2-HMAC-SHA256 + AES-256-GCM before upload.
Uploads to the Selena Platform cloud endpoint.

Key derivation: PBKDF2(password, salt, 600_000 iterations, SHA-256) → 256-bit key
Each backup: random 16-byte salt + 12-byte nonce stored as header.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from hashlib import pbkdf2_hmac
from pathlib import Path

import httpx

from .local_backup import create_backup, sha256_file

logger = logging.getLogger(__name__)

PBKDF2_ITERATIONS = 600_000
PLATFORM_BACKUP_URL = os.environ.get("PLATFORM_BACKUP_URL", "")
DEVICE_HASH = os.environ.get("PLATFORM_DEVICE_HASH", "")


def _derive_key(password: str, salt: bytes) -> bytes:
    return pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)


def encrypt_backup(archive_path: Path, password: str) -> bytes:
    """Encrypt backup file. Returns: salt(16) + nonce(12) + ciphertext."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("cryptography library required: pip install cryptography")

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    plaintext = archive_path.read_bytes()
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return salt + nonce + ciphertext


def decrypt_backup(encrypted: bytes, password: str, dest_path: Path) -> None:
    """Decrypt cloud backup bytes and write to dest_path."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("cryptography library required: pip install cryptography")

    salt = encrypted[:16]
    nonce = encrypted[16:28]
    ciphertext = encrypted[28:]
    key = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    dest_path.write_bytes(plaintext)
    logger.info("Backup decrypted to %s", dest_path)


async def cloud_backup(password: str, platform_token: str | None = None) -> bool:
    """Create local backup, encrypt it, and upload to platform. Returns True on success."""
    if not PLATFORM_BACKUP_URL:
        logger.warning("PLATFORM_BACKUP_URL not set, cloud backup skipped")
        return False

    archive_path = await create_backup()
    archive_hash = sha256_file(archive_path)

    loop = asyncio.get_event_loop()
    encrypted = await loop.run_in_executor(None, encrypt_backup, archive_path, password)

    headers = {"X-Selena-Device": DEVICE_HASH}
    if platform_token:
        headers["Authorization"] = f"Bearer {platform_token}"

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                PLATFORM_BACKUP_URL,
                content=encrypted,
                headers={
                    **headers,
                    "Content-Type": "application/octet-stream",
                    "X-Archive-Hash": archive_hash,
                },
            )
        if resp.status_code in (200, 201):
            logger.info("Cloud backup uploaded successfully (%d bytes)", len(encrypted))
            return True
        else:
            logger.error("Cloud backup upload failed: HTTP %d", resp.status_code)
            return False
    except Exception as exc:
        logger.error("Cloud backup upload error: %s", exc)
        return False
