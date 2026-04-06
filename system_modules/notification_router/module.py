"""
system_modules/notification_router/module.py — In-process SystemModule wrapper.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule
from system_modules.notification_router.router import NotificationRouter, VALID_CHANNELS

logger = logging.getLogger(__name__)


class SendRequest(BaseModel):
    message: str
    level: str = "info"
    tags: list[str] = []
    event_type: str | None = None


class ChannelRequest(BaseModel):
    name: str
    config: dict


class RuleRequest(BaseModel):
    rule_id: str | None = None
    priority: int = 0
    event_types: list[str] = []
    channel: str = ""
    level: str | None = None
    tags: list[str] = []
    enabled: bool = True


class EventWebhookRequest(BaseModel):
    type: str
    payload: dict = {}


class NotificationRouterModule(SystemModule):
    name = "notification-router"

    def __init__(self) -> None:
        super().__init__()
        self._router_svc: NotificationRouter | None = None

    async def start(self) -> None:
        self._router_svc = NotificationRouter(publish_event_cb=self.publish)
        self._ensure_push_defaults()
        await self.publish("module.started", {"name": self.name})

    def _ensure_push_defaults(self) -> None:
        """Ensure a 'push' channel and a catch-all rule exist after first boot."""
        rt = self._router_svc
        if rt is None:
            return
        if "push" not in rt._channels:
            rt.add_channel("push", {
                "push_url": "http://localhost/api/ui/modules/presence-detection/push/send",
            })
            logger.info("Auto-registered default push channel")
        if not any(r.get("channel") == "push" for r in rt._rules):
            rt.add_rule({
                "rule_id": "default-push-all",
                "channel": "push",
                "priority": 100,
                "event_types": [],
                "level": None,
                "tags": [],
                "enabled": True,
            })
            logger.info("Auto-registered default push rule (all notifications)")

    async def stop(self) -> None:
        self._router_svc = None
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        svc._register_health_endpoint(router)

        @router.post("/notify/send")
        async def send(req: SendRequest) -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            result = await svc._router_svc.send(
                message=req.message, level=req.level, tags=req.tags, event_type=req.event_type
            )
            return JSONResponse(result)

        @router.post("/notify/event")
        async def handle_event(req: EventWebhookRequest) -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            result = await svc._router_svc.handle_event(req.type, req.payload)
            return JSONResponse(result)

        @router.get("/notify/history")
        async def history(limit: int = 50) -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._router_svc.get_history(limit))

        @router.get("/notify/status")
        async def status() -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._router_svc.get_status())

        @router.post("/channels", status_code=201)
        async def add_channel(req: ChannelRequest) -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            svc._router_svc.add_channel(req.name, req.config)
            return JSONResponse({"ok": True, "name": req.name}, status_code=201)

        @router.get("/channels")
        async def list_channels() -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._router_svc.get_channels())

        @router.delete("/channels/{name}", status_code=204, response_class=Response, response_model=None)
        async def remove_channel(name: str) -> Response:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            svc._router_svc.remove_channel(name)
            return Response(status_code=204)

        @router.post("/rules", status_code=201)
        async def add_rule(req: RuleRequest) -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            rule = svc._router_svc.add_rule(req.model_dump())
            return JSONResponse(rule, status_code=201)

        @router.get("/rules")
        async def list_rules() -> JSONResponse:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            return JSONResponse(svc._router_svc.get_rules())

        @router.delete("/rules/{rule_id}", status_code=204, response_class=Response, response_model=None)
        async def remove_rule(rule_id: str) -> Response:
            if svc._router_svc is None:
                raise HTTPException(503, "Not ready")
            svc._router_svc.remove_rule(rule_id)
            return Response(status_code=204)

        svc._register_html_routes(router, __file__)
        return router
