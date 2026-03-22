"""
system_modules/protocol_bridge/main.py — точка входа FastAPI модуля protocol_bridge [#71]
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .bridge import ProtocolBridge

logger = logging.getLogger(__name__)

MODULE_NAME = "protocol-bridge"
CORE_API = os.environ.get("SELENA_CORE_API", "http://localhost:7070/api/v1")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")

_bridge: ProtocolBridge | None = None
_config: dict = {
    "mqtt_enabled": True,
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "mqtt_username": None,
    "mqtt_password": None,
    "zigbee_enabled": False,
    "zigbee_adapter_path": "/dev/ttyUSB0",
    "zwave_enabled": False,
    "http_poll_interval_sec": 30,
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


async def _register_device(
    name: str,
    device_type: str,
    protocol: str,
    capabilities: list,
    meta: dict,
) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{CORE_API}/devices",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={
                    "name": name,
                    "type": device_type,
                    "protocol": protocol,
                    "capabilities": capabilities,
                    "meta": meta,
                },
            )
            if resp.status_code == 201:
                return resp.json().get("device_id")
    except Exception as exc:
        logger.error(f"register_device failed: {exc}")
    return None


async def _update_device_state(device_id: str, state: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{CORE_API}/devices/{device_id}/state",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"state": state},
            )
    except Exception as exc:
        logger.error(f"update_device_state failed: {exc}")


async def _get_devices() -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{CORE_API}/devices",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
            )
            resp.raise_for_status()
            return resp.json().get("devices", [])
    except Exception as exc:
        logger.error(f"get_devices failed: {exc}")
        return []


async def _subscribe_events() -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{CORE_API}/events/subscribe",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={
                    "event_types": ["device.state_changed"],
                    "webhook_url": "http://localhost:8109/webhook/events",
                },
            )
    except Exception as exc:
        logger.warning(f"Event subscription failed: {exc}")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _bridge
    _bridge = ProtocolBridge(
        config=_config,
        register_device_cb=_register_device,
        update_device_state_cb=_update_device_state,
        get_devices_cb=_get_devices,
        publish_event_cb=_publish_event,
    )
    await _bridge.start()
    await _subscribe_events()
    await _publish_event("module.started", {"name": MODULE_NAME})
    yield
    if _bridge:
        await _bridge.stop()
    await _publish_event("module.stopped", {"name": MODULE_NAME})


app = FastAPI(title="SelenaCore Protocol Bridge", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    status = _bridge.get_status() if _bridge else {}
    return {"status": "ok", "module": MODULE_NAME, **status}


@app.get("/status")
async def get_status() -> dict:
    if _bridge is None:
        raise HTTPException(status_code=503, detail="Bridge not running")
    return _bridge.get_status()


class ConfigUpdateRequest(BaseModel):
    mqtt_enabled: bool | None = None
    mqtt_host: str | None = None
    mqtt_port: int | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    zigbee_enabled: bool | None = None
    zigbee_adapter_path: str | None = None
    zwave_enabled: bool | None = None
    http_poll_interval_sec: int | None = None


@app.get("/config")
async def get_config() -> dict:
    # Don't expose password
    safe = {k: v for k, v in _config.items() if k != "mqtt_password"}
    return safe


@app.post("/config")
async def update_config(req: ConfigUpdateRequest) -> dict:
    _config.update(req.model_dump(exclude_none=True))
    return get_config()


@app.post("/webhook/events")
async def webhook_events(payload: dict) -> dict:
    if _bridge is None:
        return {"status": "error"}
    event_type = payload.get("type", "")
    data = payload.get("payload", {})
    if event_type == "device.state_changed":
        await _bridge.on_state_changed(data)
    return {"status": "ok"}


@app.get("/widget", response_class=HTMLResponse)
async def widget() -> str:
    f = Path(__file__).parent / "widget.html"
    return f.read_text() if f.exists() else "<p>widget.html not found</p>"


@app.get("/settings", response_class=HTMLResponse)
async def settings_page() -> str:
    f = Path(__file__).parent / "settings.html"
    return f.read_text() if f.exists() else "<p>settings.html not found</p>"
