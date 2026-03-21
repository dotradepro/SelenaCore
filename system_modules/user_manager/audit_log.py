"""
system_modules/user_manager/audit_log.py — Audit log for user actions

Stores all security-relevant events (login, state changes, etc.)
Max 10,000 records with oldest truncation.
All writes are append-only; reads return newest-first.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("SELENA_DB_URL", "sqlite+aiosqlite:///var/lib/selena/selena.db")
MAX_AUDIT_RECORDS = 10_000


class AuditLogger:
    def __init__(self, db_url: str = DB_URL) -> None:
        self._db_url = db_url
        self._engine: AsyncEngine | None = None

    async def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(self._db_url, echo=False)
            await self._ensure_table()
        return self._engine

    async def _ensure_table(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    user_id TEXT,
                    action TEXT NOT NULL,
                    resource TEXT,
                    result TEXT NOT NULL DEFAULT 'ok',
                    ip_address TEXT,
                    details TEXT
                )
            """))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp)"
            ))

    async def log(
        self,
        action: str,
        user_id: str | None = None,
        resource: str | None = None,
        result: str = "ok",
        ip_address: str | None = None,
        details: str | None = None,
    ) -> None:
        engine = await self._get_engine()
        record_id = str(uuid.uuid4())
        now = time.time()
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO audit_log (id, timestamp, user_id, action, resource, result, ip_address, details)
                VALUES (:id, :timestamp, :user_id, :action, :resource, :result, :ip_address, :details)
            """), {
                "id": record_id, "timestamp": now, "user_id": user_id,
                "action": action, "resource": resource, "result": result,
                "ip_address": ip_address, "details": details,
            })
            # Trim oldest records
            await conn.execute(text("""
                DELETE FROM audit_log
                WHERE id NOT IN (
                    SELECT id FROM audit_log ORDER BY timestamp DESC LIMIT :max
                )
            """), {"max": MAX_AUDIT_RECORDS})

    async def get_recent(self, limit: int = 100, user_id: str | None = None) -> list[dict]:
        engine = await self._get_engine()
        async with engine.connect() as conn:
            if user_id:
                result = await conn.execute(text("""
                    SELECT * FROM audit_log WHERE user_id = :uid
                    ORDER BY timestamp DESC LIMIT :limit
                """), {"uid": user_id, "limit": limit})
            else:
                result = await conn.execute(text("""
                    SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT :limit
                """), {"limit": limit})
            return [dict(row._mapping) for row in result.fetchall()]


_audit: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    global _audit
    if _audit is None:
        _audit = AuditLogger()
    return _audit
