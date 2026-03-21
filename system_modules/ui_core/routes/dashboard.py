"""
system_modules/ui_core/routes/dashboard.py — Dashboard API routes

Provides:
  - GET /api/ui/dashboard — aggregated dashboard state
  - GET /api/ui/devices — proxied device list from Core API
  - GET /api/ui/modules — proxied module list from Core API
  - GET /api/ui/system — system health and hardware info
"""
from __future__ import annotations

import logging
import os
import platform as _platform
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ui", tags=["dashboard"])

CORE_API = os.environ.get("CORE_API_URL", "http://localhost:7070/api/v1")
DEV_TOKEN = os.environ.get("DEV_MODULE_TOKEN", "")


def _core_headers() -> dict[str, str]:
    token_dir = "/secure/module_tokens"
    try:
        tokens = list(__import__("pathlib").Path(token_dir).glob("*.token"))
        if tokens:
            token = tokens[0].read_text().strip()
            return {"Authorization": f"Bearer {token}"}
    except Exception:
        pass
    if DEV_TOKEN:
        return {"Authorization": f"Bearer {DEV_TOKEN}"}
    return {}


async def _core_get(path: str) -> Any:
    url = f"{CORE_API}{path}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=_core_headers())
            resp.raise_for_status()
            return resp.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Core API unreachable: {e}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))


@router.get("/dashboard")
async def get_dashboard() -> JSONResponse:
    """Aggregated dashboard: health + device count + module count."""
    try:
        health = await _core_get("/health")
    except Exception:
        health = {"status": "unreachable"}
    try:
        devices_resp = await _core_get("/devices")
        devices = devices_resp.get("devices", [])
    except Exception:
        devices = []
    try:
        modules_resp = await _core_get("/modules")
        modules = modules_resp.get("modules", [])
    except Exception:
        modules = []

    return JSONResponse({
        "health": health,
        "device_count": len(devices),
        "module_count": len(modules),
        "devices": devices[:10],   # preview
        "modules": modules,
    })


@router.get("/devices")
async def get_devices() -> JSONResponse:
    data = await _core_get("/devices")
    return JSONResponse(data)


@router.get("/modules")
async def get_modules() -> JSONResponse:
    data = await _core_get("/modules")
    return JSONResponse(data)


@router.get("/system")
async def get_system_info() -> JSONResponse:
    """System hardware info + Core API health."""
    try:
        health = await _core_get("/health")
    except Exception:
        health = {"status": "unreachable"}

    hw: dict[str, Any] = {
        "machine": _platform.machine(),
        "node": _platform.node(),
        "python": _platform.python_version(),
    }

    try:
        import psutil
        hw.update({
            "cpu_percent": psutil.cpu_percent(interval=None),
            "ram_total_mb": psutil.virtual_memory().total // (1024 * 1024),
            "ram_used_mb": psutil.virtual_memory().used // (1024 * 1024),
            "disk_total_gb": round(psutil.disk_usage("/").total / 1e9, 1),
            "disk_used_gb": round(psutil.disk_usage("/").used / 1e9, 1),
        })
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                sensor = next(iter(temps.values()))
                if sensor:
                    hw["cpu_temp"] = sensor[0].current
        except Exception:
            pass
    except ImportError:
        pass

    return JSONResponse({"hardware": hw, "core": health})
