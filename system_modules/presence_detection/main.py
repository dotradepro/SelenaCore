"""
system_modules/presence_detection/main.py — FastAPI entry point [#73]
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from system_modules.presence_detection.presence import PresenceDetector

logger = logging.getLogger(__name__)

MODULE_NAME = "presence-detection"
CORE_API = os.environ.get("SELENA_CORE_API", "http://localhost:7070/api/v1")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")

_detector: PresenceDetector | None = None


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


async def _subscribe_events() -> None:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{CORE_API}/events/subscribe",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={
                    "event_types": ["device.state_changed"],
                    "webhook_url": "http://localhost:8112/webhook/events",
                },
            )
    except Exception as exc:
        logger.warning(f"Event subscription failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _detector
    _detector = PresenceDetector(
        publish_event_cb=_publish_event,
        scan_interval_sec=int(os.environ.get("PRESENCE_SCAN_INTERVAL", "60")),
        away_threshold_sec=int(os.environ.get("PRESENCE_AWAY_THRESHOLD", "300")),
    )
    await _detector.start()
    await _subscribe_events()
    await _publish_event("module.started", {"name": MODULE_NAME})
    yield
    if _detector:
        await _detector.stop()
    await _publish_event("module.stopped", {"name": MODULE_NAME})


app = FastAPI(
    title="SelenaCore Presence Detection",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    status = _detector.get_status() if _detector else {}
    return {"status": "ok", "module": MODULE_NAME, **status}


@app.get("/status")
async def get_status() -> dict:
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector not running")
    return _detector.get_status()


@app.get("/users")
async def list_users() -> dict:
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector not running")
    return {"users": _detector.list_users()}


class UserRequest(BaseModel):
    user_id: str
    name: str
    devices: list[dict]


@app.post("/users", status_code=201)
async def add_user(req: UserRequest) -> dict:
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector not running")
    user = _detector.add_user(req.model_dump())
    return user


@app.get("/users/{user_id}")
async def get_user(user_id: str) -> dict:
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector not running")
    user = _detector.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.delete("/users/{user_id}", status_code=204, response_class=Response, response_model=None)
async def remove_user(user_id: str) -> None:
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector not running")
    if not _detector.remove_user(user_id):
        raise HTTPException(status_code=404, detail="User not found")


@app.post("/scan")
async def trigger_scan() -> dict:
    if _detector is None:
        raise HTTPException(status_code=503, detail="Detector not running")
    users = await _detector.trigger_scan_now()
    return {"users": users}


@app.post("/webhook/events")
async def webhook_events(payload: dict) -> dict:
    return {"status": "ok"}


@app.get("/widget", response_class=HTMLResponse)
async def widget() -> str:
    f = Path(__file__).parent / "widget.html"
    return f.read_text() if f.exists() else "<p>widget.html not found</p>"


@app.get("/settings", response_class=HTMLResponse)
async def settings_page() -> str:
    f = Path(__file__).parent / "settings.html"
    return f.read_text() if f.exists() else "<p>settings.html not found</p>"
