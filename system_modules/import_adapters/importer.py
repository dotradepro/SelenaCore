"""
system_modules/import_adapters/importer.py — ImportManager

Orchestrates device import from HA / Tuya / Hue:
  1. Run the appropriate adapter
  2. Publish import.started → import.progress → import.completed events
  3. Return imported device list

Events published:
  import.started     — source, session_id
  import.progress    — session_id, done, total
  import.completed   — session_id, source, imported_count
  import.failed      — session_id, source, error
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# Top-level imports so patch() can intercept them in tests
from system_modules.import_adapters.ha_adapter import HomeAssistantAdapter  # noqa: E402
from system_modules.import_adapters.tuya_adapter import TuyaAdapter  # noqa: E402
from system_modules.import_adapters.hue_adapter import HueAdapter, HueBridge  # noqa: E402


class ImportSource(str, Enum):
    HOME_ASSISTANT = "home_assistant"
    TUYA = "tuya"
    PHILIPS_HUE = "philips_hue"


class ImportStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ImportSession:
    session_id: str
    source: ImportSource
    status: ImportStatus = ImportStatus.RUNNING
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    imported_count: int = 0
    error: str | None = None
    devices: list[dict[str, Any]] = field(default_factory=list)


class ImportManager:
    """Runs import flows and tracks session history."""

    def __init__(
        self,
        publish_event_cb: Any,
        core_api_url: str = "http://localhost:7070",
        module_token: str = "",
    ) -> None:
        self._publish = publish_event_cb
        self._core_api_url = core_api_url
        self._module_token = module_token
        self._current: ImportSession | None = None
        self._history: deque[ImportSession] = deque(maxlen=20)

    # ── Public state ───────────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        cur = self._current
        return {
            "status": cur.status.value if cur else ImportStatus.IDLE.value,
            "session_id": cur.session_id if cur else None,
            "source": cur.source.value if cur else None,
            "imported_count": cur.imported_count if cur else 0,
            "error": cur.error if cur else None,
        }

    def get_history(self) -> list[dict[str, Any]]:
        return [
            {
                "session_id": s.session_id,
                "source": s.source.value,
                "status": s.status.value,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "imported_count": s.imported_count,
                "error": s.error,
            }
            for s in reversed(self._history)
        ]

    # ── Import flows ───────────────────────────────────────────────────────────

    async def import_ha(self, base_url: str, token: str, dry_run: bool = False) -> ImportSession:
        adapter = HomeAssistantAdapter(base_url, token)
        session = self._start_session(ImportSource.HOME_ASSISTANT)
        try:
            await self._publish("import.started", {
                "session_id": session.session_id, "source": session.source.value
            })
            entities = await adapter.get_entities()
            devices = adapter.to_selena_devices(entities)
            await self._publish("import.progress", {
                "session_id": session.session_id, "done": len(devices), "total": len(devices)
            })
            if not dry_run:
                await self._register_devices(devices, session)
            else:
                session.devices = devices
                session.imported_count = len(devices)
            self._finish_session(session, ImportStatus.COMPLETED)
            await self._publish("import.completed", {
                "session_id": session.session_id,
                "source": session.source.value,
                "imported_count": session.imported_count,
            })
        except Exception as exc:
            self._fail_session(session, str(exc))
            await self._publish("import.failed", {
                "session_id": session.session_id,
                "source": session.source.value,
                "error": str(exc),
            })
            raise
        return session

    async def import_tuya(self, scan_timeout: float = 6.0, dry_run: bool = False) -> ImportSession:
        adapter = TuyaAdapter()
        session = self._start_session(ImportSource.TUYA)
        try:
            await self._publish("import.started", {
                "session_id": session.session_id, "source": session.source.value
            })
            tuya_devices = await adapter.scan_network(timeout=scan_timeout)
            devices = adapter.to_selena_devices(tuya_devices)
            await self._publish("import.progress", {
                "session_id": session.session_id, "done": len(devices), "total": len(devices)
            })
            if not dry_run:
                await self._register_devices(devices, session)
            else:
                session.devices = devices
                session.imported_count = len(devices)
            self._finish_session(session, ImportStatus.COMPLETED)
            await self._publish("import.completed", {
                "session_id": session.session_id,
                "source": session.source.value,
                "imported_count": session.imported_count,
            })
        except Exception as exc:
            self._fail_session(session, str(exc))
            await self._publish("import.failed", {
                "session_id": session.session_id,
                "source": session.source.value,
                "error": str(exc),
            })
            raise
        return session

    async def import_hue(self, bridge_ip: str, username: str, dry_run: bool = False) -> ImportSession:
        bridge = HueBridge(bridge_id="", ip=bridge_ip, username=username)
        adapter = HueAdapter(bridge)
        session = self._start_session(ImportSource.PHILIPS_HUE)
        try:
            await self._publish("import.started", {
                "session_id": session.session_id, "source": session.source.value
            })
            lights = await adapter.get_lights()
            devices = adapter.to_selena_devices(lights)
            await self._publish("import.progress", {
                "session_id": session.session_id, "done": len(devices), "total": len(devices)
            })
            if not dry_run:
                await self._register_devices(devices, session)
            else:
                session.devices = devices
                session.imported_count = len(devices)
            self._finish_session(session, ImportStatus.COMPLETED)
            await self._publish("import.completed", {
                "session_id": session.session_id,
                "source": session.source.value,
                "imported_count": session.imported_count,
            })
        except Exception as exc:
            self._fail_session(session, str(exc))
            await self._publish("import.failed", {
                "session_id": session.session_id,
                "source": session.source.value,
                "error": str(exc),
            })
            raise
        return session

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _start_session(self, source: ImportSource) -> ImportSession:
        session = ImportSession(session_id=str(uuid.uuid4()), source=source)
        self._current = session
        return session

    def _finish_session(self, session: ImportSession, status: ImportStatus) -> None:
        session.status = status
        session.finished_at = datetime.now(timezone.utc).isoformat()
        self._history.append(session)

    def _fail_session(self, session: ImportSession, error: str) -> None:
        session.error = error
        self._finish_session(session, ImportStatus.FAILED)

    async def _register_devices(self, devices: list[dict[str, Any]], session: ImportSession) -> None:
        """POST each device to the Core API Device Registry."""
        import httpx
        headers = {"Authorization": f"Bearer {self._module_token}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            for dev in devices:
                try:
                    # Build registry-compatible payload
                    payload = {
                        "name": dev.get("name", "Imported Device"),
                        "type": dev.get("device_type", "sensor"),
                        "protocol": dev.get("protocol", "unknown"),
                        "capabilities": [],
                        "meta": dev.get("meta", {}),
                    }
                    resp = await client.post(
                        f"{self._core_api_url}/api/v1/devices",
                        headers=headers,
                        json=payload,
                    )
                    if resp.status_code == 201:
                        session.imported_count += 1
                        session.devices.append(dev)
                    else:
                        logger.warning(
                            "Failed to register device %r: %s %s",
                            dev.get("name"), resp.status_code, resp.text,
                        )
                except Exception as exc:
                    logger.warning("Error registering device %r: %s", dev.get("name"), exc)
