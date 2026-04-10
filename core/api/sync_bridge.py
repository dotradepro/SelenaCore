"""
core/api/sync_bridge.py — EventBus → SyncManager bridge

Subscribes to a curated whitelist of EventBus events and forwards them
to SyncManager for delivery to WebSocket/SSE frontend clients.

High-frequency events (device.state_changed, device.power_reading) are
coalesced per entity to avoid flooding the frontend.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive payload keys stripped before forwarding to frontend
_SENSITIVE_KEYS = frozenset({"secret", "token", "password", "api_key", "credential", "access_token", "refresh_token"})


@dataclass
class _ThrottleConfig:
    """Throttle configuration for a single event type."""
    coalesce_key: str          # payload field used as entity key (e.g. "device_id")
    interval_sec: float        # minimum interval between sends for same entity


# Event whitelist: event_type -> ThrottleConfig | None
# None = forward immediately (no throttle)
_WHITELIST: dict[str, _ThrottleConfig | None] = {
    # Devices — state changes throttled per device
    "device.state_changed":  _ThrottleConfig(coalesce_key="device_id", interval_sec=1.0),
    "device.registered":     None,
    "device.removed":        None,
    "device.offline":        None,
    "device.online":         None,

    # Modules — rare events, no throttle
    "module.started":        None,
    "module.stopped":        None,
    "module.removed":        None,
    "module.installed":      None,
    "module.error":          None,

    # Voice — low frequency, forward all
    "voice.wake_word":       None,
    "voice.recognized":      None,
    "voice.intent":          None,
    "voice.speak":           None,
    "voice.speak_done":      None,
    "voice.privacy_on":      None,
    "voice.privacy_off":     None,
    "voice.state":           None,

    # System — already throttled at source (30s)
    "monitor.metrics":       None,
    "monitor.alert":         None,

    # Core — critical, immediate
    "core.integrity_violation":  None,
    "core.integrity_restored":   None,
    "core.safe_mode_entered":    None,
    "core.safe_mode_exited":     None,

    # Energy — throttled per device
    "device.power_reading":  _ThrottleConfig(coalesce_key="device_id", interval_sec=5.0),

    # Notifications
    "notification.sent":     None,
}


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip sensitive keys from payload before forwarding."""
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _SENSITIVE_KEYS:
            continue
        if isinstance(value, dict):
            cleaned[key] = _sanitize_payload(value)
        else:
            cleaned[key] = value
    return cleaned


class SyncBridge:
    """Bridges EventBus events to SyncManager for frontend WebSocket delivery."""

    def __init__(self) -> None:
        self._sync_manager: Any = None
        self._bus: Any = None
        self._sub_id: str | None = None
        self._running = False
        # Throttle state: {(event_type, coalesce_value): (payload, TimerHandle)}
        self._pending: dict[tuple[str, str], tuple[dict[str, Any], asyncio.TimerHandle]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._forwarded_count = 0
        self._throttled_count = 0

    def start(self, bus: Any, sync_manager: Any) -> None:
        """Subscribe to EventBus and start forwarding to SyncManager."""
        self._bus = bus
        self._sync_manager = sync_manager
        self._loop = asyncio.get_event_loop()
        self._running = True

        # Subscribe to all whitelisted event types
        event_types = list(_WHITELIST.keys())
        self._sub_id = bus.subscribe_direct(
            module_id="_sync_bridge",
            event_types=event_types,
            callback=self._on_event,
        )
        logger.info(
            "SyncBridge started: forwarding %d event types to SyncManager",
            len(event_types),
        )

    def stop(self) -> None:
        """Unsubscribe and cancel all pending throttled events."""
        self._running = False
        if self._sub_id and self._bus:
            self._bus.unsubscribe_direct(self._sub_id)
            self._sub_id = None

        # Cancel all pending throttle timers
        for key, (_, handle) in self._pending.items():
            handle.cancel()
        self._pending.clear()

        logger.info(
            "SyncBridge stopped (forwarded=%d, throttled=%d)",
            self._forwarded_count,
            self._throttled_count,
        )

    async def _on_event(self, event: Any) -> None:
        """EventBus callback — filter, throttle, forward to SyncManager."""
        if not self._running or not self._sync_manager:
            return

        event_type: str = event.type
        config = _WHITELIST.get(event_type)

        # Should not happen (we only subscribed to whitelisted types)
        # but guard anyway
        if config is None and event_type not in _WHITELIST:
            return

        payload = _sanitize_payload(event.payload) if event.payload else {}

        if config is None:
            # No throttle — forward immediately
            await self._forward(event_type, payload)
        else:
            # Throttled — coalesce by entity key
            coalesce_value = str(payload.get(config.coalesce_key, "_unknown"))
            throttle_key = (event_type, coalesce_value)

            # Cancel previous pending timer for this entity
            existing = self._pending.get(throttle_key)
            if existing is not None:
                _, old_handle = existing
                old_handle.cancel()
                self._throttled_count += 1

            # Schedule flush after interval
            handle = self._loop.call_later(
                config.interval_sec,
                self._flush_throttled,
                throttle_key,
            )
            self._pending[throttle_key] = (payload, handle)

    def _flush_throttled(self, key: tuple[str, str]) -> None:
        """Timer callback — forward the latest coalesced payload."""
        entry = self._pending.pop(key, None)
        if entry is None:
            return
        payload, _ = entry
        event_type, _ = key
        # Schedule the async forward in the event loop
        asyncio.ensure_future(self._forward(event_type, payload))

    async def _forward(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish event to SyncManager."""
        try:
            await self._sync_manager.publish(event_type, payload)
            self._forwarded_count += 1
        except Exception as exc:
            logger.debug("SyncBridge forward failed for %s: %s", event_type, exc)


# ── Singleton ─────────────────────────────────────────────────────────────

_bridge: SyncBridge | None = None


def get_sync_bridge() -> SyncBridge:
    global _bridge
    if _bridge is None:
        _bridge = SyncBridge()
    return _bridge
