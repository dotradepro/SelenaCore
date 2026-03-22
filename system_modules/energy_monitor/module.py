"""
system_modules/energy_monitor/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.energy_monitor.energy import EnergyMonitor

logger = logging.getLogger(__name__)


class ReadingRequest(BaseModel):
    device_id: str
    watts: float


class EnergyMonitorModule(SystemModule):
    name = "energy-monitor"

    def __init__(self) -> None:
        super().__init__()
        self._monitor: EnergyMonitor | None = None

    async def start(self) -> None:
        db_path = os.getenv("ENERGY_DB_PATH", ":memory:")
        self._monitor = EnergyMonitor(
            publish_event_cb=self.publish,
            db_path=db_path,
        )
        await self._monitor.start()
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._monitor:
            await self._monitor.stop()
        self._cleanup_subscriptions()
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

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        return router
