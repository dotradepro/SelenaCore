"""system_modules/update_manager/main.py — FastAPI entry point.

Used for standalone testing. The production deployment loads
:class:`system_modules.update_manager.module.UpdateManagerModule` in-process
via the system module loader; this file mirrors that router so the same
endpoint contract can be exercised from a plain ``uvicorn`` invocation.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from system_modules.update_manager.updater import UpdateManager, VALID_CHANNELS

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
        repo=os.getenv("UPDATE_REPO", "dotradepro/SelenaCore"),
        channel=os.getenv("UPDATE_CHANNEL", "rc"),
        install_dir=os.getenv("UPDATE_INSTALL_DIR", "/opt/selena-core"),
        backup_dir=os.getenv("UPDATE_BACKUP_DIR", "/opt/selena-backup"),
        check_interval_sec=int(os.getenv("UPDATE_CHECK_INTERVAL", "21600")),
    )
    await _manager.start()
    try:
        yield
    finally:
        await _manager.stop()


app = FastAPI(title="UpdateManager", lifespan=lifespan)


def _req_mgr() -> UpdateManager:
    if _manager is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return _manager


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": "update-manager"}


@app.get("/status")
async def get_status() -> JSONResponse:
    return JSONResponse(_req_mgr().get_status())


@app.post("/check")
async def check_updates() -> JSONResponse:
    try:
        result = await _req_mgr().check()
        return JSONResponse(result)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/versions")
async def list_versions() -> JSONResponse:
    return JSONResponse({"versions": _req_mgr().list_versions()})


@app.get("/version/{tag}")
async def get_version(tag: str) -> JSONResponse:
    details = _req_mgr().get_version_details(tag)
    if details is None:
        raise HTTPException(404, f"version not found: {tag}")
    return JSONResponse(details)


@app.post("/install")
async def install(payload: dict = Body(...)) -> JSONResponse:
    tag = (payload or {}).get("tag")
    if not tag or not isinstance(tag, str):
        raise HTTPException(400, "tag is required")
    try:
        return JSONResponse(await _req_mgr().install_version(tag))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/rollback")
async def rollback() -> JSONResponse:
    try:
        return JSONResponse(await _req_mgr().rollback())
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/config")
async def set_config(payload: dict = Body(...)) -> JSONResponse:
    mgr = _req_mgr()
    payload = payload or {}
    if "channel" in payload:
        ch = payload["channel"]
        if ch not in VALID_CHANNELS:
            raise HTTPException(400, f"invalid channel: {ch!r}")
        mgr.set_channel(ch)
    if "auto_check" in payload:
        mgr.set_auto_check(bool(payload["auto_check"]))
    if "check_interval_sec" in payload:
        try:
            mgr.set_check_interval(int(payload["check_interval_sec"]))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return JSONResponse(mgr.get_status())


@app.get("/log")
async def get_log(tag: str | None = None, lines: int = 200) -> JSONResponse:
    return JSONResponse({"log": _req_mgr().get_apply_log(tag=tag, max_lines=lines)})


def _read_html(name: str) -> str:
    path = Path(__file__).parent / name
    return path.read_text() if path.exists() else f"<p>{name} not found</p>"


@app.get("/widget.html", response_class=HTMLResponse)
async def widget() -> HTMLResponse:
    return HTMLResponse(_read_html("widget.html"))


@app.get("/settings.html", response_class=HTMLResponse)
async def settings() -> HTMLResponse:
    return HTMLResponse(_read_html("settings.html"))
