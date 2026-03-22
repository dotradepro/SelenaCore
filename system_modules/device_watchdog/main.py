"""
system_modules/device_watchdog/main.py — точка входа FastAPI модуля device_watchdog [#70]
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pathlib import Path
from pydantic import BaseModel

from system_modules.device_watchdog.watchdog import DeviceWatchdog

logger = logging.getLogger(__name__)

MODULE_NAME = "device-watchdog"
CORE_API = os.environ.get("SELENA_CORE_API", "http://localhost:7070/api/v1")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")

_watchdog: DeviceWatchdog | None = None
_config: dict = {
    "check_interval_sec": 60,
    "ping_timeout_sec": 2.0,
    "mqtt_timeout_sec": 120,
    "protocol_timeout_sec": 300,
    "offline_threshold": 3,
    "notify_on_offline": True,
}

# ── Core API helpers ─────────────────────────────────────────────────────────

async def _publish_event(event_type: str, payload: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_API}/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"type": event_type, "source": MODULE_NAME, "payload": payload},
            )
    except Exception as exc:
        logger.error(f"Failed to publish {event_type}: {exc}")


async def _get_devices() -> list[dict]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{CORE_API}/devices",
            headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
        )
        resp.raise_for_status()
        return resp.json().get("devices", [])


async def _update_device(device_id: str, state_patch: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{CORE_API}/devices/{device_id}/state",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"state": state_patch},
            )
    except Exception as exc:
        logger.error(f"Failed to update device {device_id}: {exc}")


async def _subscribe_events() -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{CORE_API}/events/subscribe",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={
                    "event_types": ["device.protocol_heartbeat"],
                    "webhook_url": "http://localhost:8110/webhook/events",
                },
            )
    except Exception as exc:
        logger.warning(f"Event subscription failed: {exc}")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _watchdog
    _watchdog = DeviceWatchdog(
        publish_callback=_publish_event,
        get_devices_callback=_get_devices,
        update_device_callback=_update_device,
        config=_config,
    )
    await _watchdog.start()
    await _subscribe_events()
    await _publish_event("module.started", {"name": MODULE_NAME})
    yield
    if _watchdog:
        await _watchdog.stop()
    await _publish_event("module.stopped", {"name": MODULE_NAME})


app = FastAPI(title="SelenaCore Device Watchdog", version="0.1.0", lifespan=lifespan)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    summary = _watchdog.get_status_summary() if _watchdog else {}
    return {"status": "ok", "module": MODULE_NAME, **summary}


@app.post("/scan")
async def trigger_scan() -> dict:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not running")
    return await _watchdog.check_now()


@app.get("/status")
async def get_status() -> dict:
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog not running")
    return _watchdog.get_status_summary()


class ConfigUpdateRequest(BaseModel):
    check_interval_sec: int | None = None
    ping_timeout_sec: float | None = None
    mqtt_timeout_sec: int | None = None
    protocol_timeout_sec: int | None = None
    offline_threshold: int | None = None
    notify_on_offline: bool | None = None


@app.get("/config")
async def get_config() -> dict:
    return _config


@app.post("/config")
async def update_config(req: ConfigUpdateRequest) -> dict:
    update = req.model_dump(exclude_none=True)
    _config.update(update)
    if _watchdog:
        _watchdog.update_config(update)
    return _config


@app.post("/webhook/events")
async def webhook_events(payload: dict) -> dict:
    if _watchdog is None:
        return {"status": "error"}
    event_type = payload.get("type", "")
    data = payload.get("payload", {})
    if event_type == "device.protocol_heartbeat":
        await _watchdog.on_protocol_heartbeat(data)
    return {"status": "ok"}


@app.get("/widget", response_class=HTMLResponse)
async def widget() -> str:
    f = Path(__file__).parent / "widget.html"
    return f.read_text() if f.exists() else "<p>widget.html not found</p>"


@app.get("/settings", response_class=HTMLResponse)
async def settings_page() -> str:
    f = Path(__file__).parent / "settings.html"
    return f.read_text() if f.exists() else "<p>settings.html not found</p>"
