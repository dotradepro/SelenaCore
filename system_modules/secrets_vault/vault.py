"""
system_modules/secrets_vault/vault.py — AES-256-GCM encrypted secrets storage

Stores OAuth tokens and third-party credentials encrypted at rest.
Token directory: /secure/tokens/<service_name>.enc
Key directory:   /secure/vault_key  (single master key file)

Encryption: AES-256-GCM with per-secret random 96-bit nonce.
Key derivation: loaded from /secure/vault_key or auto-generated on first run.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets as _secrets
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TOKENS_DIR = Path(os.environ.get("VAULT_TOKENS_DIR", "/secure/tokens"))
KEY_FILE = Path(os.environ.get("VAULT_KEY_FILE", "/secure/vault_key"))


@dataclass
class SecretRecord:
    service: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None  # unix timestamp
    scopes: list[str] | None = None
    extra: dict[str, Any] | None = None
    created_at: float = 0.0
    updated_at: float = 0.0


class SecretsVault:
    """AES-256-GCM encrypted token vault."""

    def __init__(self) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            self._aesgcm_cls = AESGCM
        except ImportError:
            raise RuntimeError("cryptography library required: pip install cryptography")

        TOKENS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._key = self._load_or_create_key()

    # --- Key management ---

    def _load_or_create_key(self) -> bytes:
        """Load or generate the 256-bit master key."""
        if KEY_FILE.exists():
            raw = KEY_FILE.read_bytes().strip()
            return base64.b64decode(raw)
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = _secrets.token_bytes(32)  # 256 bits
        KEY_FILE.write_bytes(base64.b64encode(key))
        KEY_FILE.chmod(0o600)
        logger.info("Vault master key generated at %s", KEY_FILE)
        return key

    # --- Internal encrypt/decrypt ---

    def _encrypt(self, plaintext: str) -> bytes:
        nonce = _secrets.token_bytes(12)  # 96-bit nonce
        aesgcm = self._aesgcm_cls(self._key)
        ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
        # Prefix nonce to ciphertext for storage
        return base64.b64encode(nonce + ct)

    def _decrypt(self, data: bytes) -> str:
        raw = base64.b64decode(data)
        nonce, ct = raw[:12], raw[12:]
        aesgcm = self._aesgcm_cls(self._key)
        return aesgcm.decrypt(nonce, ct, None).decode()

    # --- Public API ---

    def _path(self, service: str) -> Path:
        # Sanitize service name to prevent path traversal
        safe = "".join(c for c in service if c.isalnum() or c in "-_.")
        if not safe:
            raise ValueError(f"Invalid service name: {service!r}")
        return TOKENS_DIR / f"{safe}.enc"

    def store(self, record: SecretRecord) -> None:
        """Encrypt and write a secret record."""
        now = datetime.now(timezone.utc).timestamp()
        record.updated_at = now
        if record.created_at == 0.0:
            record.created_at = now
        payload = json.dumps(asdict(record))
        self._path(record.service).write_bytes(self._encrypt(payload))
        self._path(record.service).chmod(0o600)
        logger.debug("Stored secret for service %s", record.service)

    def load(self, service: str) -> SecretRecord | None:
        """Decrypt and return a secret record, or None if not found."""
        p = self._path(service)
        if not p.exists():
            return None
        try:
            payload = self._decrypt(p.read_bytes())
            d = json.loads(payload)
            return SecretRecord(**d)
        except Exception as exc:
            logger.error("Failed to decrypt secret for %s: %s", service, exc)
            return None

    def delete(self, service: str) -> bool:
        """Delete stored secret. Returns True if deleted."""
        p = self._path(service)
        if p.exists():
            p.unlink()
            logger.info("Deleted secret for service %s", service)
            return True
        return False

    def list_services(self) -> list[str]:
        """Return list of stored service names."""
        return [p.stem for p in TOKENS_DIR.glob("*.enc")]

    def is_expired(self, service: str) -> bool:
        """Return True if the token is expired or near expiry (within 5 min)."""
        record = self.load(service)
        if record is None or record.expires_at is None:
            return False
        margin = 5 * 60  # 5 minutes buffer
        return datetime.now(timezone.utc).timestamp() >= (record.expires_at - margin)


_vault_instance: SecretsVault | None = None


def get_vault() -> SecretsVault:
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = SecretsVault()
    return _vault_instance
