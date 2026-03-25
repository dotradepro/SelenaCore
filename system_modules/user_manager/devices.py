"""
system_modules/user_manager/devices.py — Registered device management

A "registered device" is a browser/phone that has been paired to a user account.
After registration the device receives a device_token stored as HttpOnly cookie.
On every visit the token is verified against the DB — if valid the user is recognized
without needing to enter a PIN.

Security:
  - plain_token is a UUID4 — returned ONCE at registration, never stored in DB
  - DB stores SHA-256(plain_token) to prevent DB-leak → token theft
  - device_id is a separate UUID (opaque, safe to expose)
  - revoke() does soft-delete (active=0); hard-purge via purge_inactive()
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("SELENA_DB_URL", "sqlite+aiosqlite:////var/lib/selena/selena.db")


def _hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


@dataclass
class RegisteredDevice:
    device_id: str
    user_id: str
    device_name: str
    user_agent: str
    ip: str
    mac: str
    created_at: float
    last_seen: float | None
    active: bool


class DeviceManager:
    """CRUD for registered devices (browser/phone tokens)."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_tables(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS registered_devices (
                    device_id   TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    device_name TEXT NOT NULL DEFAULT '',
                    user_agent  TEXT NOT NULL DEFAULT '',
                    ip          TEXT NOT NULL DEFAULT '',
                    mac         TEXT NOT NULL DEFAULT '',
                    token_hash  TEXT UNIQUE NOT NULL,
                    created_at  REAL NOT NULL,
                    last_seen   REAL,
                    active      INTEGER NOT NULL DEFAULT 1
                )
            """))

    # ------------------------------------------------------------------
    # Write

    async def register(
        self,
        user_id: str,
        device_name: str,
        user_agent: str = "",
        ip: str = "",
        mac: str = "",
    ) -> str:
        """Create a new registered device.

        Returns the plain_token — store it in the client cookie.
        The plain_token is NEVER stored in the DB (only its hash).
        """
        plain_token = str(uuid.uuid4())
        device_id = str(uuid.uuid4())
        token_hash = _hash_token(plain_token)
        now = time.time()

        async with self._engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO registered_devices
                    (device_id, user_id, device_name, user_agent, ip, mac,
                     token_hash, created_at, active)
                VALUES
                    (:device_id, :user_id, :device_name, :user_agent, :ip, :mac,
                     :token_hash, :created_at, 1)
            """), {
                "device_id": device_id,
                "user_id": user_id,
                "device_name": device_name,
                "user_agent": user_agent,
                "ip": ip,
                "mac": mac,
                "token_hash": token_hash,
                "created_at": now,
            })

        logger.info("Device registered: %s for user %s (ip=%s)", device_id, user_id, ip)
        return plain_token

    async def revoke(self, device_id: str) -> None:
        """Soft-delete a device (active=0). The cookie becomes invalid on next verify."""
        async with self._engine.begin() as conn:
            await conn.execute(
                text("UPDATE registered_devices SET active = 0 WHERE device_id = :id"),
                {"id": device_id},
            )
        logger.info("Device revoked: %s", device_id)

    async def touch(self, plain_token: str) -> None:
        """Update last_seen for the device matching this token (fire-and-forget)."""
        token_hash = _hash_token(plain_token)
        async with self._engine.begin() as conn:
            await conn.execute(
                text("""
                    UPDATE registered_devices
                    SET last_seen = :ts
                    WHERE token_hash = :hash AND active = 1
                """),
                {"ts": time.time(), "hash": token_hash},
            )

    # ------------------------------------------------------------------
    # Read

    async def verify(self, plain_token: str) -> dict | None:
        """Verify a device token.

        Returns ``{device_id, user_id, device_name}`` or ``None`` if
        the token is invalid, revoked, or the owning user is inactive.
        """
        token_hash = _hash_token(plain_token)
        async with self._engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT d.device_id, d.user_id, d.device_name,
                       u.role, u.display_name, u.active AS user_active
                FROM registered_devices d
                JOIN users u ON u.user_id = d.user_id
                WHERE d.token_hash = :hash AND d.active = 1
            """), {"hash": token_hash})
            row = result.fetchone()

        if not row:
            return None
        row = dict(row._mapping)
        if not row.get("user_active", 0):
            return None  # user was deactivated

        # Update last_seen asynchronously (don't block the caller)
        await self.touch(plain_token)

        return {
            "device_id": row["device_id"],
            "user_id": row["user_id"],
            "device_name": row["device_name"],
            "role": row["role"],
            "display_name": row["display_name"],
        }

    async def list_by_user(self, user_id: str) -> list[RegisteredDevice]:
        """List all active devices for a user."""
        async with self._engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT device_id, user_id, device_name, user_agent,
                       ip, mac, created_at, last_seen, active
                FROM registered_devices
                WHERE user_id = :uid AND active = 1
                ORDER BY created_at DESC
            """), {"uid": user_id})
            rows = result.fetchall()

        return [
            RegisteredDevice(
                device_id=r.device_id,
                user_id=r.user_id,
                device_name=r.device_name,
                user_agent=r.user_agent,
                ip=r.ip,
                mac=r.mac,
                created_at=r.created_at,
                last_seen=r.last_seen,
                active=bool(r.active),
            )
            for r in rows
        ]

    async def get_by_id(self, device_id: str) -> RegisteredDevice | None:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT device_id, user_id, device_name, user_agent,
                       ip, mac, created_at, last_seen, active
                FROM registered_devices
                WHERE device_id = :id
            """), {"id": device_id})
            row = result.fetchone()
        if not row:
            return None
        r = dict(row._mapping)
        return RegisteredDevice(**{**r, "active": bool(r["active"])})

    async def rename(self, device_id: str, new_name: str) -> bool:
        """Rename a registered device. Returns True if a row was updated."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("UPDATE registered_devices SET device_name = :name WHERE device_id = :id AND active = 1"),
                {"name": new_name[:120], "id": device_id},
            )
        updated = result.rowcount > 0
        if updated:
            logger.info("Device renamed: %s → %s", device_id, new_name)
        return updated

    async def purge_inactive(self) -> int:
        """Hard-delete rows that have been soft-deleted. Returns count removed."""
        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM registered_devices WHERE active = 0")
            )
            return result.rowcount
