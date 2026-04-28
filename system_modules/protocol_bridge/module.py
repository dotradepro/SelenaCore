"""
system_modules/protocol_bridge/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.protocol_bridge.bridge import ProtocolBridge

logger = logging.getLogger(__name__)


class ConfigUpdateRequest(BaseModel):
    mqtt_enabled: bool | None = None
    mqtt_host: str | None = None
    mqtt_port: int | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    zigbee_enabled: bool | None = None
    zigbee_adapter_path: str | None = None
    zwave_enabled: bool | None = None
    http_poll_interval_sec: int | None = None


class ProtocolBridgeModule(SystemModule):
    name = "protocol-bridge"

    def __init__(self) -> None:
        super().__init__()
        self._bridge: ProtocolBridge | None = None
        self._config: dict = {
            "mqtt_enabled": True,
            "mqtt_host": "localhost",
            "mqtt_port": 1883,
            "mqtt_username": None,
            "mqtt_password": None,
            "zigbee_enabled": False,
            "zigbee_adapter_path": "/dev/ttyUSB0",
            "zwave_enabled": False,
            "http_poll_interval_sec": 30,
        }

    async def _on_state_changed(self, event) -> None:
        if self._bridge:
            await self._bridge.on_state_changed(event.payload)

    async def _on_device_command(self, event) -> None:
        if self._bridge:
            await self._bridge.handle_command(event.payload)

    async def start(self) -> None:
        self._bridge = ProtocolBridge(
            config=self._config,
            register_device_cb=self.register_device,
            update_device_state_cb=self.patch_device_state,
            get_devices_cb=self.fetch_devices,
            publish_event_cb=self.publish,
        )
        await self._bridge.start()
        self.subscribe(["device.state_changed"], self._on_state_changed)
        self.subscribe(["device.command"], self._on_device_command)
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._bridge:
            await self._bridge.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            status = svc._bridge.get_status() if svc._bridge else {}
            return {"status": "ok", "module": svc.name, **status}

        @router.get("/status")
        async def get_status() -> dict:
            if svc._bridge is None:
                raise HTTPException(503, "Not running")
            return svc._bridge.get_status()

        @router.get("/config")
        async def get_config() -> dict:
            safe = {**svc._config}
            if safe.get("mqtt_password"):
                safe["mqtt_password"] = "***"
            return safe

        @router.post("/config")
        async def update_config(req: ConfigUpdateRequest) -> dict:
            for k, v in req.model_dump(exclude_none=True).items():
                svc._config[k] = v
            return svc._config

        # ── Dashboard V2 status template ───────────────────────────────────
        @router.get("/widget/data/state")
        async def widget_state() -> dict:
            if svc._bridge is None:
                return {
                    "label": "Protocol bridge",
                    "pill": {"tone": "neutral", "text": "Not running", "icon": "alert-triangle"},
                    "rows": [],
                }
            s = svc._bridge.get_status()
            mqtt = s.get("mqtt", {})
            zigbee = s.get("zigbee", {})
            zwave = s.get("zwave", {})

            mqtt_on = bool(mqtt.get("connected"))
            if not mqtt.get("enabled"):
                pill = {"tone": "neutral", "text": "Disabled", "icon": "clock"}
            elif mqtt_on:
                pill = {"tone": "ok", "text": "Connected", "icon": "check-circle"}
            else:
                pill = {"tone": "warn", "text": "MQTT offline", "icon": "alert-triangle"}

            # Compact icon strip — one entry per protocol, color-coded
            # (green when active, neutral when disabled).
            strip = [
                {
                    "icon": "network",
                    "value": "MQTT",
                    "label": "online" if mqtt_on else "off",
                    "tone": "ok" if mqtt_on else "neutral",
                },
                {
                    "icon": "wifi",
                    "value": "Zigbee",
                    "label": "on" if zigbee.get("enabled") else "off",
                    "tone": "ok" if zigbee.get("enabled") else "neutral",
                },
                {
                    "icon": "radio",
                    "value": "Z-Wave",
                    "label": "on" if zwave.get("enabled") else "off",
                    "tone": "ok" if zwave.get("enabled") else "neutral",
                },
            ]
            rows = [
                {"label": "MQTT host", "value": mqtt.get("host") or "—" if mqtt.get("enabled") else "off", "icon": "server"},
            ]
            return {
                "label": "Protocol bridge",
                "pill": pill,
                "rows": rows,
                "strip": strip,
            }

        svc._register_html_routes(router, __file__)
        return router
