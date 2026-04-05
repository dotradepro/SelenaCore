"""
system_modules/update_manager/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from core.module_loader.system_module import SystemModule
from system_modules.update_manager.updater import UpdateManager

logger = logging.getLogger(__name__)


class UpdateManagerModule(SystemModule):
    name = "update-manager"

    def __init__(self) -> None:
        super().__init__()
        self._manager: UpdateManager | None = None
        self._downloaded_path: Path | None = None

    async def start(self) -> None:
        self._manager = UpdateManager(
            publish_event_cb=self.publish,
            current_version=os.getenv("CURRENT_VERSION", "0.1.0"),
            manifest_url=os.getenv("UPDATE_MANIFEST_URL", ""),
            install_dir=os.getenv("UPDATE_INSTALL_DIR", "/opt/selena-update"),
            backup_dir=os.getenv("UPDATE_BACKUP_DIR", "/opt/selena-backup"),
            check_interval_sec=int(os.getenv("UPDATE_CHECK_INTERVAL", "3600")),
        )
        await self._manager.start()
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._manager:
            await self._manager.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        def _req() -> UpdateManager:
            if svc._manager is None:
                raise HTTPException(503, "Service not ready")
            return svc._manager

        svc._register_health_endpoint(router)

        @router.get("/update/status")
        async def get_status() -> JSONResponse:
            return JSONResponse(_req().get_status())

        @router.post("/update/check")
        async def check() -> JSONResponse:
            info = await _req().check()
            return JSONResponse(info)

        @router.post("/update/download")
        async def download() -> JSONResponse:
            pkg_path = await _req().download()
            svc._downloaded_path = pkg_path
            return JSONResponse({"ok": True, "path": str(pkg_path)})

        @router.post("/update/apply")
        async def apply() -> JSONResponse:
            if svc._downloaded_path is None or not svc._downloaded_path.exists():
                raise HTTPException(400, "No downloaded package. Run /update/download first.")
            await _req().apply(svc._downloaded_path)
            applied = str(svc._downloaded_path)
            svc._downloaded_path = None
            return JSONResponse({"ok": True, "applied": applied})

        @router.post("/update/rollback")
        async def rollback() -> JSONResponse:
            result = await _req().rollback()
            return JSONResponse(result)

        svc._register_html_routes(router, __file__)
        return router
