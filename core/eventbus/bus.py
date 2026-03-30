"""
core/eventbus/bus.py — Event Bus на asyncio.Queue с webhook доставкой
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from core.eventbus.types import CORE_EVENTS

logger = logging.getLogger(__name__)

WEBHOOK_RETRY_ATTEMPTS = 3
WEBHOOK_TIMEOUT_SEC = 10


@dataclass
class Event:
    event_id: str
    type: str
    source: str
    payload: dict[str, Any]
    timestamp: float

    @classmethod
    def create(cls, type: str, source: str, payload: dict[str, Any]) -> "Event":
        return cls(
            event_id=str(uuid.uuid4()),
            type=type,
            source=source,
            payload=payload,
            timestamp=datetime.now(timezone.utc).timestamp(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "type": self.type,
            "source": self.source,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


@dataclass
class Subscription:
    subscription_id: str
    module_id: str
    event_types: list[str]
    webhook_url: str
    secret: str = ""


@dataclass
class DirectSubscription:
    """In-process subscription — calls an async Python callback instead of a webhook.

    Used exclusively by SYSTEM modules that run inside the core process.
    """

    subscription_id: str
    module_id: str
    event_types: list[str]
    callback: Any  # Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=10000)
        self._subscriptions: dict[str, Subscription] = {}
        self._direct_subs: dict[str, DirectSubscription] = {}
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop(), name="eventbus-dispatch")
        logger.info("EventBus started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("EventBus stopped")

    def subscribe(
        self,
        module_id: str,
        event_types: list[str],
        webhook_url: str,
        secret: str = "",
    ) -> Subscription:
        sub = Subscription(
            subscription_id=str(uuid.uuid4()),
            module_id=module_id,
            event_types=event_types,
            webhook_url=webhook_url,
            secret=secret,
        )
        self._subscriptions[sub.subscription_id] = sub
        logger.info(
            "Subscription created: %s → %s for %s",
            sub.subscription_id,
            webhook_url,
            event_types,
        )
        return sub

    def unsubscribe(self, subscription_id: str) -> None:
        self._subscriptions.pop(subscription_id, None)

    def subscribe_direct(
        self,
        module_id: str,
        event_types: list[str],
        callback: Any,
    ) -> str:
        """Subscribe with a direct async Python callback (for SYSTEM modules).

        No HTTP webhook — the callback is called directly in the dispatch loop.
        Returns the subscription ID.
        """
        sub_id = str(uuid.uuid4())
        self._direct_subs[sub_id] = DirectSubscription(
            subscription_id=sub_id,
            module_id=module_id,
            event_types=event_types,
            callback=callback,
        )
        logger.info(
            "Direct subscription created: %s → in-process callback for %s",
            sub_id,
            event_types,
        )
        return sub_id

    def unsubscribe_direct(self, subscription_id: str) -> None:
        self._direct_subs.pop(subscription_id, None)

    async def publish(self, type: str, source: str, payload: dict[str, Any]) -> Event:
        event = Event.create(type=type, source=source, payload=payload)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("EventBus queue full, dropping oldest event")
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._queue.put_nowait(event)
        logger.debug("Event published: %s from %s", type, source)
        return event

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._deliver(event)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("EventBus dispatch error: %s", e, exc_info=True)

    async def _deliver(self, event: Event) -> None:
        matched = [
            sub
            for sub in self._subscriptions.values()
            if event.type in sub.event_types or "*" in sub.event_types
        ]
        for sub in matched:
            asyncio.create_task(
                self._deliver_to_webhook(event, sub),
                name=f"webhook-{sub.subscription_id}-{event.event_id[:8]}",
            )

        # Dispatch to in-process (SYSTEM module) callbacks
        for sub in list(self._direct_subs.values()):
            if event.type in sub.event_types or "*" in sub.event_types:
                asyncio.create_task(
                    sub.callback(event),
                    name=f"direct-{sub.module_id}-{event.event_id[:8]}",
                )

    async def _deliver_to_webhook(self, event: Event, sub: Subscription) -> None:
        body = event.to_dict()
        body_bytes = json.dumps(body).encode()

        headers = {
            "Content-Type": "application/json",
            "X-Selena-Event": event.type,
        }
        if sub.secret:
            sig = hmac.new(
                sub.secret.encode(), body_bytes, hashlib.sha256
            ).hexdigest()
            headers["X-Selena-Signature"] = f"sha256={sig}"

        for attempt in range(1, WEBHOOK_RETRY_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SEC) as client:
                    resp = await client.post(
                        sub.webhook_url, content=body_bytes, headers=headers
                    )
                    if resp.status_code < 500:
                        return
                    logger.warning(
                        "Webhook %s returned %s (attempt %d/%d)",
                        sub.webhook_url,
                        resp.status_code,
                        attempt,
                        WEBHOOK_RETRY_ATTEMPTS,
                    )
            except Exception as e:
                logger.warning(
                    "Webhook %s error (attempt %d/%d): %s",
                    sub.webhook_url,
                    attempt,
                    WEBHOOK_RETRY_ATTEMPTS,
                    e,
                )
            if attempt < WEBHOOK_RETRY_ATTEMPTS:
                await asyncio.sleep(2**attempt)

        logger.error(
            "Webhook %s failed after %d attempts for event %s",
            sub.webhook_url,
            WEBHOOK_RETRY_ATTEMPTS,
            event.event_id,
        )


# Singleton
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
