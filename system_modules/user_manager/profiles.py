"""
system_modules/user_manager/profiles.py — User profiles CRUD

Roles: admin | resident | guest
Storage: SQLite (same DB used by core registry)
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("SELENA_DB_URL", "sqlite+aiosqlite:////var/lib/selena/selena.db")
VALID_ROLES = {"owner", "admin", "user", "guest"}
# backward-compat alias: "resident" stored in old DB rows maps to "user"
_ROLE_ALIAS = {"resident": "user"}


@dataclass
class UserProfile:
    user_id: str
    username: str
    display_name: str
    role: str
    pin_hash: str
    created_at: float
    last_seen: float | None = None
    face_enrolled: bool = False
    voice_enrolled: bool = False
    active: bool = True


class UserNotFoundError(Exception):
    pass


class UserAlreadyExistsError(Exception):
    pass


class InvalidPinError(Exception):
    pass


def _hash_pin(pin: str) -> str:
    """Hash a numeric PIN with SHA-256 + salt."""
    salt = "selena-pin-salt-v1"
    return hashlib.sha256(f"{salt}{pin}".encode()).hexdigest()


class UserManager:
    """CRUD for user profiles stored in SQLite."""

    def __init__(self, db_url: str = DB_URL) -> None:
        self._db_url = db_url
        self._engine: AsyncEngine | None = None

    async def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self._db_url, echo=False)
            await self._ensure_tables()
        return self._engine

    async def _ensure_tables(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'resident',
                    pin_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_seen REAL,
                    face_enrolled INTEGER NOT NULL DEFAULT 0,
                    voice_enrolled INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1
                )
            """))

    async def _execute(self, query: str, params: dict | None = None) -> Any:
        engine = await self._get_engine()
        async with engine.begin() as conn:
            return await conn.execute(text(query), params or {})

    async def _fetch_one(self, query: str, params: dict | None = None) -> dict | None:
        engine = await self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text(query), params or {})
            row = result.fetchone()
            return dict(row._mapping) if row else None

    async def _fetch_all(self, query: str, params: dict | None = None) -> list[dict]:
        engine = await self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text(query), params or {})
            return [dict(row._mapping) for row in result.fetchall()]

    def _row_to_profile(self, row: dict) -> UserProfile:
        role = row["role"]
        role = _ROLE_ALIAS.get(role, role)  # map legacy "resident" → "user"
        return UserProfile(
            user_id=row["user_id"],
            username=row["username"],
            display_name=row["display_name"],
            role=role,
            pin_hash=row["pin_hash"],
            created_at=row["created_at"],
            last_seen=row.get("last_seen"),
            face_enrolled=bool(row.get("face_enrolled", 0)),
            voice_enrolled=bool(row.get("voice_enrolled", 0)),
            active=bool(row.get("active", 1)),
        )

    async def count_users(self) -> int:
        """Return number of active users."""
        row = await self._fetch_one("SELECT COUNT(*) AS cnt FROM users WHERE active = 1")
        return int(row["cnt"]) if row else 0

    async def create(
        self,
        username: str,
        display_name: str,
        pin: str,
        role: str = "user",
    ) -> UserProfile:
        # Map legacy alias
        role = _ROLE_ALIAS.get(role, role)
        if role not in VALID_ROLES:
            raise ValueError(f"Invalid role '{role}'. Must be one of {VALID_ROLES}")
        if not pin or not pin.isdigit() or len(pin) < 4:
            raise InvalidPinError("PIN must be at least 4 digits")

        existing = await self._fetch_one(
            "SELECT user_id FROM users WHERE username = :username", {"username": username}
        )
        if existing:
            raise UserAlreadyExistsError(f"Username '{username}' already exists")

        # First ever user automatically becomes owner
        if await self.count_users() == 0:
            role = "owner"
            logger.info("First user — assigning role=owner")

        user_id = str(uuid.uuid4())
        now = time.time()
        pin_hash = _hash_pin(pin)

        await self._execute("""
            INSERT INTO users (user_id, username, display_name, role, pin_hash, created_at, active)
            VALUES (:user_id, :username, :display_name, :role, :pin_hash, :created_at, 1)
        """, {
            "user_id": user_id, "username": username, "display_name": display_name,
            "role": role, "pin_hash": pin_hash, "created_at": now,
        })

        logger.info("User created: %s (%s) role=%s", username, user_id, role)
        profile = await self.get(user_id)
        return profile  # type: ignore

    async def get(self, user_id: str) -> UserProfile:
        row = await self._fetch_one("SELECT * FROM users WHERE user_id = :id", {"id": user_id})
        if not row:
            raise UserNotFoundError(f"User '{user_id}' not found")
        return self._row_to_profile(row)

    async def get_by_username(self, username: str) -> UserProfile | None:
        row = await self._fetch_one("SELECT * FROM users WHERE username = :u", {"u": username})
        return self._row_to_profile(row) if row else None

    async def list_all(self) -> list[UserProfile]:
        rows = await self._fetch_all("SELECT * FROM users WHERE active = 1 ORDER BY created_at")
        return [self._row_to_profile(r) for r in rows]

    async def update(self, user_id: str, **fields) -> UserProfile:
        allowed = {"display_name", "role", "face_enrolled", "voice_enrolled", "active"}
        update_fields = {k: v for k, v in fields.items() if k in allowed}
        if not update_fields:
            raise ValueError("No valid fields to update")
        if "role" in update_fields:
            update_fields["role"] = _ROLE_ALIAS.get(update_fields["role"], update_fields["role"])
            if update_fields["role"] not in VALID_ROLES:
                raise ValueError(f"Invalid role: {update_fields['role']}")

        set_clause = ", ".join(f"{k} = :{k}" for k in update_fields)
        await self._execute(
            f"UPDATE users SET {set_clause} WHERE user_id = :user_id",
            {**update_fields, "user_id": user_id}
        )
        return await self.get(user_id)

    async def update_pin(self, user_id: str, new_pin: str) -> None:
        """Replace a user's PIN hash."""
        if not new_pin or not new_pin.isdigit() or len(new_pin) < 4:
            raise InvalidPinError("PIN must be at least 4 digits")
        await self._execute(
            "UPDATE users SET pin_hash = :hash WHERE user_id = :id",
            {"hash": _hash_pin(new_pin), "id": user_id},
        )
        logger.info("PIN updated for user: %s", user_id)

    async def verify_pin(self, user_id: str, pin: str) -> bool:
        """Return True if pin matches the stored hash for user_id."""
        row = await self._fetch_one(
            "SELECT pin_hash FROM users WHERE user_id = :id AND active = 1",
            {"id": user_id},
        )
        if not row:
            return False
        return row["pin_hash"] == _hash_pin(pin)

    async def delete(self, user_id: str) -> None:
        """Soft delete — sets active=0."""
        await self._execute(
            "UPDATE users SET active = 0 WHERE user_id = :id", {"id": user_id}
        )
        logger.info("User deactivated: %s", user_id)


_manager: UserManager | None = None


def get_user_manager() -> UserManager:
    global _manager
    if _manager is None:
        _manager = UserManager()
    return _manager
