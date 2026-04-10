"""
core/api/sync_manager.py — Versioned UI state + WebSocket broadcast

Single source of truth for UI-syncable state (settings, layout).
Provides:
  - Monotonic version counter for all state changes
  - Event log (deque) for replay on reconnect
  - WebSocket client registry with ping/pong health check
  - Full snapshot delivery on new connections
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from starlette.websockets import WebSocket

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("CORE_DATA_DIR", "/var/lib/selena"))
_LAYOUT_PATH = _DATA_DIR / "widget_layout.json"
_CONFIG_PATH = Path(os.environ.get("SELENA_CONFIG", "/opt/selena-core/config/core.yaml"))


@dataclass
class SyncEvent:
    version: int
    event_type: str
    payload: dict[str, Any]
    timestamp: float


@dataclass
class _WSClient:
    ws: WebSocket
    client_id: str
    last_pong: float = field(default_factory=time.monotonic)


class SyncManager:
    """Manages versioned UI state and broadcasts to WebSocket clients."""

    def __init__(self) -> None:
        self._version: int = 0
        self._settings: dict[str, Any] = self._load_settings()
        self._layout: dict[str, Any] = self._load_layout()
        self._event_log: deque[SyncEvent] = deque(maxlen=512)
        self._clients: dict[str, _WSClient] = {}
        self._lock = asyncio.Lock()
        self._client_counter = 0
        # Snapshot providers — set via set_snapshot_providers() during startup
        self._devices_fn: Any = None      # async () -> list[dict]
        self._modules_fn: Any = None      # () -> list[dict]
        self._system_fn: Any = None       # async () -> dict
        self._voice_fn: Any = None        # () -> dict

    # ── Initial state loading ──────────────────────────────────────────

    @staticmethod
    def _load_settings() -> dict[str, Any]:
        """Load theme/language from core.yaml."""
        settings: dict[str, Any] = {"theme": "auto", "language": "en"}
        try:
            if _CONFIG_PATH.exists():
                import yaml
                with _CONFIG_PATH.open("r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                sys_cfg = cfg.get("system", {})
                if sys_cfg.get("language"):
                    settings["language"] = sys_cfg["language"]
                ui_cfg = cfg.get("ui", {})
                if ui_cfg.get("theme"):
                    settings["theme"] = ui_cfg["theme"]
        except Exception as exc:
            logger.debug("SyncManager: failed to load settings from core.yaml: %s", exc)
        return settings

    @staticmethod
    def _load_layout() -> dict[str, Any]:
        """Load widget layout from persisted JSON."""
        try:
            if _LAYOUT_PATH.exists():
                return json.loads(_LAYOUT_PATH.read_text())
        except Exception:
            pass
        return {"pinned": [], "sizes": {}}

    # ── Snapshot providers ────────────────────────────────────────────

    def set_snapshot_providers(
        self,
        devices_fn: Any = None,
        modules_fn: Any = None,
        system_fn: Any = None,
        voice_fn: Any = None,
    ) -> None:
        """Set callbacks for enriching the hello snapshot with live data.

        Args:
            devices_fn: async () -> list[dict]  — device registry snapshot
            modules_fn: () -> list[dict]        — module list snapshot
            system_fn:  async () -> dict        — lightweight HW metrics
            voice_fn:   () -> dict              — current voice state
        """
        self._devices_fn = devices_fn
        self._modules_fn = modules_fn
        self._system_fn = system_fn
        self._voice_fn = voice_fn

    # ── Snapshot & replay ──────────────────────────────────────────────

    async def get_snapshot(self) -> dict[str, Any]:
        """Full authoritative state for new connections.

        Includes devices, modules, system metrics, and voice state
        when snapshot providers are configured. Each provider has a 2s
        timeout to avoid blocking WebSocket handshake.
        """
        snapshot: dict[str, Any] = {
            "type": "hello",
            "version": self._version,
            "settings": self._settings.copy(),
            "layout": self._layout.copy(),
        }
        # Enrich with live data (best-effort — failures don't block connect)
        if self._devices_fn:
            try:
                snapshot["devices"] = await asyncio.wait_for(self._devices_fn(), timeout=2.0)
            except Exception as exc:
                logger.debug("Snapshot: devices provider failed: %s", exc)
        if self._modules_fn:
            try:
                snapshot["modules"] = self._modules_fn()
            except Exception as exc:
                logger.debug("Snapshot: modules provider failed: %s", exc)
        if self._system_fn:
            try:
                snapshot["system"] = await asyncio.wait_for(self._system_fn(), timeout=2.0)
            except Exception as exc:
                logger.debug("Snapshot: system provider failed: %s", exc)
        if self._voice_fn:
            try:
                snapshot["voice"] = self._voice_fn()
            except Exception as exc:
                logger.debug("Snapshot: voice provider failed: %s", exc)
        return snapshot

    def get_events_since(self, version: int) -> list[SyncEvent] | None:
        """Return events after given version, or None if too old (need full snapshot)."""
        if not self._event_log:
            return [] if version >= self._version else None
        oldest = self._event_log[0].version
        if version < oldest:
            return None  # Client missed too many events — send full snapshot
        return [e for e in self._event_log if e.version > version]

    # ── State mutations ────────────────────────────────────────────────

    async def publish(self, event_type: str, payload: dict[str, Any]) -> SyncEvent:
        """Increment version, log event, broadcast to all WS clients."""
        async with self._lock:
            self._version += 1
            event = SyncEvent(
                version=self._version,
                event_type=event_type,
                payload=payload,
                timestamp=time.time(),
            )
            self._event_log.append(event)

        # Broadcast to WebSocket clients (fire-and-forget)
        msg = json.dumps({
            "type": "event",
            "version": event.version,
            "event_type": event.event_type,
            "payload": event.payload,
        })
        await self._broadcast_ws(msg)

        # Also broadcast to legacy SSE clients
        from core.api.routes.ui import _broadcast
        _broadcast({"type": event_type, "payload": payload})

        return event

    async def update_settings(self, payload: dict[str, Any]) -> None:
        """Update settings state and publish event."""
        self._settings.update(payload)
        # Persist theme to core.yaml
        if "theme" in payload:
            try:
                from core.config_writer import update_config
                update_config("ui", "theme", payload["theme"])
            except Exception as exc:
                logger.debug("Failed to persist theme: %s", exc)
        await self.publish("settings_changed", payload)

    async def update_layout(self, layout: dict[str, Any]) -> None:
        """Update layout state and publish event."""
        self._layout = layout
        await self.publish("layout_changed", layout)

    # ── WebSocket client management ────────────────────────────────────

    async def register(self, ws: WebSocket) -> str:
        """Register a new WS client, return client_id."""
        self._client_counter += 1
        client_id = f"ws-{self._client_counter}"
        self._clients[client_id] = _WSClient(ws=ws, client_id=client_id)
        logger.debug("SyncManager: client %s connected (total: %d)", client_id, len(self._clients))
        return client_id

    def unregister(self, client_id: str) -> None:
        """Remove a WS client."""
        self._clients.pop(client_id, None)
        logger.debug("SyncManager: client %s disconnected (total: %d)", client_id, len(self._clients))

    def update_pong(self, client_id: str) -> None:
        """Update last pong timestamp for a client."""
        client = self._clients.get(client_id)
        if client:
            client.last_pong = time.monotonic()

    async def send_ping(self, client_id: str) -> bool:
        """Send ping to a specific client. Returns False if send fails."""
        client = self._clients.get(client_id)
        if not client:
            return False
        try:
            msg = json.dumps({"type": "ping", "version": self._version, "ts": time.time()})
            await client.ws.send_text(msg)
            return True
        except Exception:
            return False

    def is_client_stale(self, client_id: str, timeout_sec: float = 10.0) -> bool:
        """Check if client hasn't responded to pong within timeout."""
        client = self._clients.get(client_id)
        if not client:
            return True
        return (time.monotonic() - client.last_pong) > timeout_sec

    async def _broadcast_ws(self, msg: str) -> None:
        """Send message to all connected WS clients."""
        stale: list[str] = []
        for cid, client in list(self._clients.items()):
            try:
                await client.ws.send_text(msg)
            except Exception:
                stale.append(cid)
        for cid in stale:
            self._clients.pop(cid, None)

    @property
    def version(self) -> int:
        return self._version

    @property
    def settings(self) -> dict[str, Any]:
        return self._settings.copy()


# ── Singleton ──────────────────────────────────────────────────────────

_sync_manager: SyncManager | None = None


def get_sync_manager() -> SyncManager:
    global _sync_manager
    if _sync_manager is None:
        _sync_manager = SyncManager()
    return _sync_manager
