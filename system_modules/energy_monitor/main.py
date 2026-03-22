"""system_modules/energy_monitor/main.py — FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from system_modules.energy_monitor.energy import EnergyMonitor

logger = logging.getLogger(__name__)

CORE_URL = os.getenv("CORE_API_URL", "http://localhost:7070")
MODULE_TOKEN = os.getenv("MODULE_TOKEN", "energy-monitor-token")

# ── Core API helpers ──────────────────────────────────────────────────────────


async def _publish(event_type: str, payload: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_URL}/api/v1/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"type": event_type, "source": "energy-monitor", "payload": payload},
            )
    except Exception as exc:
        logger.warning("Failed to publish %s: %s", event_type, exc)


# ── App state ─────────────────────────────────────────────────────────────────

_monitor: EnergyMonitor | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _monitor
    db_path = os.getenv("ENERGY_DB_PATH", ":memory:")
    _monitor = EnergyMonitor(
        publish_event_cb=_publish,
        db_path=db_path,
    )
    await _monitor.start()
    yield
    await _monitor.stop()


app = FastAPI(title="EnergyMonitor", lifespan=lifespan)

# ── Request models ────────────────────────────────────────────────────────────


class ReadingRequest(BaseModel):
    device_id: str
    watts: float = Field(ge=0.0)


# ── Routes ────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": "energy-monitor"}


@app.post("/energy/reading", status_code=201)
async def post_reading(req: ReadingRequest) -> JSONResponse:
    if _monitor is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    await _monitor.record_reading(req.device_id, req.watts)
    return JSONResponse({"ok": True, "device_id": req.device_id, "watts": req.watts}, status_code=201)


@app.get("/energy/current")
async def get_current() -> JSONResponse:
    if _monitor is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse({
        "power": _monitor.get_current_power(),
        "total_w": _monitor.get_total_power(),
    })


@app.get("/energy/today")
async def get_today() -> JSONResponse:
    if _monitor is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse({
        "total_kwh": _monitor.get_total_today_kwh(),
        "devices": {
            dev: _monitor.get_daily_kwh(dev)
            for dev in _monitor.get_all_devices()
        },
    })


@app.get("/energy/devices")
async def get_devices() -> JSONResponse:
    if _monitor is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse({"devices": _monitor.get_all_devices()})


@app.get("/energy/devices/{device_id}/history")
async def get_device_history(device_id: str, limit: int = 100) -> JSONResponse:
    if _monitor is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse({
        "device_id": device_id,
        "history": _monitor.get_device_history(device_id, limit=limit),
    })


@app.get("/energy/status")
async def get_status() -> JSONResponse:
    if _monitor is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return JSONResponse(_monitor.get_status())


@app.get("/widget.html", response_class=HTMLResponse)
async def widget() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.energy_monitor").joinpath("widget.html")
    return HTMLResponse(path.read_text())


@app.get("/settings.html", response_class=HTMLResponse)
async def settings() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.energy_monitor").joinpath("settings.html")
    return HTMLResponse(path.read_text())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("system_modules.energy_monitor.main:app", host="0.0.0.0", port=8114, reload=False)
