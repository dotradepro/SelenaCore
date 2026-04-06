"""system_modules/update_manager/main.py — FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response

from system_modules.update_manager.updater import UpdateManager, UpdateState

logger = logging.getLogger(__name__)

CORE_URL = os.getenv("CORE_API_URL", "http://localhost")
MODULE_TOKEN = os.getenv("MODULE_TOKEN", "update-manager-token")


async def _publish(event_type: str, payload: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_URL}/api/v1/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"type": event_type, "source": "update-manager", "payload": payload},
            )
    except Exception as exc:
        logger.warning("Failed to publish %s: %s", event_type, exc)


_manager: UpdateManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _manager
    _manager = UpdateManager(
        publish_event_cb=_publish,
        current_version=os.getenv("CURRENT_VERSION", "0.1.0"),
        manifest_url=os.getenv("UPDATE_MANIFEST_URL", ""),
        install_dir=os.getenv("UPDATE_INSTALL_DIR", "/opt/selena-update"),
        backup_dir=os.getenv("UPDATE_BACKUP_DIR", "/opt/selena-backup"),
        check_interval_sec=int(os.getenv("UPDATE_CHECK_INTERVAL", "3600")),
    )
    await _manager.start()
    yield
    await _manager.stop()


app = FastAPI(title="UpdateManager", lifespan=lifespan)


def _req_mgr() -> UpdateManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return _manager


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": "update-manager"}


@app.get("/update/status")
async def get_status() -> JSONResponse:
    return JSONResponse(_req_mgr().get_status())


@app.post("/update/check")
async def check_updates() -> JSONResponse:
    mgr = _req_mgr()
    try:
        result = await mgr.check()
        return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/update/download")
async def download_update() -> JSONResponse:
    mgr = _req_mgr()
    if mgr.state not in (UpdateState.UPDATE_AVAILABLE, UpdateState.UP_TO_DATE):
        # allow re-download
        pass
    try:
        path = await mgr.download()
        return JSONResponse({"ok": True, "path": str(path)})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/update/apply")
async def apply_update() -> JSONResponse:
    mgr = _req_mgr()
    if mgr.state != UpdateState.DOWNLOADED:
        raise HTTPException(status_code=409, detail=f"Cannot apply in state: {mgr.state}")
    try:
        # store path in status for simplicity — in production use shared temp path
        raise HTTPException(status_code=501, detail="Use /update/download first to get package path, then POST /update/apply-path")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/update/rollback")
async def rollback() -> JSONResponse:
    mgr = _req_mgr()
    try:
        await mgr.rollback()
        return JSONResponse({"ok": True, "state": mgr.state.value})
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/widget.html", response_class=HTMLResponse)
async def widget() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.update_manager").joinpath("widget.html")
    return HTMLResponse(path.read_text())


@app.get("/settings.html", response_class=HTMLResponse)
async def settings() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.update_manager").joinpath("settings.html")
    return HTMLResponse(path.read_text())



# System module — loaded in-process by SelenaCore via importlib.
# No standalone entry point needed.
