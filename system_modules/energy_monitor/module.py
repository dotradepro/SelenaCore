"""
system_modules/energy_monitor/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from core.module_loader.system_module import SystemModule
from system_modules.energy_monitor.energy import EnergyMonitor
from system_modules.energy_monitor.voice_handler import EnergyVoiceHandler

logger = logging.getLogger(__name__)


class ReadingRequest(BaseModel):
    device_id: str
    watts: float


class SourceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    type: str = Field(..., pattern=r"^(device_registry|mqtt_topic|manual)$")
    config: dict[str, Any] = Field(default_factory=dict)


class ToggleRequest(BaseModel):
    enabled: bool


class EnergyMonitorModule(SystemModule):
    name = "energy-monitor"

    OWNED_INTENTS = [
        "energy.current",
        "energy.today",
    ]

    _OWNED_INTENT_META: dict[str, dict] = {
        "energy.current": dict(
            noun_class="ENERGY", verb="query", priority=100,
            description="Report current instantaneous power draw (watts) across the home or a named device.",
        ),
        "energy.today": dict(
            noun_class="ENERGY", verb="query", priority=100,
            description="Report total energy consumed today (kWh) across the home or a named device.",
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._monitor: EnergyMonitor | None = None
        self._voice: EnergyVoiceHandler | None = None

    async def start(self) -> None:
        # Persist sources + readings across restarts. Falls back to in-memory
        # only if the data dir is missing (e.g. unit tests).
        default_db = "/var/lib/selena/energy.db"
        try:
            Path(default_db).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            default_db = ":memory:"
        db_path = os.getenv("ENERGY_DB_PATH", default_db)
        self._monitor = EnergyMonitor(
            publish_event_cb=self.publish,
            db_path=db_path,
        )
        await self._monitor.start()
        self._voice = EnergyVoiceHandler(self)

        # Subscribe to dedicated power-meter events for device_registry sources.
        # Energy Monitor never reaches into device.state_changed for power
        # data — any module that owns a metered device publishes
        # device.power_reading on the bus and we consume it here.
        self.subscribe(["device.power_reading"], self._on_device_power_reading)
        # Auto-create / auto-remove device_registry sources when devices
        # appear in / disappear from the registry. The user does not need
        # to manually wire each new device into Energy Monitor.
        self.subscribe(["device.registered", "device.removed"], self._on_device_lifecycle)
        # Subscribe to MQTT data events from protocol_bridge
        self.subscribe(["mqtt.message"], self._on_mqtt_message)
        # Subscribe to voice intents
        self.subscribe(["voice.intent"], self._on_voice_intent)

        # Register energy.* intents (static catalog). Idempotent.
        await self._claim_intent_ownership()

        await self.publish("module.started", {"name": self.name})

    # speak() is inherited from SystemModule — blocking, waits for TTS to finish

    async def _on_voice_intent(self, event: Any) -> None:
        """Handle voice.intent events for energy.* intents."""
        payload = event.payload if hasattr(event, "payload") else event
        intent = payload.get("intent", "")
        if intent.startswith("energy.") and self._voice:
            ctx = await self._voice.handle(intent, payload.get("params", {}))
            if ctx:
                await self.speak_action(intent, ctx)

    async def _join_devices(self) -> list[dict[str, Any]]:
        """Build a unified [device + power + kwh + source] view.

        Reads the Device table from the registry, looks up current power
        and today's kWh from EnergyMonitor, and the source row that
        backs each device (if any). Returns one dict per device,
        suitable for direct table render in the unified settings page.
        """
        import json as _json
        from sqlalchemy import select
        from core.registry.models import Device

        if self._monitor is None:
            return []

        # Index sources by device_id for O(1) lookup
        sources_by_device: dict[str, dict[str, Any]] = {}
        for src in self._monitor.get_sources():
            cfg = src.get("config") or {}
            did = cfg.get("device_id")
            if did:
                sources_by_device[did] = src

        current_power = self._monitor.get_current_power()  # dict[device_id → watts]

        async with self._db_session() as session:
            res = await session.execute(
                select(Device).order_by(Device.location, Device.name)
            )
            devices = list(res.scalars())

        out: list[dict[str, Any]] = []
        for d in devices:
            db_state = _json.loads(d.state) if d.state else {}
            src = sources_by_device.get(d.device_id)
            watts = current_power.get(d.device_id)
            try:
                kwh_today = self._monitor.get_daily_kwh(d.device_id)
            except Exception:
                kwh_today = 0.0
            out.append({
                "device_id": d.device_id,
                "name": d.name,
                "location": d.location or "",
                "entity_type": d.entity_type or "",
                "protocol": d.protocol,
                "enabled": bool(d.enabled),
                "state": db_state,
                "watts": watts,
                "kwh_today": round(kwh_today, 3),
                "source": {
                    "id": src["id"],
                    "enabled": src.get("enabled", True),
                    "last_reading_ts": src.get("last_reading_ts"),
                } if src else None,
            })
        return out

    async def _on_device_lifecycle(self, event: Any) -> None:
        """Auto-create / auto-remove device_registry sources.

        On ``device.registered``: register a new device_registry source
        unless one already exists for this device_id.
        On ``device.removed``: find the matching source and delete it.
        """
        if self._monitor is None:
            return
        payload = event.payload if hasattr(event, "payload") else event
        device_id = payload.get("device_id", "")
        if not device_id:
            return

        if event.type == "device.registered":
            # Skip if a source for this device already exists.
            existing = self._monitor.get_source_device_ids()
            if device_id in existing:
                return
            name = payload.get("name") or device_id
            try:
                self._monitor.add_source(
                    name=name,
                    type="device_registry",
                    config={"device_id": device_id},
                )
                logger.info(
                    "energy-monitor: auto-created source for new device %s (%s)",
                    name, device_id,
                )
            except ValueError as exc:
                logger.warning(
                    "energy-monitor: failed to auto-create source for %s: %s",
                    device_id, exc,
                )

        elif event.type == "device.removed":
            sources = self._monitor.get_sources()
            for s in sources:
                cfg = s.get("config") or {}
                if cfg.get("device_id") == device_id:
                    try:
                        self._monitor.delete_source(s["id"])
                        logger.info(
                            "energy-monitor: auto-removed source %s for deleted device %s",
                            s["id"], device_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "energy-monitor: failed to remove source %s: %s",
                            s["id"], exc,
                        )

    async def _on_device_power_reading(self, event: Any) -> None:
        """Handle device.power_reading bus events — record watts for any
        device_registry source bound to that device."""
        if self._monitor is None:
            return
        payload = event.payload if hasattr(event, "payload") else event
        device_id = payload.get("device_id", "")
        watts = payload.get("watts")
        if not device_id or watts is None:
            return

        source_map = self._monitor.get_source_device_ids()
        if device_id not in source_map:
            return
        source_id = source_map[device_id]

        try:
            await self._monitor.record_reading(device_id, float(watts))
            self._monitor._update_source_ts(source_id)
        except (ValueError, TypeError):
            logger.debug("Non-numeric watts for %s: %s", device_id, watts)

    async def _on_mqtt_message(self, event: Any) -> None:
        """Handle mqtt.message events — match topic to configured MQTT sources."""
        if self._monitor is None:
            return
        payload = event.payload if hasattr(event, "payload") else event
        topic = payload.get("topic", "")

        topic_map = self._monitor.get_source_mqtt_topics()
        if topic not in topic_map:
            return

        info = topic_map[topic]
        state_key = info["state_key"]
        device_id = info["device_id"]
        source_id = info["source_id"]

        msg_data = payload.get("payload") or payload.get("data") or {}
        if isinstance(msg_data, str):
            try:
                import json
                msg_data = json.loads(msg_data)
            except (json.JSONDecodeError, TypeError):
                try:
                    watts = float(msg_data)
                    await self._monitor.record_reading(device_id, watts)
                    self._monitor._update_source_ts(source_id)
                except ValueError:
                    pass
                return

        watts = msg_data.get(state_key) if isinstance(msg_data, dict) else None
        if watts is not None:
            try:
                watts = float(watts)
                await self._monitor.record_reading(device_id, watts)
                self._monitor._update_source_ts(source_id)
            except (ValueError, TypeError):
                pass

    async def stop(self) -> None:
        if self._monitor:
            await self._monitor.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        svc._register_health_endpoint(router)

        @router.post("/energy/reading", status_code=201)
        async def record_reading(req: ReadingRequest) -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            await svc._monitor.record_reading(req.device_id, req.watts)
            return JSONResponse({"ok": True}, status_code=201)

        @router.get("/energy/current")
        async def get_current() -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._monitor.get_current_power())

        @router.get("/energy/today")
        async def get_today() -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse({"total_kwh": svc._monitor.get_total_today_kwh()})

        @router.get("/energy/devices")
        async def get_devices() -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._monitor.get_all_devices())

        @router.get("/energy/devices/{device_id}/history")
        async def get_device_history(device_id: str, limit: int = Query(100, ge=1, le=10000)) -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._monitor.get_device_history(device_id, limit))

        @router.get("/energy/devices/full")
        async def get_devices_full() -> JSONResponse:
            """Unified device list with name + room + type + state + power + kWh.

            Joins the Device registry (owned by device-control) with the
            energy-monitor's per-device current power and today's kWh
            counters. One row per device, ready for the unified settings
            table and the dashboard widget modal.
            """
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse({"devices": await svc._join_devices()})

        @router.get("/energy/status")
        async def get_status() -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._monitor.get_status())

        # ── Data Sources CRUD ─────────────────────────────────────────────

        @router.get("/energy/sources")
        async def get_sources() -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse({"sources": svc._monitor.get_sources()})

        @router.post("/energy/sources", status_code=201)
        async def add_source(req: SourceRequest) -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            try:
                source = svc._monitor.add_source(req.name, req.type, req.config)
            except ValueError as e:
                raise HTTPException(400, str(e))
            return JSONResponse(source, status_code=201)

        @router.delete("/energy/sources/{source_id}")
        async def delete_source(source_id: str) -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            ok = svc._monitor.delete_source(source_id)
            if not ok:
                raise HTTPException(404, "Source not found")
            return JSONResponse({"ok": True})

        @router.patch("/energy/sources/{source_id}/toggle")
        async def toggle_source(source_id: str, req: ToggleRequest) -> JSONResponse:
            if svc._monitor is None:
                raise HTTPException(503, "Not ready")
            ok = svc._monitor.toggle_source(source_id, req.enabled)
            if not ok:
                raise HTTPException(404, "Source not found")
            return JSONResponse({"ok": True, "enabled": req.enabled})

        svc._register_html_routes(router, __file__)
        return router
