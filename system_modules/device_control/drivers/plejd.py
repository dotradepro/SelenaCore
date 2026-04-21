"""Plejd driver — thin shell over the singleton gateway.

Every Plejd device on the site routes through the one PlejdGateway
persistent GATT connection (the BLE mesh forwards). So the driver
per-device does not own any connection — it just pushes commands into
the shared gateway and subscribes to events filtered by output_address.

``connect()`` deliberately does NOT start the gateway. Gateway lifecycle
is owned by ``DeviceControlModule.start()/stop()`` because it's
process-wide.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator

from ..plejd.gateway import PlejdEvent, get_gateway
from .base import DeviceDriver, DriverError


class PlejdDriver(DeviceDriver):
    """One-per-Device-row wrapper around the shared Plejd gateway."""

    protocol = "plejd_native"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        plejd_meta = (meta or {}).get("plejd") or {}
        try:
            self._output_address = int(plejd_meta["output_address"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DriverError(
                "plejd meta missing output_address — re-run the Plejd import wizard",
            ) from exc
        self._dimmable = bool(plejd_meta.get("dimmable", True))
        self._last_state: dict[str, Any] = {"on": False}
        if self._dimmable:
            self._last_state["brightness"] = 0
        #: Queue of events destined for this output. Gateway's global
        #: subscriber routes here based on ``event.output_address``.
        self._event_q: asyncio.Queue[PlejdEvent] = asyncio.Queue()
        self._subscribed = False

    # ── Helpers ──────────────────────────────────────────────────────

    def _gw(self):
        gw = get_gateway()
        if gw is None:
            raise DriverError(
                "Plejd gateway not running — enable the plejd_native provider "
                "first (Providers tab).",
            )
        return gw

    def _on_gateway_event(self, ev: PlejdEvent) -> None:
        if ev.output_address != self._output_address:
            return
        self._event_q.put_nowait(ev)

    def _ensure_subscribed(self) -> None:
        if self._subscribed:
            return
        self._gw().subscribe(self._on_gateway_event)
        self._subscribed = True

    def _event_to_state(self, ev: PlejdEvent) -> dict[str, Any]:
        state: dict[str, Any] = {"on": bool(ev.on)}
        if self._dimmable and ev.dim_level is not None:
            # Scale 16-bit dim level to 0-255 for the logical model.
            state["brightness"] = max(0, min(255, ev.dim_level >> 8))
        return state

    # ── DeviceDriver API ─────────────────────────────────────────────

    async def connect(self) -> dict[str, Any]:
        """Subscribe to events. Returns the last-known state (empty until
        the mesh pushes an update — Plejd devices don't expose a
        get-state request, only push)."""
        self._gw()   # raises DriverError if the gateway isn't running
        self._ensure_subscribed()
        return dict(self._last_state)

    async def disconnect(self) -> None:
        # Nothing to close per-device; the gateway owns the BLE connection.
        return None

    async def set_state(self, state: dict[str, Any]) -> None:
        gw = self._gw()
        if state.get("on") is False:
            await gw.send_off(self._output_address)
            self._last_state = {**self._last_state, "on": False}
            return
        dim_level: int | None = None
        if "brightness" in state and self._dimmable:
            b = int(state["brightness"])
            b = max(0, min(255, b))
            # Expand 0-255 → 0-65535 for the wire format.
            dim_level = b << 8
            self._last_state = {**self._last_state, "brightness": b}
        if state.get("on", True):
            await gw.send_on(self._output_address, dim_level)
            self._last_state = {**self._last_state, "on": True}

    async def get_state(self) -> dict[str, Any]:
        return dict(self._last_state)

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        self._ensure_subscribed()
        while True:
            ev = await self._event_q.get()
            state = self._event_to_state(ev)
            self._last_state = {**self._last_state, **state}
            yield state
