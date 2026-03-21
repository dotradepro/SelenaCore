"""
system_modules/voice_core/voice_history.py — Voice interaction history in SQLite

Schema:
  voice_history(id, timestamp, user_id, wake_word, recognized_text, intent, response, duration_ms)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)

MAX_RECORDS = 10_000


@dataclass
class VoiceRecord:
    timestamp: float
    user_id: str | None
    wake_word: str
    recognized_text: str
    intent: str | None
    response: str | None
    duration_ms: int


class VoiceHistory:
    """SQLite-backed voice interaction history."""

    def __init__(self, db_path: str = "/var/lib/selena/selena.db") -> None:
        self._db_path = db_path
        self._lock = asyncio.Lock()
        self._engine = None

    async def _get_engine(self):
        if self._engine is None:
            from sqlalchemy.ext.asyncio import create_async_engine
            self._engine = create_async_engine(
                f"sqlite+aiosqlite:///{self._db_path}", echo=False
            )
            await self._ensure_table()
        return self._engine

    async def _ensure_table(self) -> None:
        engine = self._engine
        async with engine.begin() as conn:
            await conn.execute(__import__("sqlalchemy").text("""
                CREATE TABLE IF NOT EXISTS voice_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    user_id TEXT,
                    wake_word TEXT NOT NULL DEFAULT '',
                    recognized_text TEXT NOT NULL DEFAULT '',
                    intent TEXT,
                    response TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0
                )
            """))
            await conn.execute(__import__("sqlalchemy").text(
                "CREATE INDEX IF NOT EXISTS idx_vh_timestamp ON voice_history(timestamp)"
            ))

    async def add(self, record: VoiceRecord) -> None:
        """Add a voice record and trim if over MAX_RECORDS."""
        from sqlalchemy import text
        engine = await self._get_engine()
        async with self._lock:
            async with engine.begin() as conn:
                await conn.execute(text("""
                    INSERT INTO voice_history
                        (timestamp, user_id, wake_word, recognized_text, intent, response, duration_ms)
                    VALUES (:timestamp, :user_id, :wake_word, :recognized_text, :intent, :response, :duration_ms)
                """), asdict(record))
                # Trim oldest records
                await conn.execute(text("""
                    DELETE FROM voice_history
                    WHERE id NOT IN (
                        SELECT id FROM voice_history ORDER BY timestamp DESC LIMIT :max_records
                    )
                """), {"max_records": MAX_RECORDS})

    async def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent voice records."""
        from sqlalchemy import text
        engine = await self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT id, timestamp, user_id, wake_word, recognized_text, intent, response, duration_ms
                FROM voice_history
                ORDER BY timestamp DESC
                LIMIT :limit
            """), {"limit": limit})
            rows = result.fetchall()
        return [dict(row._mapping) for row in rows]

    async def get_by_user(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return voice records for a specific user."""
        from sqlalchemy import text
        engine = await self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(text("""
                SELECT id, timestamp, user_id, wake_word, recognized_text, intent, response, duration_ms
                FROM voice_history
                WHERE user_id = :user_id
                ORDER BY timestamp DESC
                LIMIT :limit
            """), {"user_id": user_id, "limit": limit})
            rows = result.fetchall()
        return [dict(row._mapping) for row in rows]


_history: VoiceHistory | None = None


def get_voice_history() -> VoiceHistory:
    global _history
    if _history is None:
        _history = VoiceHistory()
    return _history
