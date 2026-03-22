"""
system_modules/import_adapters/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.import_adapters.importer import ImportManager

logger = logging.getLogger(__name__)


class HAImportRequest(BaseModel):
    base_url: str
    token: str
    dry_run: bool = False


class TuyaImportRequest(BaseModel):
    scan_timeout: float = 6.0
    dry_run: bool = False


class HueImportRequest(BaseModel):
    bridge_ip: str
    username: str
    dry_run: bool = False


class ImportAdaptersModule(SystemModule):
    name = "import-adapters"

    def __init__(self) -> None:
        super().__init__()
        self._manager: ImportManager | None = None

    async def start(self) -> None:
        self._manager = ImportManager(
            publish_event_cb=self.publish,
            core_api_url=os.getenv("CORE_API_URL", "http://localhost:7070"),
            module_token=os.getenv("MODULE_TOKEN", ""),
        )
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        self._manager = None
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            return {"status": "ok", "module": svc.name}

        @router.get("/import/status")
        async def get_status() -> JSONResponse:
            if svc._manager is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._manager.get_status())

        @router.get("/import/history")
        async def get_history() -> JSONResponse:
            if svc._manager is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._manager.get_history())

        @router.post("/import/ha")
        async def import_ha(req: HAImportRequest) -> JSONResponse:
            if svc._manager is None:
                raise HTTPException(503, "Not ready")
            result = await svc._manager.import_ha(req.base_url, req.token, req.dry_run)
            return JSONResponse(result)

        @router.post("/import/tuya")
        async def import_tuya(req: TuyaImportRequest) -> JSONResponse:
            if svc._manager is None:
                raise HTTPException(503, "Not ready")
            result = await svc._manager.import_tuya(req.scan_timeout, req.dry_run)
            return JSONResponse(result)

        @router.post("/import/hue")
        async def import_hue(req: HueImportRequest) -> JSONResponse:
            if svc._manager is None:
                raise HTTPException(503, "Not ready")
            result = await svc._manager.import_hue(req.bridge_ip, req.username, req.dry_run)
            return JSONResponse(result)

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        return router
