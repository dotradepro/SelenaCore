"""
core/module_loader/system_module.py — Base class for in-process system modules.

System modules (type=SYSTEM) run INSIDE the smarthome-core container as Python
objects loaded via importlib — NOT as separate subprocesses or Docker containers.

Architecture:
  - SYSTEM modules  → importlib, in-process, ~0 MB RAM overhead
  - User modules    → subprocess, WebSocket Module Bus

Subclass contract:
  1. Set class attribute ``name`` matching manifest.json "name"
  2. Implement ``start()`` and ``stop()``
  3. Optionally implement ``get_router()`` → APIRouter mounted at
     /api/ui/modules/{name}/
  4. In __init__.py: export ``module_class = <YourClass>``
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, AsyncGenerator, Callable

if TYPE_CHECKING:
    from fastapi import APIRouter
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from core.eventbus.bus import EventBus

logger = logging.getLogger(__name__)


class SystemModule(ABC):
    """Base class for SYSTEM-type modules — runs inside core process.

    Do NOT launch system modules as uvicorn subprocesses.
    Do NOT specify ``port`` in their manifest.json.
    Use ``self.publish()``, ``self.subscribe()``, ``self.fetch_devices()``, etc.
    for all communication with core instead of HTTP calls.
    """

    name: str  # Must match manifest.json "name", e.g. "weather-service"

    def __init__(self) -> None:
        self._bus: "EventBus | None" = None
        self._session_factory: "async_sessionmaker | None" = None
        self._direct_sub_ids: list[str] = []

    def setup(self, bus: "EventBus", session_factory: "async_sessionmaker") -> None:
        """Inject core services. Called by loader before start()."""
        self._bus = bus
        self._session_factory = session_factory

    @abstractmethod
    async def start(self) -> None:
        """Start the module: initialize service, subscribe to events."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the module: cancel background tasks, release resources."""
        ...

    def get_router(self) -> "APIRouter | None":
        """Return a FastAPI APIRouter mounted at /api/ui/modules/{name}/.

        Override this method to expose REST endpoints.
        The router is mounted by the Plugin Manager right after startup.
        """
        return None

    # ── EventBus helpers ─────────────────────────────────────────────────────

    def subscribe(self, event_types: list[str], callback: Callable) -> str:
        """Subscribe to EventBus events with a direct async Python callback.

        The callback signature must be: ``async def handler(event: Event) -> None``
        Returns the subscription ID.
        """
        if self._bus is None:
            raise RuntimeError(
                f"Module '{self.name}': setup() must be called before subscribe()"
            )
        sub_id = self._bus.subscribe_direct(self.name, event_types, callback)
        self._direct_sub_ids.append(sub_id)
        return sub_id

    async def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        """Publish an event to the EventBus."""
        if self._bus is None:
            raise RuntimeError(
                f"Module '{self.name}': setup() must be called before publish()"
            )
        await self._bus.publish(type=event_type, source=self.name, payload=payload)

    # ── TTS speech helper ──────────────────────────────────────────────────────

    async def speak(self, text: str, *, timeout: float = 30.0) -> None:
        """Publish voice.speak and WAIT for TTS to complete (voice.speak_done).

        This ensures speech finishes before the caller continues, so actions
        (like starting radio playback) happen AFTER the voice announcement.
        """
        if not text or self._bus is None:
            return

        speech_id = str(uuid.uuid4())
        done = asyncio.Event()

        async def _on_speak_done(event: Any) -> None:
            if event.payload.get("speech_id") == speech_id:
                done.set()

        sub_id = self.subscribe(["voice.speak_done"], _on_speak_done)
        try:
            await self.publish("voice.speak", {"text": text, "speech_id": speech_id})
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug("speak() timeout for '%s' (speech_id=%s)", text[:40], speech_id)
        finally:
            if self._bus:
                self._bus.unsubscribe_direct(sub_id)
            if sub_id in self._direct_sub_ids:
                self._direct_sub_ids.remove(sub_id)

    async def speak_action(
        self, intent: str, context: dict, *, timeout: float = 30.0,
    ) -> None:
        """Publish voice.speak with action context for LLM to generate TTS text.

        Unlike speak(text), this does NOT pass pre-written text.  Instead it
        sends a structured *action_context* dict that VoiceCore forwards to
        LLM, which generates a natural-language response in the TTS language.
        """
        if self._bus is None:
            return

        speech_id = str(uuid.uuid4())
        done = asyncio.Event()

        async def _on_done(event: Any) -> None:
            if event.payload.get("speech_id") == speech_id:
                done.set()

        sub_id = self.subscribe(["voice.speak_done"], _on_done)
        try:
            await self.publish("voice.speak", {
                "action_context": {"intent": intent, **context},
                "speech_id": speech_id,
            })
            await asyncio.wait_for(done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.debug(
                "speak_action() timeout intent=%s (speech_id=%s)", intent, speech_id,
            )
        finally:
            if self._bus:
                self._bus.unsubscribe_direct(sub_id)
            if sub_id in self._direct_sub_ids:
                self._direct_sub_ids.remove(sub_id)

    async def request_clarification(
        self,
        pending_intent: str,
        pending_params: dict[str, Any],
        *,
        question_key: str,
        reason: str = "missing_param",
        hint: str | None = None,
        param_name: str | None = None,
        allowed_values: list[str] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        rooms: list[str] | None = None,
        timeout_sec: float = 10.0,
    ) -> None:
        """Ask the user a clarifying question mid-command.

        Modules call this from their voice-intent handler when they've
        detected a missing/ambiguous parameter the classifier couldn't
        supply (e.g. ``device.set_temperature`` with no numeric value).
        Emits a ``voice.clarification_request`` event — VoiceCore reads
        it, speaks the prompt via ``action_phrasing`` (``question_key``
        is a key registered there, e.g. ``"clarify.missing_value"``),
        opens the mic for ``timeout_sec`` and routes the reply through
        ``IntentRouter.route_clarification()`` against this pending
        context. On successful match the original intent re-fires with
        the merged params.

        Does NOT wait — the caller should ``return`` immediately so
        VoiceCore's state machine can transition to
        ``AWAITING_CLARIFICATION``.
        """
        if self._bus is None:
            return
        payload = {
            "reason": reason,
            "question_key": question_key,
            "hint": hint,
            "param_name": param_name,
            "allowed_values": allowed_values,
            "candidates": candidates,
            "rooms": rooms,
            "pending_intent": pending_intent,
            "pending_params": pending_params,
            "timeout_sec": timeout_sec,
        }
        await self.publish("voice.clarification_request", payload)

    # ── DeviceRegistry helpers ────────────────────────────────────────────────

    @asynccontextmanager
    async def _db_session(self) -> "AsyncGenerator[AsyncSession, None]":
        """Context manager yielding a fresh SQLAlchemy session."""
        if self._session_factory is None:
            raise RuntimeError(
                f"Module '{self.name}': setup() must be called first"
            )
        async with self._session_factory() as session:
            yield session

    async def fetch_devices(self) -> list[dict[str, Any]]:
        """Return all registered devices as plain dicts."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            devices = await registry.get_all()
            return [_device_to_dict(d) for d in devices]

    async def patch_device_state(self, device_id: str, state: dict[str, Any]) -> None:
        """Update a device's state in the registry and commit."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            await registry.update_state(device_id, state)
            await session.commit()

    async def get_device_state(self, device_id: str) -> dict[str, Any]:
        """Return the state dict of a single device."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            device = await registry.get(device_id)
            if device is None:
                return {}
            return json.loads(device.state)

    async def register_device(
        self,
        name: str,
        type: str,
        protocol: str,
        capabilities: list[str],
        meta: dict[str, Any],
    ) -> str:
        """Register a new device and return its device_id."""
        from core.registry.service import DeviceRegistry

        async with self._db_session() as session:
            registry = DeviceRegistry(session)
            device = await registry.create(
                name=name,
                type=type,
                protocol=protocol,
                capabilities=capabilities,
                meta=meta,
            )
            await session.commit()
            return device.device_id

    # ── Router helpers ──────────────────────────────────────────────────────

    def _register_html_routes(self, router: "APIRouter", module_file: str) -> None:
        """Register /widget and /settings HTML endpoints on *router*.

        Call at the end of ``get_router()`` to avoid repeating the same
        4-line pattern in every system module::

            self._register_html_routes(router, __file__)
        """
        from pathlib import Path

        from fastapi.responses import HTMLResponse

        parent = Path(module_file).parent

        # No-cache headers so edits to widget.html / settings.html are picked
        # up immediately on browser refresh (dev and prod both edit these
        # files directly via volume-mount; stale cache hides changes).
        _NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}

        @router.get("/widget", response_class=HTMLResponse)
        async def _widget():
            f = parent / "widget.html"
            return HTMLResponse(
                f.read_text() if f.exists() else "<p>widget.html not found</p>",
                headers=_NO_CACHE,
            )

        @router.get("/settings", response_class=HTMLResponse)
        async def _settings_page():
            f = parent / "settings.html"
            return HTMLResponse(
                f.read_text() if f.exists() else "<p>settings.html not found</p>",
                headers=_NO_CACHE,
            )

    # ── Intent ownership ──────────────────────────────────────────────────
    #
    # Subclasses that own voice intents declare them via two class attrs:
    #
    #     OWNED_INTENTS = ["media.play_radio", "media.pause", ...]
    #     _OWNED_INTENT_META = {
    #         "media.play_radio": dict(
    #             noun_class="MEDIA", verb="play", priority=100,
    #             description="...",
    #             entity_types=None,      # optional
    #         ),
    #         ...
    #     }
    #
    # and call ``await self._claim_intent_ownership()`` inside ``start()``.
    # Idempotent — safe to run on every boot. See system_modules/
    # device_control/module.py for the canonical usage.
    OWNED_INTENTS: list[str] = []
    _OWNED_INTENT_META: dict[str, dict] = {}

    async def _claim_intent_ownership(self) -> None:
        """Register this module's intents in intent_definitions.

        1. UPDATE module=self.name on every OWNED_INTENTS row that exists
           (so legacy seed-script rows get re-homed).
        2. UPDATE description + entity_types from _OWNED_INTENT_META so
           the module is the source of truth for LLM prompt wording.
        3. INSERT any missing rows from _OWNED_INTENT_META.
        """
        if not self.OWNED_INTENTS or self._session_factory is None:
            return
        try:
            from sqlalchemy import select, update
            from core.registry.models import IntentDefinition

            async with self._session_factory() as session:
                await session.execute(
                    update(IntentDefinition)
                    .where(IntentDefinition.intent.in_(self.OWNED_INTENTS))
                    .values(module=self.name)
                )
                for intent_name, meta in self._OWNED_INTENT_META.items():
                    ent = meta.get("entity_types")
                    payload = json.dumps(list(ent)) if ent else None
                    await session.execute(
                        update(IntentDefinition)
                        .where(IntentDefinition.intent == intent_name)
                        .values(
                            entity_types=payload,
                            description=meta["description"],
                        )
                    )
                existing = {
                    row[0] for row in (await session.execute(
                        select(IntentDefinition.intent).where(
                            IntentDefinition.intent.in_(self.OWNED_INTENTS)
                        )
                    )).all()
                }
                for intent_name in self.OWNED_INTENTS:
                    if intent_name in existing:
                        continue
                    meta = self._OWNED_INTENT_META.get(intent_name)
                    if meta is None:
                        continue
                    ent = meta.get("entity_types")
                    session.add(IntentDefinition(
                        intent=intent_name,
                        module=self.name,
                        noun_class=meta.get("noun_class", "GENERIC"),
                        verb=meta.get("verb", ""),
                        priority=meta.get("priority", 100),
                        description=meta["description"],
                        source="module",
                        entity_types=json.dumps(list(ent)) if ent else None,
                    ))
                await session.commit()
            logger.info(
                "%s: claimed ownership of %d intent(s)",
                self.name, len(self.OWNED_INTENTS),
            )
        except Exception as exc:
            logger.warning(
                "%s: intent ownership claim failed: %s", self.name, exc,
            )

    def _register_health_endpoint(self, router: "APIRouter") -> None:
        """Register a minimal ``GET /health`` on *router*.

        Only use this for modules whose health is the simple
        ``{"status": "ok", "module": name}`` pattern. Modules that add
        extra status fields should keep their own endpoint.
        """
        svc = self

        @router.get("/health")
        async def _health():
            return {"status": "ok", "module": svc.name}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _cleanup_subscriptions(self) -> None:
        """Unsubscribe all direct EventBus subscriptions."""
        if self._bus:
            for sub_id in self._direct_sub_ids:
                self._bus.unsubscribe_direct(sub_id)
        self._direct_sub_ids.clear()


def _device_to_dict(device: Any) -> dict[str, Any]:
    """Convert a Device ORM object to a plain dict (no SQLAlchemy state)."""
    return {
        "device_id": device.device_id,
        "name": device.name,
        "type": device.type,
        "protocol": device.protocol,
        "state": json.loads(device.state),
        "capabilities": json.loads(device.capabilities),
        "last_seen": device.last_seen.timestamp() if device.last_seen else None,
        "module_id": device.module_id,
        "meta": json.loads(device.meta),
    }
