"""
core/module_bus.py — Module Bus: unified WebSocket communication hub.

CAN-bus inspired architecture:
  - Core is the master node (priority, routing, access control)
  - Modules connect TO core via WebSocket (no ports needed)
  - All inter-module communication goes through core
  - Single protocol: intents, events, registration, health, API proxy

Connection lifecycle:
  Module connects → token auth → announce → ack → message loop → disconnect

Message types:
  announce / re_announce  — module registers capabilities
  intent / intent_response — addressed request/reply
  event                   — pub/sub through core
  ping / pong             — health check
  shutdown                — graceful core shutdown
  api_request / api_response — core API proxy with ACL
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import re
import time
import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


# ── Exceptions ───────────────────────────────────────────────────────────────


class BusDisconnected(Exception):
    """Raised when a pending future's module disconnects."""

    def __init__(self, module: str) -> None:
        super().__init__(f"Module '{module}' disconnected")
        self.module = module


class BusTimeout(Exception):
    """Raised when a request to a module times out."""


# ── DropOldestQueue ──────────────────────────────────────────────────────────


class DropOldestQueue:
    """asyncio.Queue with drop-oldest overflow (for event channel)."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self._maxsize = maxsize

    def put_nowait(self, item: str) -> None:
        while self._queue.full():
            try:
                self._queue.get_nowait()
                logger.debug("Bus event queue overflow — dropped oldest message")
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(item)

    async def get(self) -> str:
        return await self._queue.get()

    def get_nowait(self) -> str:
        return self._queue.get_nowait()

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class IntentEntry:
    """Compiled intent pattern in the sorted index."""
    module: str
    lang: str
    pattern: re.Pattern[str]
    priority: int
    raw_pattern: str


@dataclass
class BusConnection:
    """Active WebSocket connection to a module."""
    module: str
    ws: WebSocket
    capabilities: dict[str, Any]
    permissions: set[str]
    connected_at: float
    last_pong: float
    circuit_open_until: float = 0.0

    # Dual channels: critical (backpressure) + event (drop-oldest)
    critical_queue: asyncio.Queue[str] = field(
        default_factory=lambda: asyncio.Queue(maxsize=100),
    )
    event_queue: DropOldestQueue = field(
        default_factory=lambda: DropOldestQueue(1000),
    )


# ── API ACL ──────────────────────────────────────────────────────────────────

API_ACL: dict[str, list[tuple[str, str]]] = {
    "devices.read": [("GET", r"^/devices(/.*)?$")],
    "devices.control": [("POST", r"^/devices/[^/]+/control$")],
    "secrets.read": [("GET", r"^/secrets(/.*)?$")],
    "modules.list": [("GET", r"^/modules$")],
}


# ── ModuleBus ────────────────────────────────────────────────────────────────


class ModuleBus:
    """Central hub for all module WebSocket connections."""

    def __init__(self) -> None:
        self._connections: dict[str, BusConnection] = {}
        self._pending: dict[str, tuple[asyncio.Future[dict], str]] = {}
        self._intent_index: list[IntentEntry] = []

        self._conn_lock = asyncio.Lock()
        self._pending_lock = asyncio.Lock()
        self._pending_semaphore = asyncio.Semaphore(50)

        self._ping_interval = 15.0
        self._ping_miss_limit = 3
        self._intent_timeout = 10.0
        self._circuit_open_duration = 30.0
        self._max_fallthrough = 3

    # ── Connection lifecycle ─────────────────────────────────────────────

    async def handle_connection(self, ws: WebSocket, token: str) -> None:
        """Full connection lifecycle: announce → writer → message loop → cleanup."""

        # 1. Receive and validate announce
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        except (asyncio.TimeoutError, WebSocketDisconnect):
            try:
                await ws.close(code=4002, reason="announce_timeout")
            except Exception:
                pass
            return

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.close(code=4003, reason="invalid_json")
            return

        if msg.get("type") != "announce":
            await ws.close(code=4003, reason="expected_announce")
            return

        module_name = msg.get("module", "")
        if not module_name:
            await ws.close(code=4003, reason="missing_module_name")
            return

        capabilities = msg.get("capabilities", {})
        permissions = self._get_module_permissions(module_name)

        # Validate subscriptions vs permissions
        subs = capabilities.get("subscriptions", [])
        warnings: list[str] = []
        if "*" in subs and "events.subscribe_all" not in permissions:
            subs = [s for s in subs if s != "*"]
            capabilities["subscriptions"] = subs
            warnings.append("wildcard_subscription_denied: events.subscribe_all required")

        # Detect intent conflicts
        warnings.extend(
            self._detect_intent_conflicts(module_name, capabilities.get("intents", []))
        )

        # 2. Register connection
        conn = BusConnection(
            module=module_name,
            ws=ws,
            capabilities=capabilities,
            permissions=permissions,
            connected_at=time.monotonic(),
            last_pong=time.monotonic(),
        )
        async with self._conn_lock:
            # Disconnect existing connection if any (re-connect scenario)
            old = self._connections.pop(module_name, None)
            if old:
                logger.info("Bus: replacing existing connection for '%s'", module_name)
            self._connections[module_name] = conn
            self._rebuild_intent_index()

        # 3. Send ack
        bus_id = str(_uuid.uuid4())
        await ws.send_text(json.dumps({
            "type": "announce_ack",
            "status": "ok",
            "module": module_name,
            "bus_id": bus_id,
            "warnings": warnings,
        }))

        logger.info(
            "Bus: module '%s' connected (bus_id=%s, intents=%d, subs=%d)",
            module_name, bus_id[:8],
            len(capabilities.get("intents", [])),
            len(capabilities.get("subscriptions", [])),
        )

        # Persist to registered_modules DB
        asyncio.ensure_future(
            self._persist_module_connected(module_name, capabilities)
        )

        # 4. Start writer + ping tasks
        writer_task = asyncio.create_task(self._connection_writer(conn))
        ping_task = asyncio.create_task(self._ping_loop(conn))

        # 5. Message loop
        try:
            async for raw_msg in ws.iter_text():
                try:
                    parsed = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue
                await self._dispatch_message(module_name, parsed)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.error("Bus: message loop error for '%s': %s", module_name, exc)
        finally:
            # 6. Cleanup
            writer_task.cancel()
            ping_task.cancel()
            for t in (writer_task, ping_task):
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            await self._disconnect(module_name)

    async def _disconnect(self, module: str) -> None:
        """Remove connection and cancel pending futures."""
        async with self._conn_lock:
            conn = self._connections.pop(module, None)
            if conn:
                self._rebuild_intent_index()
                logger.info("Bus: module '%s' disconnected", module)
                asyncio.ensure_future(self._persist_module_disconnected(module))

        # Cancel all pending futures for this module
        async with self._pending_lock:
            to_remove = []
            for req_id, (future, mod) in self._pending.items():
                if mod == module and not future.done():
                    future.set_exception(BusDisconnected(module))
                    to_remove.append(req_id)
            for req_id in to_remove:
                del self._pending[req_id]

    async def shutdown_all(self, drain_ms: int = 5000) -> None:
        """Graceful shutdown: notify all modules, wait, close connections."""
        msg = json.dumps({
            "type": "shutdown",
            "reason": "core_restart",
            "drain_ms": drain_ms,
        })
        async with self._conn_lock:
            for conn in self._connections.values():
                try:
                    await conn.critical_queue.put(msg)
                except asyncio.QueueFull:
                    pass

        logger.info("Bus: shutdown drain %dms for %d modules", drain_ms, len(self._connections))
        await asyncio.sleep(drain_ms / 1000)

        async with self._conn_lock:
            for conn in list(self._connections.values()):
                try:
                    await conn.ws.close(code=1001, reason="core_shutdown")
                except Exception:
                    pass
            self._connections.clear()
            self._intent_index.clear()

    # ── Writer + Ping ────────────────────────────────────────────────────

    async def _connection_writer(self, conn: BusConnection) -> None:
        """Drain critical + event queues to WebSocket. Critical has priority."""
        try:
            while True:
                # Critical queue — non-blocking check
                try:
                    msg = conn.critical_queue.get_nowait()
                    await conn.ws.send_text(msg)
                    continue
                except asyncio.QueueEmpty:
                    pass

                # Event queue — with short timeout to check critical again
                try:
                    msg = await asyncio.wait_for(conn.event_queue.get(), timeout=0.1)
                    await conn.ws.send_text(msg)
                except asyncio.TimeoutError:
                    # Both empty — block on critical with longer timeout
                    try:
                        msg = await asyncio.wait_for(conn.critical_queue.get(), timeout=1.0)
                        await conn.ws.send_text(msg)
                    except asyncio.TimeoutError:
                        continue
        except (WebSocketDisconnect, asyncio.CancelledError, Exception):
            return

    async def _ping_loop(self, conn: BusConnection) -> None:
        """Periodic ping, disconnect on missed pongs."""
        miss_count = 0
        try:
            while True:
                await asyncio.sleep(self._ping_interval)
                try:
                    await conn.critical_queue.put(
                        json.dumps({"type": "ping", "ts": time.time()})
                    )
                except asyncio.QueueFull:
                    pass

                # Check pong freshness
                elapsed = time.monotonic() - conn.last_pong
                if elapsed > self._ping_interval * self._ping_miss_limit:
                    miss_count += 1
                    if miss_count >= self._ping_miss_limit:
                        logger.warning(
                            "Bus: module '%s' missed %d pings, disconnecting",
                            conn.module, miss_count,
                        )
                        try:
                            await conn.ws.close(code=4004, reason="ping_timeout")
                        except Exception:
                            pass
                        return
                else:
                    miss_count = 0
        except asyncio.CancelledError:
            return

    # ── Message dispatch ─────────────────────────────────────────────────

    async def _dispatch_message(self, module: str, msg: dict[str, Any]) -> None:
        """Route an incoming message from a module."""
        msg_type = msg.get("type", "")

        if msg_type == "pong":
            conn = self._connections.get(module)
            if conn:
                conn.last_pong = time.monotonic()

        elif msg_type == "intent_response":
            await self._handle_intent_response(msg)

        elif msg_type == "event":
            await self._handle_module_event(module, msg)

        elif msg_type == "api_request":
            await self._handle_api_request(module, msg)

        elif msg_type == "api_response":
            await self._handle_api_response(msg)

        elif msg_type == "re_announce":
            await self._handle_re_announce(module, msg)

        else:
            logger.debug("Bus: unknown message type '%s' from '%s'", msg_type, module)

    async def _handle_intent_response(self, msg: dict[str, Any]) -> None:
        """Resolve a pending intent future."""
        req_id = msg.get("id")
        if not req_id:
            return
        async with self._pending_lock:
            entry = self._pending.pop(req_id, None)
        if entry is None:
            return  # late response after timeout — ignore
        future, _ = entry
        if not future.done():
            future.set_result(msg)

    async def _handle_api_response(self, msg: dict[str, Any]) -> None:
        """Resolve a pending core→module API request future."""
        req_id = msg.get("id")
        if not req_id:
            return
        async with self._pending_lock:
            entry = self._pending.pop(req_id, None)
        if entry is None:
            return
        future, _ = entry
        if not future.done():
            future.set_result(msg)

    async def _handle_module_event(self, source: str, msg: dict[str, Any]) -> None:
        """Validate permissions and route event to subscribers."""
        payload = msg.get("payload", {})
        event_type = payload.get("event_type", "")
        data = payload.get("data", {})

        # Permission check: event_type must be in module's publishes
        conn = self._connections.get(source)
        if conn:
            publishes = conn.capabilities.get("publishes", [])
            if publishes and event_type not in publishes:
                logger.warning(
                    "Bus: module '%s' tried to publish '%s' not in publishes — dropped",
                    source, event_type,
                )
                return

        # Route to EventBus (system modules + other bus subscribers)
        try:
            from core.eventbus.bus import get_event_bus
            await get_event_bus().publish(
                type=event_type, source=source, payload=data,
            )
        except Exception as exc:
            logger.error("Bus: event publish to EventBus failed: %s", exc)

        # Deliver to bus subscribers
        event_msg = json.dumps({
            "type": "event",
            "payload": {
                "event_id": str(_uuid.uuid4()),
                "event_type": event_type,
                "source": source,
                "data": data,
                "timestamp": time.time(),
            },
        })
        for name, c in self._connections.items():
            if name == source:
                continue
            subs = c.capabilities.get("subscriptions", [])
            if any(_matches_subscription(event_type, pat) for pat in subs):
                c.event_queue.put_nowait(event_msg)

    async def _handle_api_request(self, module: str, msg: dict[str, Any]) -> None:
        """Proxy API request with ACL check."""
        req_id = msg.get("id", "")
        method = msg.get("method", "GET").upper()
        path = msg.get("path", "")
        body = msg.get("body")

        conn = self._connections.get(module)
        if not conn:
            return

        # ACL check
        allowed = False
        for perm in conn.permissions:
            rules = API_ACL.get(perm, [])
            for allowed_method, pattern in rules:
                if method == allowed_method and re.match(pattern, path):
                    allowed = True
                    break
            if allowed:
                break

        if not allowed:
            resp = json.dumps({
                "type": "api_response",
                "id": req_id,
                "status": 403,
                "body": {"error": f"Permission denied for {method} {path}"},
            })
            try:
                await conn.critical_queue.put(resp)
            except asyncio.QueueFull:
                pass
            return

        # Forward to internal API handler
        try:
            result = await self._execute_api_request(method, path, body)
            resp = json.dumps({
                "type": "api_response",
                "id": req_id,
                "status": 200,
                "body": result,
            })
        except Exception as exc:
            resp = json.dumps({
                "type": "api_response",
                "id": req_id,
                "status": 500,
                "body": {"error": str(exc)},
            })

        try:
            await conn.critical_queue.put(resp)
        except asyncio.QueueFull:
            pass

    async def _execute_api_request(
        self, method: str, path: str, body: Any,
    ) -> dict[str, Any]:
        """Execute an internal API request (simplified — delegates to services)."""
        # Basic device registry access
        if path.startswith("/devices"):
            from core.module_loader.sandbox import get_sandbox
            from core.registry.service import DeviceRegistry

            sandbox = get_sandbox()
            sf = sandbox._session_factory
            if sf is None:
                return {"error": "Database not ready"}
            async with sf() as session:
                registry = DeviceRegistry(session)
                if method == "GET" and path == "/devices":
                    devices = await registry.get_all()
                    return {"devices": [d.to_dict() for d in devices] if hasattr(devices[0], 'to_dict') else []}
        return {"error": f"Not implemented: {method} {path}"}

    async def _handle_re_announce(self, module: str, msg: dict[str, Any]) -> None:
        """Hot-reload module capabilities without reconnect."""
        capabilities = msg.get("capabilities", {})
        conn = self._connections.get(module)
        if not conn:
            return

        # Validate subscriptions
        subs = capabilities.get("subscriptions", [])
        warnings: list[str] = []
        if "*" in subs and "events.subscribe_all" not in conn.permissions:
            subs = [s for s in subs if s != "*"]
            capabilities["subscriptions"] = subs
            warnings.append("wildcard_subscription_denied")

        warnings.extend(
            self._detect_intent_conflicts(module, capabilities.get("intents", []))
        )

        # Atomic update
        async with self._conn_lock:
            conn.capabilities = capabilities
            self._rebuild_intent_index()

        ack = json.dumps({
            "type": "announce_ack",
            "status": "ok",
            "module": module,
            "bus_id": str(_uuid.uuid4()),
            "warnings": warnings,
        })
        try:
            await conn.critical_queue.put(ack)
        except asyncio.QueueFull:
            pass

        logger.info("Bus: module '%s' re-announced capabilities", module)

    # ── Intent routing ───────────────────────────────────────────────────

    async def route_intent(
        self, text: str, lang: str, context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Match text against intent index and route to module.

        Returns:
            {"handled": True, "module": "name", "tts_text": "...", "data": {...}}
            {"handled": False, "reason": "circuit_open"|"timeout", "module": "name"}
            None — no matching module found
        """
        matches = self._match_intents(text, lang)
        if not matches:
            return None

        async with self._pending_semaphore:
            for entry in matches[:self._max_fallthrough]:
                # Circuit breaker check
                if self._is_circuit_open(entry.module):
                    return {
                        "handled": False,
                        "reason": "circuit_open",
                        "module": entry.module,
                    }

                conn = self._connections.get(entry.module)
                if not conn:
                    continue

                req_id = str(_uuid.uuid4())
                future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()

                async with self._pending_lock:
                    self._pending[req_id] = (future, entry.module)

                msg = json.dumps({
                    "type": "intent",
                    "id": req_id,
                    "payload": {"text": text, "lang": lang, "context": context},
                })
                try:
                    await conn.critical_queue.put(msg)
                except asyncio.QueueFull:
                    async with self._pending_lock:
                        self._pending.pop(req_id, None)
                    continue

                try:
                    result = await asyncio.wait_for(future, timeout=self._intent_timeout)
                except asyncio.TimeoutError:
                    self._open_circuit(entry.module)
                    async with self._pending_lock:
                        self._pending.pop(req_id, None)
                    return {
                        "handled": False,
                        "reason": "timeout",
                        "module": entry.module,
                    }
                except BusDisconnected:
                    return {
                        "handled": False,
                        "reason": "disconnected",
                        "module": entry.module,
                    }

                payload = result.get("payload", {})
                if payload.get("handled"):
                    return {"handled": True, "module": entry.module, **payload}
                # handled=False → fallthrough to next match

        return None  # all attempts exhausted

    def _match_intents(self, text: str, lang: str) -> list[IntentEntry]:
        """Find matching intent entries for text in given language."""
        text_lower = text.lower().strip()
        matches = []
        for entry in self._intent_index:
            if entry.lang != lang:
                continue
            if entry.pattern.search(text_lower):
                matches.append(entry)
        # Fallback: try English if no matches in requested language
        if not matches and lang != "en":
            for entry in self._intent_index:
                if entry.lang != "en":
                    continue
                if entry.pattern.search(text_lower):
                    matches.append(entry)
        return matches

    def _rebuild_intent_index(self) -> None:
        """Rebuild sorted intent index from all connections. Must hold _conn_lock."""
        entries: list[IntentEntry] = []
        for module, conn in self._connections.items():
            for intent_def in conn.capabilities.get("intents", []):
                priority = intent_def.get("priority", 50)
                for lang, patterns in intent_def.get("patterns", {}).items():
                    for p in patterns:
                        try:
                            compiled = re.compile(p, re.IGNORECASE)
                        except re.error:
                            logger.warning(
                                "Bus: invalid regex '%s' from module '%s'", p, module,
                            )
                            continue
                        entries.append(IntentEntry(
                            module=module,
                            lang=lang,
                            pattern=compiled,
                            priority=priority,
                            raw_pattern=p,
                        ))
        # Sort: priority ASC → pattern length DESC → module name ASC
        entries.sort(key=lambda e: (e.priority, -len(e.raw_pattern), e.module))
        self._intent_index = entries

    def _detect_intent_conflicts(
        self, new_module: str, new_intents: list[dict[str, Any]],
    ) -> list[str]:
        """Detect potential pattern overlaps with existing modules."""
        warnings: list[str] = []
        for intent_def in new_intents:
            for lang, patterns in intent_def.get("patterns", {}).items():
                for pattern in patterns:
                    for existing in self._intent_index:
                        if existing.module == new_module or existing.lang != lang:
                            continue
                        if pattern in existing.raw_pattern or existing.raw_pattern in pattern:
                            warnings.append(
                                f"intent_conflict:{pattern}:{existing.module}"
                            )
        return warnings

    # ── Circuit breaker ──────────────────────────────────────────────────

    def _is_circuit_open(self, module: str) -> bool:
        conn = self._connections.get(module)
        if conn and conn.circuit_open_until > time.monotonic():
            return True
        return False

    def _open_circuit(self, module: str) -> None:
        conn = self._connections.get(module)
        if conn:
            conn.circuit_open_until = time.monotonic() + self._circuit_open_duration
            logger.warning(
                "Bus: circuit opened for '%s' (%.0fs)",
                module, self._circuit_open_duration,
            )

    # ── Event delivery ───────────────────────────────────────────────────

    async def deliver_event_to_bus(
        self, source: str, event_type: str, payload: dict[str, Any],
    ) -> None:
        """Deliver an EventBus event to bus-connected modules (called by EventBus)."""
        event_msg = json.dumps({
            "type": "event",
            "payload": {
                "event_id": str(_uuid.uuid4()),
                "event_type": event_type,
                "source": source,
                "data": payload,
                "timestamp": time.time(),
            },
        })
        for name, conn in self._connections.items():
            if name == source:
                continue
            subs = conn.capabilities.get("subscriptions", [])
            if any(_matches_subscription(event_type, pat) for pat in subs):
                conn.event_queue.put_nowait(event_msg)

    # ── Permissions ──────────────────────────────────────────────────────

    def _get_module_permissions(self, module: str) -> set[str]:
        """Load permissions from module manifest (stored in sandbox registry)."""
        try:
            from core.module_loader.sandbox import get_sandbox
            sandbox = get_sandbox()
            info = sandbox._modules.get(module)
            if info and info.manifest:
                return set(info.manifest.get("permissions", []))
        except Exception:
            pass
        return set()

    # ── Core→Module API proxy ───────────────────────────────────────────

    async def send_api_request(
        self,
        module: str,
        method: str,
        path: str,
        body: Any = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send an API request from core to a bus-connected module.

        Used by the UI proxy to forward requests to modules.
        Raises ``TimeoutError`` on timeout, ``KeyError`` if not connected.
        """
        conn = self._connections.get(module)
        if not conn:
            raise KeyError(f"Module '{module}' not connected")

        req_id = str(_uuid.uuid4())
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()

        async with self._pending_lock:
            self._pending[req_id] = (future, module)

        msg = json.dumps({
            "type": "api_request",
            "id": req_id,
            "method": method.upper(),
            "path": path,
            "body": body,
        })
        try:
            await conn.critical_queue.put(msg)
        except asyncio.QueueFull:
            async with self._pending_lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"Queue full for module '{module}'")

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            async with self._pending_lock:
                self._pending.pop(req_id, None)
            raise TimeoutError(f"API request to '{module}' timed out") from None

        return result.get("payload", result.get("body", {}))

    # ── DB persistence ────────────────────────────────────────────────────

    async def _persist_module_connected(
        self, module: str, capabilities: dict[str, Any],
    ) -> None:
        """Save module to registered_modules DB on connect."""
        try:
            from core.module_loader.sandbox import get_sandbox
            from core.registry.models import RegisteredModule
            from sqlalchemy import select

            sf = get_sandbox()._session_factory
            if sf is None:
                return

            # Extract intent names and description from capabilities
            intent_names: list[str] = []
            for intent_def in capabilities.get("intents", []):
                name = intent_def.get("name", "")
                if name:
                    intent_names.append(name)
            description = capabilities.get("description", module)

            async with sf() as session:
                async with session.begin():
                    result = await session.execute(
                        select(RegisteredModule).where(RegisteredModule.name == module)
                    )
                    existing = result.scalar_one_or_none()

                    if existing:
                        existing.connected = True
                        existing.enabled = True
                        if intent_names:
                            existing.intents = json.dumps(intent_names)
                        if description:
                            existing.description_user = description
                            existing.description_en = description
                        from datetime import datetime, timezone
                        existing.last_seen = datetime.now(timezone.utc)
                    else:
                        mod = RegisteredModule(
                            name=module,
                            name_user=module,
                            name_en=module,
                            description_user=description,
                            description_en=description,
                            intents=json.dumps(intent_names),
                            connected=True,
                            enabled=True,
                        )
                        from datetime import datetime, timezone
                        mod.last_seen = datetime.now(timezone.utc)
                        session.add(mod)

            # Invalidate LLM prompt cache
            try:
                from system_modules.llm_engine.intent_router import get_intent_router
                get_intent_router().refresh_system_prompt()
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Failed to persist module connect for '%s': %s", module, exc)

    async def _persist_module_disconnected(self, module: str) -> None:
        """Mark module as disconnected in DB."""
        try:
            from core.module_loader.sandbox import get_sandbox
            from core.registry.models import RegisteredModule
            from sqlalchemy import select

            sf = get_sandbox()._session_factory
            if sf is None:
                return

            async with sf() as session:
                async with session.begin():
                    result = await session.execute(
                        select(RegisteredModule).where(RegisteredModule.name == module)
                    )
                    existing = result.scalar_one_or_none()
                    if existing:
                        existing.connected = False

            # Invalidate LLM prompt cache
            try:
                from system_modules.llm_engine.intent_router import get_intent_router
                get_intent_router().refresh_system_prompt()
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Failed to persist module disconnect for '%s': %s", module, exc)

    # ── Status ───────────────────────────────────────────────────────────

    def is_connected(self, module: str) -> bool:
        return module in self._connections

    def list_modules(self) -> list[dict[str, Any]]:
        return [
            {
                "module": conn.module,
                "connected_at": conn.connected_at,
                "capabilities": conn.capabilities,
                "permissions": list(conn.permissions),
                "circuit_open": self._is_circuit_open(conn.module),
            }
            for conn in self._connections.values()
        ]

    def get_module_capabilities(self, module: str) -> dict[str, Any] | None:
        conn = self._connections.get(module)
        return conn.capabilities if conn else None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _matches_subscription(event_type: str, pattern: str) -> bool:
    """Wildcard matching: 'device.*' matches 'device.state_changed'."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return event_type == prefix or event_type.startswith(prefix + ".")
    return event_type == pattern


# ── Singleton ────────────────────────────────────────────────────────────────

_bus: ModuleBus | None = None


def get_module_bus() -> ModuleBus:
    global _bus
    if _bus is None:
        _bus = ModuleBus()
    return _bus
