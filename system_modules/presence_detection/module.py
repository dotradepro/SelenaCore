"""
system_modules/presence_detection/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.presence_detection.presence import PresenceDetector

logger = logging.getLogger(__name__)


class UserRequest(BaseModel):
    user_id: str
    name: str
    devices: list[dict] = []


class PresenceDetectionModule(SystemModule):
    name = "presence-detection"

    def __init__(self) -> None:
        super().__init__()
        self._detector: PresenceDetector | None = None

    async def _on_state_changed(self, event) -> None:
        """Forward device state changes to the detector."""
        pass  # presence detector handles its own scanning

    async def start(self) -> None:
        self._detector = PresenceDetector(
            publish_event_cb=self.publish,
            scan_interval_sec=int(os.environ.get("PRESENCE_SCAN_INTERVAL", "60")),
            away_threshold_sec=int(os.environ.get("PRESENCE_AWAY_THRESHOLD", "180")),
        )
        await self._detector.start()
        self.subscribe(["device.state_changed"], self._on_state_changed)
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._detector:
            await self._detector.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            status = svc._detector.get_status() if svc._detector else {}
            return {"status": "ok", "module": svc.name, **status}

        @router.get("/status")
        async def get_status() -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            return svc._detector.get_status()

        @router.get("/users")
        async def list_users() -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            return {"users": svc._detector.list_users()}

        @router.post("/users", status_code=201)
        async def add_user(req: UserRequest) -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            result = svc._detector.add_user({"user_id": req.user_id, "name": req.name, "devices": req.devices})
            return result

        @router.get("/users/{user_id}")
        async def get_user(user_id: str) -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            user = svc._detector.get_user(user_id)
            if not user:
                raise HTTPException(404, "User not found")
            return user

        @router.delete("/users/{user_id}", status_code=204, response_class=Response, response_model=None)
        async def remove_user(user_id: str) -> Response:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            svc._detector.remove_user(user_id)
            return Response(status_code=204)

        @router.post("/scan")
        async def trigger_scan() -> dict:
            if svc._detector is None:
                raise HTTPException(503, "Not running")
            results = await svc._detector.trigger_scan_now()
            return {"status": "scan_triggered", "results": results}

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        return router
