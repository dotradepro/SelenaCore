"""BLE adapter arbiter — one owner at a time, FIFO by request order.

The arbiter serialises access to the BT adapter across heterogeneous
consumers. It has two ticket shapes:

  * **Exclusive slot** (``async with arbiter.slot("scan"):`` …) — the
    caller needs uninterrupted adapter access for a short burst. Queued
    behind any currently-held slot, runs one at a time.

  * **Persistent reservation** (``arbiter.reserve("plejd_gateway")``) —
    a long-lived holder that does not want to be serialised against
    itself. While a persistent reservation is active, new exclusive
    slots from other owners must pause; the gateway re-releases between
    commands so the scanner can squeeze in.

The split matters because the Plejd gateway wants to *hold* a GATT
connection open across minutes while presence_detection does 3-second
scan bursts every 60 s. Without arbitration the scanner routinely
disconnects the gateway, leading to flapping lights.

Implementation is a single asyncio.Lock plus a short FIFO queue of
waiters. The arbiter is **process-wide** — one instance per event loop
via ``get_arbiter()``. Tests spin up their own instance so they don't
pollute each other.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Max time a caller waits for an exclusive slot before giving up. Keep
# this small; callers should always have a retry strategy.
DEFAULT_SLOT_TIMEOUT_S = 30.0


class BLEBusy(RuntimeError):
    """Raised when the caller could not acquire the adapter in time."""


@dataclass
class _Ticket:
    owner: str
    requested_at: float = field(default_factory=time.monotonic)
    granted_at: float | None = None
    released_at: float | None = None
    future: asyncio.Future | None = None


class BLEArbiter:
    """Cooperative BLE adapter serializer.

    Fair-FIFO ordering: the first caller to request a slot is the first
    one granted. A persistent reservation counts as one continuous
    owner — releasing it wakes the next queued exclusive waiter.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._queue: deque[_Ticket] = deque()
        self._current: _Ticket | None = None

    # ── Exclusive slot ─────────────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def slot(
        self, owner: str, *, timeout: float = DEFAULT_SLOT_TIMEOUT_S,
    ) -> AsyncIterator[None]:
        """Acquire the adapter exclusively for the duration of the block.

        Waits up to ``timeout`` seconds. Raises ``BLEBusy`` on timeout.
        """
        ticket = _Ticket(owner=owner)
        await self._acquire(ticket, timeout=timeout)
        try:
            yield
        finally:
            self._release(ticket)

    async def _acquire(self, ticket: _Ticket, *, timeout: float) -> None:
        loop = asyncio.get_event_loop()
        # Fast path: no current owner and empty queue → grant immediately.
        if self._current is None and not self._queue:
            self._current = ticket
            ticket.granted_at = time.monotonic()
            return

        future = loop.create_future()
        ticket.future = future
        self._queue.append(ticket)
        try:
            await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            # Drop our own ticket out of the queue so an eventual grant
            # doesn't wake a cancelled caller.
            try:
                self._queue.remove(ticket)
            except ValueError:
                pass
            raise BLEBusy(
                f"BLE arbiter: {owner_of(ticket)!r} timed out after "
                f"{timeout:.1f}s (current={owner_of(self._current)!r})",
            ) from exc

    def _release(self, ticket: _Ticket) -> None:
        ticket.released_at = time.monotonic()
        if self._current is not ticket:
            # Defensive — can only happen if a caller releases someone
            # else's ticket (which we never expose publicly).
            return
        self._current = None
        # Wake the next waiter, if any.
        while self._queue:
            nxt = self._queue.popleft()
            if nxt.future is None or nxt.future.done():
                # Cancelled while waiting — skip.
                continue
            self._current = nxt
            nxt.granted_at = time.monotonic()
            nxt.future.set_result(None)
            return

    # ── Persistent reservation ─────────────────────────────────────────

    @contextlib.asynccontextmanager
    async def reserve(
        self, owner: str, *, timeout: float = DEFAULT_SLOT_TIMEOUT_S,
    ) -> AsyncIterator["_Reservation"]:
        """Hold the adapter for a long time, but yield it on request.

        Usage:
            async with arbiter.reserve("plejd") as res:
                while running:
                    await open_gatt()
                    try:
                        await ...
                    finally:
                        async with res.lease():
                            pass   # let pending scans run

        The returned ``_Reservation`` exposes ``lease()`` — a bounded
        release point where queued callers can run. This keeps the
        gateway in charge of *when* it pauses, avoiding mid-transmission
        disconnects.
        """
        reservation = _Reservation(self, owner)
        await self._acquire(reservation._ticket, timeout=timeout)
        try:
            yield reservation
        finally:
            self._release(reservation._ticket)

    # ── Introspection (for /gateway/status and debug) ──────────────────

    def current_owner(self) -> str | None:
        return owner_of(self._current)

    def queue_depth(self) -> int:
        return len(self._queue)

    def queued_owners(self) -> list[str]:
        return [t.owner for t in self._queue]


def owner_of(t: _Ticket | None) -> str | None:
    return t.owner if t is not None else None


class _Reservation:
    """Handle for a persistent reservation.

    Constructed internally by ``BLEArbiter.reserve``. The gateway calls
    ``lease()`` periodically between its own GATT commands to give
    queued scan bursts a chance.
    """

    def __init__(self, arbiter: BLEArbiter, owner: str) -> None:
        self._arbiter = arbiter
        self.owner = owner
        self._ticket = _Ticket(owner=owner)

    @contextlib.asynccontextmanager
    async def lease(self) -> AsyncIterator[None]:
        """Release the reservation just long enough for queued slots to
        run, then re-acquire. If nothing is queued the lease is a no-op
        (no adapter churn)."""
        if not self._arbiter._queue:
            yield
            return
        # Release -> let others run -> re-acquire (append at tail so we
        # don't starve callers that arrived during the lease).
        self._arbiter._release(self._ticket)
        try:
            await asyncio.sleep(0)    # yield to scheduler
            # Spin until the queue drains once. That's typically a single
            # scan burst (≤ 5 s).
            while self._arbiter._current is not None and self._arbiter._current is not self._ticket:
                await asyncio.sleep(0.05)
        finally:
            # Re-acquire at the tail.
            self._ticket = _Ticket(owner=self.owner)
            await self._arbiter._acquire(self._ticket, timeout=DEFAULT_SLOT_TIMEOUT_S)
            yield


# ── Process singleton ─────────────────────────────────────────────────


_DEFAULT: BLEArbiter | None = None


def get_arbiter() -> BLEArbiter:
    """Return the process-wide arbiter instance, creating it on first use.

    Safe to call before any event loop is running — the arbiter itself
    is event-loop-free until someone acquires a slot.
    """
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = BLEArbiter()
    return _DEFAULT


def reset_arbiter() -> None:
    """Drop the singleton — tests only."""
    global _DEFAULT
    _DEFAULT = None
