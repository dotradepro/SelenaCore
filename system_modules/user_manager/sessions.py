"""
system_modules/user_manager/sessions.py — Temporary browser sessions.

A "browser session" is a short-lived token granted via QR scan.  The browser
works *as* the approving user without registering a new device.

The session expires after ``idle_timeout`` seconds of inactivity (no
heartbeat from the browser).  Each heartbeat resets the timer.

Sessions are persisted to SQLite so they survive container restarts.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_IDLE_TIMEOUT = 300  # 5 minutes of inactivity


@dataclass
class BrowserSession:
    token: str
    user_id: str
    role: str
    display_name: str
    device_name: str          # description ("Chrome via QR")
    created_at: float
    last_activity: float
    idle_timeout: int = _DEFAULT_IDLE_TIMEOUT

    @property
    def expired(self) -> bool:
        return time.time() > self.last_activity + self.idle_timeout


class BrowserSessionManager:
    """Issue and verify temporary browser sessions (QR login)."""

    def __init__(self, db_path: str | None = None) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._cleanup_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._db_path = db_path
        if db_path:
            self._init_db()
            self._load_from_db()

    # ── SQLite persistence ────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    token TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_activity REAL NOT NULL,
                    idle_timeout INTEGER NOT NULL
                )
            """)

    def _load_from_db(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM browser_sessions").fetchall()
        loaded = 0
        for r in rows:
            s = BrowserSession(
                token=r["token"],
                user_id=r["user_id"],
                role=r["role"],
                display_name=r["display_name"],
                device_name=r["device_name"],
                created_at=r["created_at"],
                last_activity=r["last_activity"],
                idle_timeout=r["idle_timeout"],
            )
            if s.expired:
                continue
            self._sessions[s.token] = s
            loaded += 1
        if loaded:
            logger.info("Restored %d browser sessions from DB", loaded)

    def _db_save(self, session: BrowserSession) -> None:
        if not self._db_path:
            return
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO browser_sessions
                   (token, user_id, role, display_name, device_name,
                    created_at, last_activity, idle_timeout)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session.token, session.user_id, session.role,
                 session.display_name, session.device_name,
                 session.created_at, session.last_activity,
                 session.idle_timeout),
            )

    def _db_delete(self, token: str) -> None:
        if not self._db_path:
            return
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("DELETE FROM browser_sessions WHERE token = ?", (token,))

    def _db_sync(self) -> None:
        """Sync all live sessions to DB and remove expired rows."""
        if not self._db_path:
            return
        with sqlite3.connect(self._db_path) as conn:
            live_tokens = set(self._sessions.keys())
            conn.execute("DELETE FROM browser_sessions WHERE token NOT IN ({})".format(
                ",".join("?" for _ in live_tokens)
            ) if live_tokens else "DELETE FROM browser_sessions",
                tuple(live_tokens) if live_tokens else ())
            for s in self._sessions.values():
                conn.execute(
                    """INSERT OR REPLACE INTO browser_sessions
                       (token, user_id, role, display_name, device_name,
                        created_at, last_activity, idle_timeout)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (s.token, s.user_id, s.role, s.display_name,
                     s.device_name, s.created_at, s.last_activity,
                     s.idle_timeout),
                )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_cleanup(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            removed = self._cleanup_expired()
            self._db_sync()
            if removed:
                logger.debug("Cleaned up %d expired browser sessions", removed)

    # ── Public API ────────────────────────────────────────────────────────────

    def grant(
        self,
        user_id: str,
        role: str,
        display_name: str,
        device_name: str = "Browser (QR session)",
        idle_timeout: int = _DEFAULT_IDLE_TIMEOUT,
    ) -> str:
        """Create a new browser session.  Returns the plain token."""
        token = str(uuid.uuid4())
        now = time.time()
        session = BrowserSession(
            token=token,
            user_id=user_id,
            role=role,
            display_name=display_name,
            device_name=device_name,
            created_at=now,
            last_activity=now,
            idle_timeout=idle_timeout,
        )
        self._sessions[token] = session
        self._db_save(session)
        logger.info(
            "Browser session granted for user=%s idle_timeout=%ds",
            user_id, idle_timeout,
        )
        return token

    def verify(self, token: str) -> dict[str, Any] | None:
        """Verify a session token.

        Returns user info dict (same shape as DeviceManager.verify) or None.
        Also updates last_activity (acts as implicit heartbeat).
        """
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expired:
            self._sessions.pop(token, None)
            return None
        session.last_activity = time.time()
        return {
            "device_id": f"session:{session.token[:8]}",
            "user_id": session.user_id,
            "device_name": session.device_name,
            "role": session.role,
            "display_name": session.display_name,
            "session": True,  # marker to distinguish from real devices
        }

    def heartbeat(self, token: str) -> dict[str, Any] | None:
        """Explicit heartbeat — resets idle timer.

        Returns remaining seconds or None if session is expired/invalid.
        """
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expired:
            self._sessions.pop(token, None)
            return None
        session.last_activity = time.time()
        remaining = session.idle_timeout
        return {"remaining": remaining, "idle_timeout": session.idle_timeout}

    def revoke(self, token: str) -> None:
        """Invalidate a session immediately (logout)."""
        self._sessions.pop(token, None)
        self._db_delete(token)
        logger.debug("Browser session revoked: %s...", token[:8])

    def _cleanup_expired(self) -> int:
        expired = [t for t, s in self._sessions.items() if s.expired]
        for t in expired:
            self._sessions.pop(t, None)
        return len(expired)
