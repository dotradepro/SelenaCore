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

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        return router
