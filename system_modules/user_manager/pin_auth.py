"""
system_modules/user_manager/pin_auth.py — PIN authentication + rate limiting

Security rules:
  - Max 5 failed attempts per user
  - After 5 failures: 10-minute lock
  - Lock state is in-memory (resets on restart, acceptable for home device)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
LOCK_DURATION_SEC = 600  # 10 minutes


def _hash_pin(pin: str) -> str:
    salt = "selena-pin-salt-v1"
    return hashlib.sha256(f"{salt}{pin}".encode()).hexdigest()


@dataclass
class LockState:
    attempts: int = 0
    locked_until: float = 0.0
    last_attempt: float = 0.0


class PinAuthManager:
    """PIN authentication with brute-force protection."""

    def __init__(self) -> None:
        self._lock_states: dict[str, LockState] = {}  # user_id → state
        self._mu = asyncio.Lock()

    def _get_state(self, user_id: str) -> LockState:
        if user_id not in self._lock_states:
            self._lock_states[user_id] = LockState()
        return self._lock_states[user_id]

    def is_locked(self, user_id: str) -> bool:
        state = self._get_state(user_id)
        if state.locked_until > time.time():
            return True
        if state.locked_until > 0 and time.time() >= state.locked_until:
            # Lock expired — reset
            state.attempts = 0
            state.locked_until = 0.0
        return False

    def lock_remaining_sec(self, user_id: str) -> int:
        state = self._get_state(user_id)
        remaining = state.locked_until - time.time()
        return max(0, int(remaining))

    async def authenticate(
        self, user_id: str, pin: str, stored_pin_hash: str
    ) -> tuple[bool, str]:
        """Verify PIN. Returns (success, message).

        Raises no exceptions — all errors returned as (False, reason).
        """
        async with self._mu:
            state = self._get_state(user_id)
            now = time.time()

            # Check lock
            if state.locked_until > now:
                remaining = int(state.locked_until - now)
                return False, f"Account locked. Try again in {remaining} seconds."

            # Verify PIN
            submitted_hash = _hash_pin(pin)
            if submitted_hash == stored_pin_hash:
                # Success — reset attempts
                state.attempts = 0
                state.locked_until = 0.0
                logger.info("PIN auth success for user %s", user_id)
                return True, "ok"

            # Failed attempt
            state.attempts += 1
            state.last_attempt = now

            if state.attempts >= MAX_ATTEMPTS:
                state.locked_until = now + LOCK_DURATION_SEC
                logger.warning(
                    "User %s locked for %d seconds after %d failed PIN attempts",
                    user_id, LOCK_DURATION_SEC, MAX_ATTEMPTS
                )
                return False, f"Account locked for {LOCK_DURATION_SEC // 60} minutes after too many failed attempts."

            remaining_attempts = MAX_ATTEMPTS - state.attempts
            logger.warning("PIN auth failed for user %s (%d attempts left)", user_id, remaining_attempts)
            return False, f"Incorrect PIN. {remaining_attempts} attempts remaining."

    def reset_lock(self, user_id: str) -> None:
        """Admin reset of lock state."""
        state = self._get_state(user_id)
        state.attempts = 0
        state.locked_until = 0.0
        logger.info("Lock reset for user %s", user_id)


_pin_auth: PinAuthManager | None = None


def get_pin_auth() -> PinAuthManager:
    global _pin_auth
    if _pin_auth is None:
        _pin_auth = PinAuthManager()
    return _pin_auth
