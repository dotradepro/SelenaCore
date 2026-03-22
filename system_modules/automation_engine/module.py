"""
system_modules/automation_engine/module.py — In-process SystemModule wrapper.

Runs inside the core process via importlib — NOT a separate uvicorn subprocess.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.automation_engine.engine import AutomationEngine

logger = logging.getLogger(__name__)


class RuleRequest(BaseModel):
    definition: dict


class YamlImportRequest(BaseModel):
    yaml_text: str


class EnableRequest(BaseModel):
    enabled: bool


class AutomationEngineModule(SystemModule):
    name = "automation-engine"

    def __init__(self) -> None:
        super().__init__()
        self._engine: AutomationEngine | None = None

    async def _send_notification(self, message: str, channel: str) -> None:
        await self.publish(
            "notification.send", {"message": message, "channel": channel}
        )

    async def _on_event(self, event) -> None:
        if self._engine:
            await self._engine.on_event(event.type, event.payload)

    async def start(self) -> None:
        self._engine = AutomationEngine(
            send_device_command_cb=self.patch_device_state,
            publish_event_cb=self.publish,
            get_device_state_cb=self.get_device_state,
            send_notification_cb=self._send_notification,
        )
        self.subscribe(
            ["device.state_changed", "presence.home", "presence.away", "voice.intent"],
            self._on_event,
        )
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/health")
        async def health() -> dict:
            status = svc._engine.get_status() if svc._engine else {}
            return {"status": "ok", "module": svc.name, **status}

        @router.get("/status")
        async def get_status() -> dict:
            if svc._engine is None:
                raise HTTPException(503, "Engine not running")
            return svc._engine.get_status()

        @router.get("/rules")
        async def list_rules() -> dict:
            if svc._engine is None:
                raise HTTPException(503, "Engine not running")
            return {"rules": svc._engine.list_rules()}

        @router.post("/rules", status_code=201)
        async def create_rule(req: RuleRequest) -> dict:
            if svc._engine is None:
                raise HTTPException(503, "Engine not running")
            try:
                rule = svc._engine.load_rule(req.definition)
                return rule.to_dict()
            except Exception as exc:
                raise HTTPException(400, str(exc))

        @router.post("/rules/import")
        async def import_yaml(req: YamlImportRequest) -> dict:
            if svc._engine is None:
                raise HTTPException(503, "Engine not running")
            try:
                rules = svc._engine.load_rules_from_yaml(req.yaml_text)
                return {"imported": len(rules), "rules": [r.to_dict() for r in rules]}
            except ValueError as exc:
                raise HTTPException(400, str(exc))

        @router.get("/rules/{rule_id}")
        async def get_rule(rule_id: str) -> dict:
            if svc._engine is None:
                raise HTTPException(503, "Engine not running")
            rule = svc._engine.get_rule(rule_id)
            if not rule:
                raise HTTPException(404, "Rule not found")
            return rule.to_dict()

        @router.delete("/rules/{rule_id}", status_code=200)
        async def delete_rule(rule_id: str) -> dict:
            if svc._engine is None:
                raise HTTPException(503, "Engine not running")
            if not svc._engine.delete_rule(rule_id):
                raise HTTPException(404, "Rule not found")
            return {"removed": rule_id}

        @router.patch("/rules/{rule_id}/enabled")
        async def set_rule_enabled(rule_id: str, req: EnableRequest) -> dict:
            if svc._engine is None:
                raise HTTPException(503, "Engine not running")
            if not svc._engine.enable_rule(rule_id, req.enabled):
                raise HTTPException(404, "Rule not found")
            return svc._engine.get_rule(rule_id).to_dict()

        @router.get("/widget", response_class=HTMLResponse)
        async def widget() -> HTMLResponse:
            f = Path(__file__).parent / "widget.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>widget.html not found</p>")

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page() -> HTMLResponse:
            f = Path(__file__).parent / "settings.html"
            return HTMLResponse(f.read_text() if f.exists() else "<p>settings.html not found</p>")

        return router
