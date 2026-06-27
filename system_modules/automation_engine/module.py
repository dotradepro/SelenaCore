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
from system_modules.automation_engine.voice_handler import AutomationVoiceHandler

logger = logging.getLogger(__name__)


class RuleRequest(BaseModel):
    definition: dict


class YamlImportRequest(BaseModel):
    yaml_text: str


class EnableRequest(BaseModel):
    enabled: bool


class AutomationEngineModule(SystemModule):
    name = "automation-engine"

    OWNED_INTENTS = [
        "automation.list",
        "automation.enable",
        "automation.disable",
        # automation.status removed 2026-04-18 — redundant with list,
        # both described as "report state of rules". Classifier
        # couldn't distinguish and it muddied the candidate set.
    ]

    _OWNED_INTENT_META: dict[str, dict] = {
        "automation.list": dict(
            noun_class="AUTOMATION", verb="query", priority=100,
            description=(
                "Read back the list of configured automation rules / "
                "scenes — their names, states, next trigger times. "
                "Query ONLY, no side effects. Use for 'list "
                "automations', 'what rules do I have', 'show all "
                "automations'."
            ),
        ),
        "automation.enable": dict(
            noun_class="AUTOMATION", verb="activate", priority=100,
            description=(
                "Turn ON a named automation rule by its label. "
                "Activates future trigger reactions — does NOT run the "
                "rule immediately. Use for 'enable the bedtime "
                "automation', 'activate morning routine', 'turn on "
                "the vacation scene rule'."
            ),
        ),
        "automation.disable": dict(
            noun_class="AUTOMATION", verb="deactivate", priority=100,
            description=(
                "Turn OFF a named automation rule — keep the rule "
                "definition but stop reacting to triggers. Use for "
                "'disable the morning routine', 'turn off the "
                "automation', 'pause the rule'."
            ),
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._engine: AutomationEngine | None = None
        self._voice: AutomationVoiceHandler | None = None

    async def _send_notification(self, message: str, channel: str) -> None:
        await self.publish(
            "notification.send", {"message": message, "channel": channel}
        )

    # speak() is inherited from SystemModule — blocking, waits for TTS to finish

    async def _on_event(self, event) -> None:
        if event.type == "voice.intent":
            intent = event.payload.get("intent", "")
            if intent.startswith("automation.") and self._voice:
                ctx = await self._voice.handle(intent, event.payload.get("params", {}))
                if ctx:
                    await self.speak_action(intent, ctx)
            return
        if self._engine:
            await self._engine.on_event(event.type, event.payload)

    async def start(self) -> None:
        self._engine = AutomationEngine(
            send_device_command_cb=self.patch_device_state,
            publish_event_cb=self.publish,
            get_device_state_cb=self.get_device_state,
            send_notification_cb=self._send_notification,
        )
        self._voice = AutomationVoiceHandler(self)
        self.subscribe(
            ["device.state_changed", "presence.home", "presence.away", "voice.intent"],
            self._on_event,
        )

        # Register automation.* intents (static catalog). Idempotent.
        await self._claim_intent_ownership()

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

        svc._register_html_routes(router, __file__)
        return router
