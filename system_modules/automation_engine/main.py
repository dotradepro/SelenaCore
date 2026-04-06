"""
system_modules/automation_engine/main.py — FastAPI entry point for automation_engine [#72]
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

from system_modules.automation_engine.engine import AutomationEngine

logger = logging.getLogger(__name__)

MODULE_NAME = "automation-engine"
CORE_API = os.environ.get("SELENA_CORE_API", "http://localhost/api/v1")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")

_engine: AutomationEngine | None = None

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


async def _send_device_command(device_id: str, state: dict) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.patch(
                f"{CORE_API}/devices/{device_id}/state",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={"state": state},
            )
    except Exception as exc:
        logger.error(f"send_device_command failed for {device_id}: {exc}")


async def _get_device_state(device_id: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{CORE_API}/devices/{device_id}",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
            )
            resp.raise_for_status()
            return resp.json().get("state", {})
    except Exception as exc:
        logger.error(f"get_device_state failed for {device_id}: {exc}")
        return {}


async def _send_notification(message: str, channel: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{CORE_API}/events/publish",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={
                    "type": "notification.send",
                    "source": MODULE_NAME,
                    "payload": {"message": message, "channel": channel},
                },
            )
    except Exception as exc:
        logger.error(f"send_notification failed: {exc}")


async def _subscribe_events() -> None:
    event_types = [
        "device.state_changed",
        "presence.home",
        "presence.away",
        "voice.intent",
    ]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{CORE_API}/events/subscribe",
                headers={"Authorization": f"Bearer {MODULE_TOKEN}"},
                json={
                    "event_types": event_types,
                    "webhook_url": "http://localhost/webhook/events",  # Legacy — use SystemModule.subscribe() instead
                },
            )
    except Exception as exc:
        logger.warning(f"Event subscription failed: {exc}")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine
    _engine = AutomationEngine(
        send_device_command_cb=_send_device_command,
        publish_event_cb=_publish_event,
        get_device_state_cb=_get_device_state,
        send_notification_cb=_send_notification,
    )
    await _subscribe_events()
    await _publish_event("module.started", {"name": MODULE_NAME})
    yield
    await _publish_event("module.stopped", {"name": MODULE_NAME})


app = FastAPI(
    title="SelenaCore Automation Engine",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    status = _engine.get_status() if _engine else {}
    return {"status": "ok", "module": MODULE_NAME, **status}


@app.get("/status")
async def get_status() -> dict:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not running")
    return _engine.get_status()


@app.get("/rules")
async def list_rules() -> dict:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not running")
    return {"rules": _engine.list_rules()}


class RuleRequest(BaseModel):
    definition: dict


@app.post("/rules", status_code=201)
async def create_rule(req: RuleRequest) -> dict:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not running")
    try:
        rule = _engine.load_rule(req.definition)
        return rule.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class YamlImportRequest(BaseModel):
    yaml_text: str


@app.post("/rules/import")
async def import_yaml(req: YamlImportRequest) -> dict:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not running")
    try:
        rules = _engine.load_rules_from_yaml(req.yaml_text)
        return {"imported": len(rules), "rules": [r.to_dict() for r in rules]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/rules/{rule_id}")
async def get_rule(rule_id: str) -> dict:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not running")
    rule = _engine.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule.to_dict()


@app.delete("/rules/{rule_id}", status_code=204, response_class=Response, response_model=None)
async def delete_rule(rule_id: str) -> None:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not running")
    if not _engine.delete_rule(rule_id):
        raise HTTPException(status_code=404, detail="Rule not found")


class EnableRequest(BaseModel):
    enabled: bool


@app.patch("/rules/{rule_id}/enabled")
async def set_rule_enabled(rule_id: str, req: EnableRequest) -> dict:
    if _engine is None:
        raise HTTPException(status_code=503, detail="Engine not running")
    if not _engine.enable_rule(rule_id, req.enabled):
        raise HTTPException(status_code=404, detail="Rule not found")
    rule = _engine.get_rule(rule_id)
    return rule.to_dict()


@app.post("/webhook/events")
async def webhook_events(payload: dict) -> dict:
    if _engine is None:
        return {"status": "error"}
    event_type = payload.get("type", "")
    data = payload.get("payload", {})
    await _engine.on_event(event_type, data)
    return {"status": "ok"}


@app.get("/widget", response_class=HTMLResponse)
async def widget() -> str:
    f = Path(__file__).parent / "widget.html"
    return f.read_text() if f.exists() else "<p>widget.html not found</p>"


@app.get("/settings", response_class=HTMLResponse)
async def settings_page() -> str:
    f = Path(__file__).parent / "settings.html"
    return f.read_text() if f.exists() else "<p>settings.html not found</p>"
