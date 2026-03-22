"""system_modules/notification_router/main.py — FastAPI entry point."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from system_modules.notification_router.router import NotificationRouter, VALID_CHANNELS

logger = logging.getLogger(__name__)

CORE_URL = os.getenv("CORE_API_URL", "http://localhost:7070")
MODULE_TOKEN = os.getenv("MODULE_TOKEN", "notification-router-token")


async def _publish(event_type: str, payload: dict[str, Any]) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_URL}/api/v1/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"type": event_type, "source": "notification-router", "payload": payload},
            )
    except Exception as exc:
        logger.warning("Failed to publish %s: %s", event_type, exc)


_router: NotificationRouter | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _router
    _router = NotificationRouter(publish_event_cb=_publish)
    yield
    _router = None


app = FastAPI(title="NotificationRouter", lifespan=lifespan)

# ── Request models ────────────────────────────────────────────────────────────


class SendRequest(BaseModel):
    message: str
    level: str = "info"
    tags: list[str] = Field(default_factory=list)
    event_type: str | None = None


class ChannelRequest(BaseModel):
    name: str
    config: dict = Field(default_factory=dict)


class RuleRequest(BaseModel):
    rule_id: str | None = None
    priority: int = 100
    event_types: list[str] = Field(default_factory=list)
    channel: str
    level: str | None = None
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class EventWebhookRequest(BaseModel):
    type: str
    payload: dict = Field(default_factory=dict)


# ── Routes ────────────────────────────────────────────────────────────────────


def _require_router() -> NotificationRouter:
    if _router is None:
        raise HTTPException(status_code=503, detail="Service not ready")
    return _router


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "module": "notification-router"}


@app.post("/notify/send")
async def send_notification(req: SendRequest) -> JSONResponse:
    rt = _require_router()
    channels = await rt.send(req.message, level=req.level, tags=req.tags, event_type=req.event_type)
    return JSONResponse({"ok": True, "delivered_to": channels})


@app.post("/notify/event")
async def handle_event(req: EventWebhookRequest) -> JSONResponse:
    rt = _require_router()
    channels = await rt.handle_event(req.type, req.payload)
    return JSONResponse({"ok": True, "delivered_to": channels})


@app.get("/notify/history")
async def get_history(limit: int = 50) -> JSONResponse:
    rt = _require_router()
    return JSONResponse({"history": rt.get_history(limit=limit)})


@app.get("/notify/status")
async def get_status() -> JSONResponse:
    rt = _require_router()
    return JSONResponse(rt.get_status())


@app.post("/channels", status_code=201)
async def add_channel(req: ChannelRequest) -> JSONResponse:
    rt = _require_router()
    try:
        rt.add_channel(req.name, req.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "name": req.name}, status_code=201)


@app.get("/channels")
async def list_channels() -> JSONResponse:
    rt = _require_router()
    return JSONResponse({"channels": rt.get_channels()})


@app.delete("/channels/{name}", status_code=204, response_class=Response, response_model=None)
async def remove_channel(name: str) -> None:
    rt = _require_router()
    if not rt.remove_channel(name):
        raise HTTPException(status_code=404, detail="Channel not found")


@app.post("/rules", status_code=201)
async def add_rule(req: RuleRequest) -> JSONResponse:
    rt = _require_router()
    if req.channel not in VALID_CHANNELS:
        raise HTTPException(status_code=400, detail=f"Invalid channel: {req.channel}")
    rule_id = rt.add_rule(req.model_dump())
    return JSONResponse({"ok": True, "rule_id": rule_id}, status_code=201)


@app.get("/rules")
async def list_rules() -> JSONResponse:
    rt = _require_router()
    return JSONResponse({"rules": rt.get_rules()})


@app.delete("/rules/{rule_id}", status_code=204, response_class=Response, response_model=None)
async def remove_rule(rule_id: str) -> None:
    rt = _require_router()
    if not rt.remove_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")


@app.get("/widget.html", response_class=HTMLResponse)
async def widget() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.notification_router").joinpath("widget.html")
    return HTMLResponse(path.read_text())


@app.get("/settings.html", response_class=HTMLResponse)
async def settings() -> HTMLResponse:
    import importlib.resources
    path = importlib.resources.files("system_modules.notification_router").joinpath("settings.html")
    return HTMLResponse(path.read_text())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("system_modules.notification_router.main:app", host="0.0.0.0", port=8116, reload=False)
