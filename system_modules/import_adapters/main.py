"""system_modules/import_adapters/main.py — FastAPI entry point for import-adapters module."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from system_modules.import_adapters.importer import ImportManager

logger = logging.getLogger(__name__)

CORE_URL = os.getenv("CORE_API_URL", "http://localhost:7070")
MODULE_TOKEN = os.getenv("MODULE_TOKEN", "import-adapters-token")


async def _publish(event_type: str, payload: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_URL}/api/v1/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"type": event_type, "source": "import-adapters", "payload": payload},
            )
    except Exception as exc:
        logger.warning("Failed to publish %s: %s", event_type, exc)


_manager: ImportManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _manager
    _manager = ImportManager(
        publish_event_cb=_publish,
        core_api_url=CORE_URL,
        module_token=MODULE_TOKEN,
    )
    yield


app = FastAPI(title="ImportAdapters", lifespan=lifespan)


def _req_mgr() -> ImportManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return _manager


# ── Request models ─────────────────────────────────────────────────────────────

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


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": "import-adapters"}


@app.get("/import/status")
async def status() -> JSONResponse:
    return JSONResponse(_req_mgr().get_status())


@app.get("/import/history")
async def history() -> JSONResponse:
    return JSONResponse(_req_mgr().get_history())


@app.post("/import/ha")
async def import_ha(req: HAImportRequest) -> JSONResponse:
    mgr = _req_mgr()
    try:
        session = await mgr.import_ha(req.base_url, req.token, dry_run=req.dry_run)
        return JSONResponse({
            "session_id": session.session_id,
            "source": session.source.value,
            "imported_count": session.imported_count,
            "status": session.status.value,
        })
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/import/tuya")
async def import_tuya(req: TuyaImportRequest) -> JSONResponse:
    mgr = _req_mgr()
    try:
        session = await mgr.import_tuya(scan_timeout=req.scan_timeout, dry_run=req.dry_run)
        return JSONResponse({
            "session_id": session.session_id,
            "source": session.source.value,
            "imported_count": session.imported_count,
            "status": session.status.value,
        })
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/import/hue")
async def import_hue(req: HueImportRequest) -> JSONResponse:
    mgr = _req_mgr()
    try:
        session = await mgr.import_hue(req.bridge_ip, req.username, dry_run=req.dry_run)
        return JSONResponse({
            "session_id": session.session_id,
            "source": session.source.value,
            "imported_count": session.imported_count,
            "status": session.status.value,
        })
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/widget.html", response_class=HTMLResponse)
async def widget() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.import_adapters").joinpath("widget.html")
    return HTMLResponse(path.read_text())


@app.get("/settings.html", response_class=HTMLResponse)
async def settings() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.import_adapters").joinpath("settings.html")
    return HTMLResponse(path.read_text())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("system_modules.import_adapters.main:app", host="0.0.0.0", port=8117, reload=False)
