"""
core/eventbus/bus.py — Event Bus: asyncio.Queue dispatch + direct callbacks + Module Bus delivery

Delivery channels:
  1. DirectSubscription — in-process async callbacks (SYSTEM modules)
  2. Module Bus — WebSocket delivery to user modules (via core/module_bus.py)
  3. Webhook (deprecated) — kept for backward compat but will be removed
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.eventbus.types import CORE_EVENTS

logger = logging.getLogger(__name__)


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
    """Deprecated webhook subscription — kept for API compat."""
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
        """Deprecated: webhook subscriptions. Use Module Bus for user modules."""
        sub = Subscription(
            subscription_id=str(uuid.uuid4()),
            module_id=module_id,
            event_types=event_types,
            webhook_url=webhook_url,
            secret=secret,
        )
        self._subscriptions[sub.subscription_id] = sub
        logger.info(
            "Subscription created (deprecated webhook): %s → %s for %s",
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
        # Dispatch to in-process (SYSTEM module) callbacks
        for sub in list(self._direct_subs.values()):
            if event.type in sub.event_types or "*" in sub.event_types:
                asyncio.create_task(
                    sub.callback(event),
                    name=f"direct-{sub.module_id}-{event.event_id[:8]}",
                )

        # Deliver to bus-connected user modules
        try:
            from core.module_bus import get_module_bus
            await get_module_bus().deliver_event_to_bus(
                source=event.source,
                event_type=event.type,
                payload=event.payload,
            )
        except Exception as exc:
            logger.debug("Bus event delivery failed: %s", exc)


# Singleton
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
