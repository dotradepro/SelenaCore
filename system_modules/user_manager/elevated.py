"""
system_modules/user_manager/elevated.py — In-memory elevated sessions.

An "elevated session" is a short-lived token (default TTL 600 s) that grants
access to sensitive operations (settings, user management, …) without requiring
the user to re-enter their PIN on every individual action.

All state is held in-memory — a process restart invalidates all elevated
sessions.  This is intentional: after a reboot the user must re-confirm.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DEFAULT_TTL_SEC = 600   # 10 minutes


@dataclass
class _ElevatedSession:
    token: str
    user_id: str
    expires_at: float


class ElevatedManager:
    """Issue and verify short-lived elevated-access tokens.

    Usage::

        token = manager.grant(user_id)
        ok    = manager.verify(token, user_id)
        manager.revoke(token)
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _ElevatedSession] = {}
        self._cleanup_task: asyncio.Task | None = None  # type: ignore[type-arg]

    def start_cleanup(self) -> None:
        """Start background cleanup loop.  Call once from module.start()."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self) -> None:
        """Cancel the background cleanup loop.  Call from module.stop()."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            removed = self.cleanup_expired()
            if removed:
                logger.debug("Cleaned up %d expired elevated sessions", removed)

    # ── Public API ────────────────────────────────────────────────────────────

    def grant(self, user_id: str, ttl: int = _DEFAULT_TTL_SEC) -> str:
        """Issue a new elevated token for *user_id*.

        Returns the plain token string.  Store it securely on the client
        (e.g. in memory / sessionStorage, NOT localStorage).
        """
        token = str(uuid.uuid4())
        self._sessions[token] = _ElevatedSession(
            token=token,
            user_id=user_id,
            expires_at=time.time() + ttl,
        )
        logger.debug("Elevated session granted for user=%s ttl=%ds", user_id, ttl)
        return token

    def verify(self, token: str, user_id: str) -> bool:
        """Return True if *token* is valid, belongs to *user_id*, and not expired."""
        session = self._sessions.get(token)
        if session is None:
            return False
        if session.user_id != user_id:
            return False
        if time.time() > session.expires_at:
            self._sessions.pop(token, None)
            return False
        return True

    def revoke(self, token: str) -> None:
        """Invalidate an elevated token immediately."""
        self._sessions.pop(token, None)

    def cleanup_expired(self) -> int:
        """Remove all expired sessions.  Returns the number removed."""
        now = time.time()
        expired = [t for t, s in self._sessions.items() if s.expires_at < now]
        for t in expired:
            self._sessions.pop(t, None)
        return len(expired)
