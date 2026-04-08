"""
system_modules/device_control/drivers/base.py — Driver ABC.

A driver translates SelenaCore's logical state model
({"on": bool, "brightness": int, ...}) into protocol-specific commands
(Tuya DPS values, MQTT topic publishes, HTTP REST calls).

Every driver MUST be push-based, not poll-based:
  - ``connect()`` performs the initial read-back and opens any persistent
    connection (TCP socket, MQTT subscription, Pulsar listener).
  - ``stream_events()`` is an async generator that yields a logical state
    dict whenever the device announces a change. The watcher in
    ``DeviceControlModule._watch_device`` reads it in a loop until the
    generator raises ``DriverError``, then reconnects with backoff.

Sync libraries (tinytuya) are wrapped via ``asyncio.to_thread`` so the event
loop is never blocked.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator


class DriverError(Exception):
    """Raised on any driver fault — triggers reconnect with backoff."""


class DeviceDriver(ABC):
    """Abstract base class for all device drivers."""

    #: Stable string used in ``Device.protocol`` and the ``DRIVERS`` registry.
    protocol: str = ""

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        """``device_id`` is the SelenaCore Device PK; ``meta`` is the JSON
        blob from ``Device.meta`` — driver-specific (e.g. ``meta["tuya"]``)."""
        self.device_id = device_id
        self.meta = meta

    @abstractmethod
    async def connect(self) -> dict[str, Any]:
        """Open connection and return the initial logical state.

        Raises ``DriverError`` on failure.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Close any persistent resources. Idempotent."""

    @abstractmethod
    async def set_state(self, state: dict[str, Any]) -> None:
        """Apply a partial state update to the device.

        ``state`` uses logical keys (``on``, ``brightness``, ...).
        Raises ``DriverError`` on failure.
        """

    @abstractmethod
    async def get_state(self) -> dict[str, Any]:
        """Return current logical state without using the persistent stream."""

    @abstractmethod
    def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator yielding logical state dicts as the device pushes them.

        Should run forever; ``DriverError`` ends the stream and triggers
        reconnect in the watcher loop.
        """

    def consume_metering(self) -> dict[str, float] | None:
        """Pop the latest power-metering snapshot, if any.

        Drivers for metered devices (smart plugs, energy clamps) override
        this to expose ``{"watts": float, "volts": float?, "amps": float?}``
        captured from the most recent push frame. Called by the watcher
        right after each ``stream_events()`` yield; the watcher publishes
        ``device.power_reading`` on the EventBus when this returns data.

        Default: no metering. One-shot — implementations should clear the
        cached snapshot on read so each frame produces at most one event.
        """
        return None
