"""
sdk/base_module.py — SmartHomeModule base class + decorators

Every SelenaCore module should inherit SmartHomeModule and use the provided
decorators to expose intents, scheduled tasks, and event handlers.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

CORE_API_BASE = os.environ.get("SELENA_CORE_API", "http://localhost:7070/api/v1")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")


def intent(pattern: str):
    """Decorator: register an async handler for an intent pattern (regex or keyword)."""
    def decorator(func: F) -> F:
        func._intent_pattern = pattern  # type: ignore[attr-defined]
        return func
    return decorator


def on_event(event_type: str):
    """Decorator: subscribe handler to an EventBus event type."""
    def decorator(func: F) -> F:
        func._event_type = event_type  # type: ignore[attr-defined]
        return func
    return decorator


def scheduled(cron: str):
    """Decorator: mark an async method to run on a cron schedule.

    Uses simple interval notation: 'every:30s', 'every:5m', 'every:1h'
    or standard cron '*/5 * * * *' (requires apscheduler).
    """
    def decorator(func: F) -> F:
        func._schedule = cron  # type: ignore[attr-defined]
        return func
    return decorator


class SmartHomeModule:
    """Base class for all SelenaCore modules.

    Subclass this, implement on_start()/on_stop(), and use decorators
    to register intent handlers, event handlers, and scheduled tasks.
    """

    name: str = "unnamed_module"
    version: str = "0.1.0"

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._intent_handlers: dict[str, Callable] = {}
        self._event_handlers: dict[str, Callable] = {}
        self._core_token = MODULE_TOKEN
        self._log = logging.getLogger(self.name)
        self._discover_handlers()

    def _discover_handlers(self) -> None:
        """Scan methods for decorator metadata."""
        for attr_name in dir(self.__class__):
            method = getattr(self, attr_name, None)
            if callable(method):
                if hasattr(method, "_intent_pattern"):
                    self._intent_handlers[method._intent_pattern] = method
                if hasattr(method, "_event_type"):
                    self._event_handlers[method._event_type] = method

    async def start(self) -> None:
        """Called by the module runner to start the module."""
        self._log.info("Module %s v%s starting", self.name, self.version)
        await self.on_start()
        # Auto-schedule tasks
        for attr_name in dir(self.__class__):
            method = getattr(self, attr_name, None)
            if callable(method) and hasattr(method, "_schedule"):
                task = asyncio.create_task(self._run_scheduled(method, method._schedule))
                self._tasks.append(task)

    async def stop(self) -> None:
        """Called to gracefully stop the module."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.on_stop()
        self._log.info("Module %s stopped", self.name)

    async def on_start(self) -> None:
        """Override in subclass: called when module starts."""

    async def on_stop(self) -> None:
        """Override in subclass: called when module stops."""

    async def handle_intent(self, intent_text: str, context: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch intent to registered handlers (exact or regex match)."""
        import re
        for pattern, handler in self._intent_handlers.items():
            if re.search(pattern, intent_text, re.IGNORECASE):
                try:
                    return await handler(intent_text, context)
                except Exception as exc:
                    self._log.error("Intent handler error: %s", exc)
                    return {"error": str(exc)}
        return None

    async def handle_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Dispatch event to registered handlers."""
        handler = self._event_handlers.get(event_type)
        if handler:
            try:
                await handler(payload)
            except Exception as exc:
                self._log.error("Event handler error for %s: %s", event_type, exc)

    async def publish_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Publish an event to the SelenaCore EventBus."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{CORE_API_BASE}/events/publish",
                    json={"event_type": event_type, "payload": payload},
                    headers={"Authorization": f"Bearer {self._core_token}"},
                )
                return resp.status_code == 200
        except Exception as exc:
            self._log.warning("Event publish failed: %s", exc)
            return False

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Fetch a device from the SelenaCore registry."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(
                    f"{CORE_API_BASE}/devices/{device_id}",
                    headers={"Authorization": f"Bearer {self._core_token}"},
                )
                return resp.json() if resp.status_code == 200 else None
        except Exception:
            return None

    @staticmethod
    async def _run_scheduled(method: Callable, schedule: str) -> None:
        """Simple interval scheduler from 'every:Ns/Nm/Nh' notation."""
        import re
        m = re.match(r"every:(\d+)(s|m|h)", schedule)
        if not m:
            return
        amount, unit = int(m.group(1)), m.group(2)
        interval = amount * {"s": 1, "m": 60, "h": 3600}[unit]
        while True:
            await asyncio.sleep(interval)
            try:
                await method()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Scheduled task error: %s", exc)
