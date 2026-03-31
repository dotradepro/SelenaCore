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

    def __init__(self) -> None:
        super().__init__()
        self._monitor: EnergyMonitor | None = None
        self._voice: EnergyVoiceHandler | None = None

    async def start(self) -> None:
        db_path = os.getenv("ENERGY_DB_PATH", ":memory:")
        self._monitor = EnergyMonitor(
            publish_event_cb=self.publish,
            db_path=db_path,
        )
        await self._monitor.start()
        self._voice = EnergyVoiceHandler(self)

        # Subscribe to device state changes for device_registry sources
        self.subscribe(["device.state_changed"], self._on_device_state_changed)
        # Subscribe to MQTT data events from protocol_bridge
        self.subscribe(["mqtt.message"], self._on_mqtt_message)
        # Subscribe to voice intents
        self.subscribe(["voice.intent"], self._on_voice_intent)

        # Register voice intent patterns with IntentRouter (Tier 1.5)
        try:
            from system_modules.llm_engine.intent_router import get_intent_router
            from .intent_patterns import ENERGY_INTENTS
            intent_router = get_intent_router()
            for entry in ENERGY_INTENTS:
                intent_router.register_system_intent(entry)
            logger.info("EnergyMonitor: registered %d voice intents", len(ENERGY_INTENTS))
        except Exception as exc:
            logger.warning("EnergyMonitor: failed to register intents: %s", exc)

        await self.publish("module.started", {"name": self.name})

    async def speak(self, text: str) -> None:
        """Send text to TTS via EventBus."""
        await self.publish("voice.speak", {"text": text})

    async def _on_voice_intent(self, event: Any) -> None:
        """Handle voice.intent events for energy.* intents."""
        payload = event.payload if hasattr(event, "payload") else event
        intent = payload.get("intent", "")
        if intent.startswith("energy.") and self._voice:
            await self._voice.handle(intent, payload.get("params", {}))

    async def _on_device_state_changed(self, event: Any) -> None:
        """Handle device.state_changed events — extract watts from configured sources."""
        if self._monitor is None:
            return
        payload = event.payload if hasattr(event, "payload") else event
        device_id = payload.get("device_id", "")
        new_state = payload.get("new_state") or payload.get("state") or {}

        source_map = self._monitor.get_source_device_ids()
        if device_id not in source_map:
            return

        source_id = source_map[device_id]
        # Find the state_key for this source
        sources = self._monitor.get_sources()
        state_key = "power"
        for src in sources:
            if src["id"] == source_id:
                state_key = src["config"].get("state_key", "power")
                break

        watts = new_state.get(state_key)
        if watts is None:
            # Try common alternative keys
            for alt_key in ("watts", "power", "watt", "energy_power", "current_power"):
                if alt_key in new_state:
                    watts = new_state[alt_key]
                    break

        if watts is not None:
            try:
                watts = float(watts)
                await self._monitor.record_reading(device_id, watts)
                self._monitor._update_source_ts(source_id)
            except (ValueError, TypeError):
                logger.debug("Non-numeric watts value for %s: %s", device_id, watts)

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
        try:
            from system_modules.llm_engine.intent_router import get_intent_router
            get_intent_router().unregister_system_intents(self.name)
        except Exception:
            pass
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            return {"status": "ok", "module": svc.name}

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

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        return router
