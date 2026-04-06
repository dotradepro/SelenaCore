"""
sdk/base_module.py — SmartHomeModule base class + decorators (WebSocket bus client)

Every SelenaCore module inherits SmartHomeModule and uses decorators to expose
intents, event handlers, and scheduled tasks.  Communication goes through the
core Module Bus (WebSocket), not per-module HTTP servers.

Connection lifecycle (mirrors core/module_bus.py):
  connect(token) → announce → ack → flush_outbox + message_loop → reconnect
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import random
import re
import time
import uuid as _uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ── Environment ─────────────────────────────────────────────────────────────

SELENA_BUS_URL = os.environ.get("SELENA_BUS_URL", "ws://localhost/api/v1/bus")
MODULE_TOKEN = os.environ.get("MODULE_TOKEN", "")
MODULE_DIR = os.environ.get("MODULE_DIR", "")

# ── Decorators ──────────────────────────────────────────────────────────────


def intent(
    pattern: str,
    order: int = 50,
    name: str = "",
    description: str = "",
):
    """Register an async handler for an intent pattern (regex).

    ``order`` controls priority in the bus intent index (lower = higher priority).
    Range: 0-29 system, 30-49 core, 50-99 user modules.

    ``name`` — intent name for LLM prompt catalog (e.g. "email.check_inbox").
    ``description`` — human-readable description for LLM context.
    """
    def decorator(func: F) -> F:
        func._intent_pattern = pattern   # type: ignore[attr-defined]
        func._intent_order = order       # type: ignore[attr-defined]
        func._intent_name = name         # type: ignore[attr-defined]
        func._intent_description = description  # type: ignore[attr-defined]
        return func
    return decorator


def on_event(event_type: str):
    """Subscribe handler to an EventBus event type (supports wildcards like ``device.*``)."""
    def decorator(func: F) -> F:
        func._event_type = event_type  # type: ignore[attr-defined]
        return func
    return decorator


def scheduled(cron: str):
    """Mark an async method to run on a cron schedule.

    Uses simple interval notation: ``'every:30s'``, ``'every:5m'``, ``'every:1h'``
    or standard cron ``'*/5 * * * *'`` (requires apscheduler).
    """
    def decorator(func: F) -> F:
        func._schedule = cron  # type: ignore[attr-defined]
        return func
    return decorator


# ── SmartHomeModule ─────────────────────────────────────────────────────────

# Fatal close codes from core — do NOT reconnect
_FATAL_REASONS = {"invalid_token", "permission_denied"}

# Reconnect limits
_RECONNECT_BASE = 1.0
_RECONNECT_MAX = 60.0


class SmartHomeModule:
    """Base class for all SelenaCore modules (WebSocket bus client)."""

    name: str = "unnamed_module"
    version: str = "0.1.0"

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name == "unnamed_module":
            cls.name = cls.__name__

    def __init__(self) -> None:
        self._log = logging.getLogger(self.name)
        self._token = MODULE_TOKEN
        self._bus_url = SELENA_BUS_URL
        self._bus_id: str | None = None
        self._ws: Any | None = None  # websockets.WebSocketClientProtocol
        self._connected = asyncio.Event()
        self._stopped = False
        self._shutdown_event = asyncio.Event()
        self._drain_ms = 0

        # Handlers discovered from decorators
        # (pattern, order, method, name, description)
        self._intent_handlers: list[tuple[str, int, Callable, str, str]] = []
        self._event_handlers: dict[str, Callable] = {}
        self._tasks: list[asyncio.Task] = []

        # Outbox for events published while disconnected
        self._outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=500)

        # Pending API request futures
        self._pending: dict[str, asyncio.Future[dict]] = {}

        # i18n
        self._locale_strings: dict[str, dict[str, str]] = {}  # {lang: {key: template}}

        self._discover_handlers()
        self._register_locales()

    # ── Handler discovery ───────────────────────────────────────────────

    def _discover_handlers(self) -> None:
        """Scan methods for @intent / @on_event / @scheduled decorator metadata."""
        for attr_name in dir(self.__class__):
            method = getattr(self, attr_name, None)
            if not callable(method):
                continue
            if hasattr(method, "_intent_pattern"):
                pattern = method._intent_pattern
                order = getattr(method, "_intent_order", 50)
                iname = getattr(method, "_intent_name", "")
                idesc = getattr(method, "_intent_description", "")
                self._intent_handlers.append((pattern, order, method, iname, idesc))
            if hasattr(method, "_event_type"):
                self._event_handlers[method._event_type] = method

        # Sort intent handlers by order ASC → pattern length DESC
        self._intent_handlers.sort(key=lambda e: (e[1], -len(e[0])))
        self._validate_intents()

    def _validate_intents(self) -> None:
        """Compile-check all intent regex patterns at init time."""
        for pattern, _order, _method, _name, _desc in self._intent_handlers:
            try:
                re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"Invalid intent regex '{pattern}' in {self.name}: {exc}"
                ) from exc

    # ── i18n (autonomous — no core.i18n dependency) ─────────────────────

    def _register_locales(self) -> None:
        """Load locale files from ``locales/`` next to the module or from MODULE_DIR."""
        import inspect
        dirs_to_check = []
        if MODULE_DIR:
            dirs_to_check.append(Path(MODULE_DIR) / "locales")
        try:
            module_file = inspect.getfile(self.__class__)
            dirs_to_check.append(Path(module_file).parent / "locales")
        except (TypeError, OSError):
            pass

        for locales_dir in dirs_to_check:
            if not locales_dir.is_dir():
                continue
            for f in locales_dir.iterdir():
                if f.suffix != ".json":
                    continue
                lang = f.stem  # e.g. "uk", "en"
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    self._locale_strings.setdefault(lang, {}).update(data)
                    self._log.debug("Loaded locale %s from %s", lang, f)
                except Exception as exc:
                    self._log.warning("Locale load error %s: %s", f, exc)
            break  # first found directory wins

    def t(self, key: str, lang: str | None = None, **kwargs: Any) -> str:
        """Translate a module-scoped key using autonomous locale files.

        Falls back to English, then returns key itself if not found.
        """
        lang = lang or "en"
        template = (
            self._locale_strings.get(lang, {}).get(key)
            or self._locale_strings.get("en", {}).get(key)
        )
        if template is None:
            return key
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template

    # ── Lifecycle ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Entry point — call from ``asyncio.run(module.start())``."""
        self._log.info("Module %s v%s starting", self.name, self.version)
        await self.on_start()

        # Start scheduled tasks
        for attr_name in dir(self.__class__):
            method = getattr(self, attr_name, None)
            if callable(method) and hasattr(method, "_schedule"):
                task = asyncio.create_task(self._run_scheduled(method, method._schedule))
                self._tasks.append(task)

        # Main bus loop (reconnects until shutdown)
        await self._bus_loop()

    async def on_start(self) -> None:
        """Override: called once before bus connection."""

    async def on_stop(self) -> None:
        """Override: called once during graceful stop (resource cleanup)."""

    async def on_shutdown(self) -> None:
        """Override: lightweight hook called when core sends shutdown notification.

        NOT for resource cleanup (use on_stop).  Use for last-moment state save.
        """

    async def _safe_on_stop(self) -> None:
        """Ensure on_stop() is called exactly once."""
        if self._stopped:
            return
        self._stopped = True
        try:
            await self.on_stop()
        except Exception as exc:
            self._log.error("on_stop() error: %s", exc)

        # Cancel scheduled tasks
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._log.info("Module %s stopped", self.name)

    # ── Bus connection loop ─────────────────────────────────────────────

    async def _bus_loop(self) -> None:
        """Connect → announce → message loop → reconnect with backoff."""
        attempt = 0

        while not self._shutdown_event.is_set():
            try:
                await self._connect_and_run()
                # Clean disconnect (shutdown or fatal) — exit loop
                if self._shutdown_event.is_set():
                    break
            except Exception as exc:
                self._log.warning("Bus connection error: %s", exc)

            self._connected.clear()
            self._ws = None
            attempt += 1

            # Exponential backoff with jitter
            delay = min(_RECONNECT_BASE * (2 ** (attempt - 1)), _RECONNECT_MAX)
            jitter = random.uniform(0, delay * 0.3)
            total_delay = delay + jitter
            self._log.info("Reconnecting in %.1fs (attempt %d)", total_delay, attempt)

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(), timeout=total_delay,
                )
                break  # shutdown during backoff
            except asyncio.TimeoutError:
                pass

        await self._safe_on_stop()

    async def _connect_and_run(self) -> None:
        """Single connection attempt: connect → announce → ack → message loop."""
        import websockets

        url = f"{self._bus_url}?token={self._token}"
        async with websockets.connect(url) as ws:
            self._ws = ws

            # 1. Send announce
            capabilities = self._build_capabilities()
            await ws.send(json.dumps({
                "type": "announce",
                "module": self.name,
                "capabilities": capabilities,
            }))

            # 2. Wait for ack
            try:
                raw_ack = await asyncio.wait_for(ws.recv(), timeout=10.0)
            except asyncio.TimeoutError:
                self._log.error("Announce ack timeout")
                return

            ack = json.loads(raw_ack)
            if ack.get("type") != "announce_ack" or ack.get("status") != "ok":
                reason = ack.get("reason", "unknown")
                self._log.error("Announce rejected: %s", reason)
                if reason in _FATAL_REASONS:
                    self._shutdown_event.set()
                return

            self._bus_id = ack.get("bus_id")
            warnings = ack.get("warnings", [])
            if warnings:
                self._log.warning("Bus warnings: %s", warnings)

            self._log.info(
                "Connected to bus (bus_id=%s)",
                self._bus_id[:8] if self._bus_id else "?",
            )
            self._connected.set()

            # 3. Start flush task
            flush_task = asyncio.create_task(self._flush_outbox())

            # 4. Message loop
            try:
                async for raw_msg in ws:
                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue
                    await self._dispatch_bus_message(msg)
            except websockets.ConnectionClosed as exc:
                reason = exc.reason or ""
                self._log.info("Bus connection closed: %s (code=%s)", reason, exc.code)
                if reason in _FATAL_REASONS:
                    self._shutdown_event.set()
            finally:
                self._connected.clear()
                flush_task.cancel()
                try:
                    await flush_task
                except (asyncio.CancelledError, Exception):
                    pass
                # Cancel pending API requests
                for req_id, future in self._pending.items():
                    if not future.done():
                        future.set_exception(ConnectionError("Bus disconnected"))
                self._pending.clear()

    # ── Capabilities ────────────────────────────────────────────────────

    def _build_capabilities(self) -> dict[str, Any]:
        """Build capabilities dict for announce/re_announce.

        Manifest intents have priority over decorator-discovered intents for
        the bus index (patterns + priority sent to core).  Decorators are still
        needed for SDK-side dispatch.
        """
        caps: dict[str, Any] = {}

        # Try loading manifest.json
        manifest = self._load_manifest()

        # Intents — manifest takes priority
        if manifest and "intents" in manifest:
            caps["intents"] = manifest["intents"]
        elif self._intent_handlers:
            # Build from decorators
            intents_list = []
            for p, order, _method, iname, idesc in self._intent_handlers:
                entry: dict[str, Any] = {
                    "patterns": {"en": [p], "uk": [p]},
                    "priority": order,
                }
                if iname:
                    entry["name"] = iname
                if idesc:
                    entry["description"] = idesc
                intents_list.append(entry)
            caps["intents"] = intents_list

        # Subscriptions from event handlers
        subs = list(self._event_handlers.keys())
        if subs:
            caps["subscriptions"] = subs

        # Publishes from manifest
        if manifest and "publishes" in manifest:
            caps["publishes"] = manifest["publishes"]

        return caps

    def _load_manifest(self) -> dict[str, Any] | None:
        """Load manifest.json from MODULE_DIR or next to module file."""
        import inspect
        paths = []
        if MODULE_DIR:
            paths.append(Path(MODULE_DIR) / "manifest.json")
        try:
            module_file = inspect.getfile(self.__class__)
            paths.append(Path(module_file).parent / "manifest.json")
        except (TypeError, OSError):
            pass

        for p in paths:
            if p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return None

    async def update_capabilities(self) -> None:
        """Send re_announce to hot-reload capabilities without reconnect."""
        if not self._connected.is_set() or self._ws is None:
            self._log.warning("Cannot re_announce: not connected")
            return
        capabilities = self._build_capabilities()
        await self._ws.send(json.dumps({
            "type": "re_announce",
            "capabilities": capabilities,
        }))

    # ── Message dispatch ────────────────────────────────────────────────

    async def _dispatch_bus_message(self, msg: dict[str, Any]) -> None:
        """Route incoming bus message to appropriate handler."""
        msg_type = msg.get("type", "")

        if msg_type == "ping":
            await self._handle_ping(msg)
        elif msg_type == "intent":
            await self._handle_intent(msg)
        elif msg_type == "event":
            await self._handle_event(msg)
        elif msg_type == "shutdown":
            await self._handle_shutdown(msg)
        elif msg_type == "api_request":
            await self._handle_incoming_api_request(msg)
        elif msg_type == "api_response":
            self._handle_api_response(msg)
        elif msg_type == "announce_ack":
            # re_announce ack
            warnings = msg.get("warnings", [])
            if warnings:
                self._log.warning("Re-announce warnings: %s", warnings)

    async def _handle_ping(self, msg: dict[str, Any]) -> None:
        if self._ws:
            await self._ws.send(json.dumps({"type": "pong", "ts": msg.get("ts")}))

    async def _handle_intent(self, msg: dict[str, Any]) -> None:
        """Dispatch intent to matching @intent handler, send response."""
        req_id = msg.get("id", "")
        payload = msg.get("payload", {})
        text = payload.get("text", "")
        lang = payload.get("lang", "en")
        context = payload.get("context", {})
        context["_lang"] = lang

        result = await self._dispatch_intent(text, context)
        response: dict[str, Any] = {
            "type": "intent_response",
            "id": req_id,
            "payload": result if result else {"handled": False},
        }
        if self._ws:
            await self._ws.send(json.dumps(response))

    async def _dispatch_intent(
        self, text: str, context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Match text against @intent handlers (local regex match)."""
        text_lower = text.lower().strip()
        for pattern, _order, handler, _name, _desc in self._intent_handlers:
            if re.search(pattern, text_lower, re.IGNORECASE):
                try:
                    result = await handler(text, context)
                    if result is None:
                        return {"handled": False}
                    return {"handled": True, **result}
                except Exception as exc:
                    self._log.error("Intent handler error: %s", exc)
                    return {"handled": False, "error": str(exc)}
        return {"handled": False}

    async def _handle_event(self, msg: dict[str, Any]) -> None:
        """Dispatch event to matching @on_event handlers (supports wildcards)."""
        payload = msg.get("payload", {})
        event_type = payload.get("event_type", "")
        data = payload.get("data", {})

        for subscribed_type, handler in self._event_handlers.items():
            if _matches_subscription(event_type, subscribed_type):
                try:
                    await handler(data)
                except Exception as exc:
                    self._log.error("Event handler error for %s: %s", event_type, exc)

    async def _handle_shutdown(self, msg: dict[str, Any]) -> None:
        """Core is shutting down — run on_shutdown(), then exit."""
        self._drain_ms = msg.get("drain_ms", 0)
        self._log.info("Core shutdown notification (drain=%dms)", self._drain_ms)
        try:
            await self.on_shutdown()
        except Exception as exc:
            self._log.error("on_shutdown() error: %s", exc)
        self._shutdown_event.set()

    async def _handle_incoming_api_request(self, msg: dict[str, Any]) -> None:
        """Handle an API request from core (UI proxy → module)."""
        req_id = msg.get("id", "")
        method = msg.get("method", "GET")
        path = msg.get("path", "")
        body = msg.get("body")

        try:
            result = await self.handle_api_request(method, path, body)
            response = {
                "type": "api_response",
                "id": req_id,
                "payload": result,
            }
        except Exception as exc:
            response = {
                "type": "api_response",
                "id": req_id,
                "payload": {"error": str(exc)},
            }

        if self._ws:
            await self._ws.send(json.dumps(response))

    async def handle_api_request(
        self, method: str, path: str, body: Any,
    ) -> dict[str, Any]:
        """Override: handle incoming API requests from core (UI proxy).

        Default implementation returns 404.
        """
        return {"error": f"Not implemented: {method} {path}"}

    def _handle_api_response(self, msg: dict[str, Any]) -> None:
        """Resolve a pending api_request future."""
        req_id = msg.get("id", "")
        future = self._pending.pop(req_id, None)
        if future and not future.done():
            future.set_result(msg)

    # ── Outgoing: events ────────────────────────────────────────────────

    async def publish_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        """Publish event via bus.  Buffers in outbox while disconnected."""
        msg = json.dumps({
            "type": "event",
            "payload": {"event_type": event_type, "data": payload},
        })
        if self._connected.is_set() and self._ws:
            try:
                await self._ws.send(msg)
                return True
            except Exception:
                pass
        # Buffer in outbox
        try:
            self._outbox.put_nowait(msg)
        except asyncio.QueueFull:
            self._log.warning("Event outbox full — dropping event %s", event_type)
            return False
        return True

    async def _flush_outbox(self) -> None:
        """Persistent flush loop — blocking get, not busy-wait."""
        try:
            while True:
                msg = await self._outbox.get()
                if self._ws:
                    try:
                        await self._ws.send(msg)
                    except Exception:
                        # Put back if send failed
                        try:
                            self._outbox.put_nowait(msg)
                        except asyncio.QueueFull:
                            pass
                        await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    # ── Outgoing: API proxy ─────────────────────────────────────────────

    async def api_request(
        self,
        method: str,
        path: str,
        body: Any = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        """Send an API request via bus and wait for response.

        Raises ``TimeoutError`` or ``ConnectionError`` on failure.
        """
        if not self._connected.is_set() or self._ws is None:
            raise ConnectionError("Not connected to bus")

        req_id = str(_uuid.uuid4())
        future: asyncio.Future[dict] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        msg = json.dumps({
            "type": "api_request",
            "id": req_id,
            "method": method.upper(),
            "path": path,
            "body": body,
        })
        await self._ws.send(msg)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"API request {method} {path} timed out") from None

        status = result.get("status", 0)
        resp_body = result.get("body", {})
        if status >= 400:
            raise ConnectionError(
                f"API {method} {path} returned {status}: {resp_body}"
            )
        return resp_body

    async def get_device(self, device_id: str) -> dict[str, Any] | None:
        """Fetch a device from the SelenaCore registry via bus."""
        try:
            return await self.api_request("GET", f"/devices/{device_id}")
        except Exception:
            return None

    # ── Scheduled tasks ─────────────────────────────────────────────────

    @staticmethod
    async def _run_scheduled(method: Callable, schedule: str) -> None:
        """Simple interval scheduler from ``'every:Ns/Nm/Nh'`` notation."""
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


# ── Helpers ─────────────────────────────────────────────────────────────────


def _matches_subscription(event_type: str, pattern: str) -> bool:
    """Wildcard matching: ``'device.*'`` matches ``'device.state_changed'``."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2]
        return event_type == prefix or event_type.startswith(prefix + ".")
    return event_type == pattern
