"""
system_modules/device_watchdog/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.device_watchdog.watchdog import DeviceWatchdog
from system_modules.device_watchdog.voice_handler import WatchdogVoiceHandler

logger = logging.getLogger(__name__)


class ConfigUpdateRequest(BaseModel):
    check_interval_sec: int | None = None
    ping_timeout_sec: float | None = None
    mqtt_timeout_sec: float | None = None
    protocol_timeout_sec: float | None = None
    offline_threshold: int | None = None
    notify_on_offline: bool | None = None


class DeviceWatchdogModule(SystemModule):
    name = "device-watchdog"

    OWNED_INTENTS = [
        "watchdog.status",
        "watchdog.scan",
    ]

    _OWNED_INTENT_META: dict[str, dict] = {
        "watchdog.status": dict(
            noun_class="DEVICE", verb="query", priority=100,
            description="Report which devices are currently online/offline + last-seen time.",
        ),
        "watchdog.scan": dict(
            noun_class="DEVICE", verb="run", priority=100,
            description="Trigger an immediate liveness scan of every registered device.",
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._watchdog: DeviceWatchdog | None = None
        self._voice: WatchdogVoiceHandler | None = None
        self._config: dict = {
            "check_interval_sec": 60,
            "ping_timeout_sec": 2.0,
            "mqtt_timeout_sec": 120,
            "protocol_timeout_sec": 300,
            "offline_threshold": 3,
            "notify_on_offline": True,
        }

    # speak() is inherited from SystemModule — blocking, waits for TTS to finish

    async def _on_heartbeat(self, event) -> None:
        if self._watchdog:
            await self._watchdog.on_protocol_heartbeat(event.payload)

    async def _on_voice_intent(self, event) -> None:
        """Handle voice.intent events for watchdog.* intents."""
        payload = event.payload if hasattr(event, "payload") else event
        intent = payload.get("intent", "")
        if intent.startswith("watchdog.") and self._voice:
            ctx = await self._voice.handle(intent, payload.get("params", {}))
            if ctx:
                await self.speak_action(intent, ctx)

    async def start(self) -> None:
        self._watchdog = DeviceWatchdog(
            publish_callback=self.publish,
            get_devices_callback=self.fetch_devices,
            update_device_callback=self.patch_device_state,
            config=self._config,
        )
        await self._watchdog.start()
        self._voice = WatchdogVoiceHandler(self)
        self.subscribe(["device.protocol_heartbeat"], self._on_heartbeat)
        self.subscribe(["voice.intent"], self._on_voice_intent)

        # Register watchdog.* intents (static catalog). Idempotent.
        await self._claim_intent_ownership()

        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._watchdog:
            await self._watchdog.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            status = svc._watchdog.get_status_summary() if svc._watchdog else {}
            return {"status": "ok", "module": svc.name, **status}

        @router.post("/scan")
        async def trigger_scan() -> dict:
            if svc._watchdog is None:
                raise HTTPException(503, "Not running")
            result = await svc._watchdog.check_now()
            return {"status": "scan_triggered", **result}

        @router.get("/status")
        async def get_status() -> dict:
            if svc._watchdog is None:
                raise HTTPException(503, "Not running")
            return svc._watchdog.get_status_summary()

        @router.get("/config")
        async def get_config() -> dict:
            return svc._config

        @router.post("/config")
        async def update_config(req: ConfigUpdateRequest) -> dict:
            for k, v in req.model_dump(exclude_none=True).items():
                svc._config[k] = v
            if svc._watchdog:
                svc._watchdog.update_config(svc._config)
            return svc._config

        svc._register_html_routes(router, __file__)
        return router
