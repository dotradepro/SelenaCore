"""
core/api/routes/events.py — Event Bus endpoints
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl

from core.api.auth import verify_module_token
from core.eventbus.bus import Subscription, get_event_bus
from core.eventbus.types import CORE_EVENTS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


class EventPublish(BaseModel):
    type: str = Field(..., min_length=1, max_length=255)
    source: str = Field(..., min_length=1, max_length=255)
    payload: dict[str, Any] = Field(default_factory=dict)


class EventPublishResponse(BaseModel):
    event_id: str
    type: str
    timestamp: float


class SubscribeRequest(BaseModel):
    event_types: list[str] = Field(..., min_length=1)
    webhook_url: str  # HttpUrl causes issues with localhost in some pydantic versions


class SubscribeResponse(BaseModel):
    subscription_id: str
    event_types: list[str]
    webhook_url: str


@router.post("/publish", response_model=EventPublishResponse, status_code=201)
async def publish_event(
    body: EventPublish,
    _token: str = Depends(verify_module_token),
) -> EventPublishResponse:
    # Modules cannot publish core.* events
    if body.type.startswith("core."):
        raise HTTPException(
            status_code=403,
            detail="Publishing core.* events is forbidden for modules",
        )
    bus = get_event_bus()
    event = await bus.publish(
        type=body.type,
        source=body.source,
        payload=body.payload,
    )
    return EventPublishResponse(
        event_id=event.event_id,
        type=event.type,
        timestamp=event.timestamp,
    )


@router.post("/subscribe", response_model=SubscribeResponse, status_code=201)
async def subscribe_events(
    body: SubscribeRequest,
    module_id: str = Depends(verify_module_token),
) -> SubscribeResponse:
    bus = get_event_bus()
    sub = bus.subscribe(
        module_id=module_id,
        event_types=body.event_types,
        webhook_url=body.webhook_url,
    )
    return SubscribeResponse(
        subscription_id=sub.subscription_id,
        event_types=sub.event_types,
        webhook_url=sub.webhook_url,
    )
