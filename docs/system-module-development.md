# System Module Development Guide

This guide covers everything you need to build, register, and maintain a **system module** for SelenaCore. System modules run inside the core process and have direct access to the EventBus, database, and FastAPI application -- no containers, no network overhead.

---

## Table of Contents

1. [What Are System Modules](#what-are-system-modules)
2. [Architecture Overview](#architecture-overview)
3. [Module Structure](#module-structure)
4. [Base Class Reference](#base-class-reference)
5. [EventBus Integration](#eventbus-integration)
6. [Device Registry Access](#device-registry-access)
7. [Adding a REST API](#adding-a-rest-api)
8. [IntentRouter Integration](#intentrouter-integration)
9. [Loading Process](#loading-process)
10. [Complete Example](#complete-example)
11. [System vs User Modules](#system-vs-user-modules)
12. [Built-in System Modules](#built-in-system-modules)
13. [Best Practices](#best-practices)
14. [Troubleshooting](#troubleshooting)

---

## What Are System Modules

System modules are Python packages that run **inside** the SelenaCore process. They are loaded via `importlib` at startup and communicate with the rest of the system through direct Python calls -- no Docker containers, no WebSocket serialization, no network hops.

Key characteristics:

- **In-process execution** via Python `importlib`
- **~0 MB additional RAM** -- no container overhead
- **Direct EventBus access** through async callbacks
- **Direct database access** through a shared SQLAlchemy async session factory
- **Optional FastAPI router** mounted at `/api/ui/modules/{name}/`
- Located in the `system_modules/` directory
- Currently **21 built-in** system modules ship with SelenaCore

Use a system module when you need tight integration with the core, low latency, or direct database access. Use a [user module](user-module-development.md) when you need isolation, independent deployment, or third-party extensibility.

---

## Architecture Overview

```
SelenaCore Process
 |
 +-- PluginManager
 |     +-- scan_local_modules()      # discovers system_modules/*
 |     +-- validate manifest.json
 |     +-- importlib.import_module()
 |
 +-- EventBus (in-process)
 |     +-- DirectSubscription        # async callback, no serialization
 |
 +-- SQLAlchemy async session
 |     +-- async_sessionmaker         # injected via setup()
 |
 +-- FastAPI app
       +-- /api/ui/modules/{name}/   # optional per-module router
```

Every system module receives two core dependencies through `setup()`:

1. **EventBus** -- publish and subscribe to events with async callbacks.
2. **async_sessionmaker** -- create database sessions for direct SQL queries.

These are injected automatically by the loader before `start()` is called.

---

## Module Structure

Every system module lives in its own package under `system_modules/`:

```
system_modules/my_module/
    __init__.py      # Must export: module_class = MyModule
    module.py        # SystemModule subclass with start/stop logic
    manifest.json    # Module metadata; type must be "SYSTEM"
```

### `__init__.py`

The `__init__.py` file must export a single name: `module_class`. This is the class the loader will instantiate.

```python
from .module import MyModule as module_class
```

### `manifest.json`

```json
{
    "name": "my-module",
    "version": "1.0.0",
    "type": "SYSTEM",
    "runtime_mode": "always_on",
    "group": "system",
    "intents": ["mymodule.do_action"],
    "entities": ["mydevice"],
    "permissions": []
}
```

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Unique identifier. Must match `SystemModule.name` in your class. Use lowercase kebab-case. |
| `version` | Yes | Semantic version string. |
| `type` | Yes | Must be `"SYSTEM"` for system modules. |
| `runtime_mode` | Yes | `"always_on"` (started at boot) or `"on_demand"` (started when needed). |
| `group` | Yes | Functional category: `media`, `automation`, `voice`, `security`, `energy`, `weather`, `presence`, `notification`, `network`, `backup`, `system`. |
| `intents` | Yes | List of intent names the module handles (e.g., `["media.play", "media.stop"]`). Used by ModuleRegistry for routing. |
| `entities` | Yes | List of entity types the module works with (e.g., `["radio", "music"]`). Used for device disambiguation. |
| `permissions` | No | List of permission strings the module requires (e.g., `["devices.read", "devices.write"]`). |

System modules do **not** specify a `port` field. They share the core process and, if needed, mount a FastAPI router instead.

### ModuleRegistry

When a module is loaded, its `group`, `intents`, and `entities` from `manifest.json` are automatically registered in the **ModuleRegistry** (`core/module_registry.py`). This enables:

- **Intent routing**: `get_module_for_intent("media.play")` returns `"media-player"`
- **Entity resolution**: `get_modules_for_entity("radio")` returns `["media-player"]`
- **Device disambiguation**: when an intent targets an entity type with multiple matching devices, the system asks the user to clarify

### `module.py`

Contains your `SystemModule` subclass. See the [base class reference](#base-class-reference) and the [complete example](#complete-example) below.

---

## Base Class Reference

All system modules inherit from `SystemModule`, defined in `core/module_loader/system_module.py`.

```python
from abc import ABC, abstractmethod
from typing import Any, Callable

class SystemModule(ABC):
    name: str  # Must match manifest.json "name"

    def setup(self, bus: EventBus, session_factory: async_sessionmaker) -> None:
        """Injected by the loader before start().
        Stores references to the EventBus and the database session factory.
        Do NOT override this unless you call super().setup(...) first."""

    @abstractmethod
    async def start(self) -> None:
        """Called after setup(). Initialize your service, subscribe to events,
        launch background tasks."""

    @abstractmethod
    async def stop(self) -> None:
        """Called during shutdown. Cancel background tasks, release resources,
        unsubscribe from the EventBus."""

    def get_router(self) -> APIRouter | None:
        """Return a FastAPI APIRouter to be mounted at
        /api/ui/modules/{name}/. Return None if no API is needed."""
        return None
```

### Lifecycle

```
__init__()  -->  setup(bus, session_factory)  -->  start()
                                                      |
                                               (module running)
                                                      |
                                                   stop()
```

1. The loader instantiates your class via `module_class()`.
2. `setup()` injects the EventBus and database session factory.
3. `start()` is called -- your module is now active.
4. On shutdown (or module reload), `stop()` is called.

---

## EventBus Integration

System modules interact with the EventBus through helper methods inherited from `SystemModule`. Because system modules run in-process, event delivery is a direct async callback -- no serialization, no network round-trip.

### Subscribing to Events

```python
async def start(self) -> None:
    self.subscribe(
        event_types=["device.state_changed", "device.online"],
        callback=self._on_device_event
    )
```

The `subscribe()` method returns a subscription ID and registers an async callback. The callback signature is:

```python
async def _on_device_event(self, event: Event) -> None:
    device_id = event.payload.get("device_id")
    new_state = event.payload.get("state")
    # Process the event...
```

You can subscribe to multiple event types in a single call, or make separate `subscribe()` calls for different handlers.

### Publishing Events

```python
await self.publish("module.started", {"name": self.name})
await self.publish("device.command", {
    "device_id": "light-001",
    "command": "turn_on",
    "params": {"brightness": 80}
})
```

The first argument is the event type string. The second is the payload dictionary.

### Unsubscribing

Always clean up subscriptions when the module stops:

```python
async def stop(self) -> None:
    self._cleanup_subscriptions()
```

The `_cleanup_subscriptions()` helper removes all subscriptions registered by this module instance.

### Common Event Types

| Event Type | Payload | Description |
|---|---|---|
| `device.state_changed` | `{device_id, state, previous_state}` | A device changed state |
| `device.online` | `{device_id}` | Device came online |
| `device.offline` | `{device_id}` | Device went offline |
| `device.protocol_heartbeat` | `{device_id, protocol, timestamp}` | Heartbeat from a protocol bridge |
| `device.command` | `{device_id, command, params}` | Command issued to a device |
| `module.started` | `{name}` | A module finished starting |
| `module.stopped` | `{name}` | A module stopped |
| `automation.triggered` | `{rule_id, trigger}` | An automation rule fired |

---

## Device Registry Access

System modules have direct database access through helper methods. These wrap SQLAlchemy queries behind a clean async interface.

### Fetch All Devices

```python
devices = await self.fetch_devices()  # Returns list[dict]
for device in devices:
    print(device["id"], device["name"], device["type"])
```

### Get Device State

```python
state = await self.get_device_state(device_id)
# Returns dict, e.g. {"power": True, "brightness": 80, "color_temp": 4000}
```

### Update Device State

```python
await self.patch_device_state(device_id, {"power": True, "brightness": 80})
```

This merges the provided fields into the existing state. Fields not included are left unchanged.

### Register a New Device

```python
device_id = await self.register_device(
    name="Kitchen Light",
    type="actuator",          # sensor | actuator | controller | virtual
    protocol="zigbee",
    capabilities=["turn_on", "turn_off", "set_brightness"],
    meta={"manufacturer": "IKEA", "model": "TRADFRI"}
)
```

**Device types:**

| Type | Description |
|---|---|
| `sensor` | Reports measurements (temperature, humidity, motion) |
| `actuator` | Performs actions (lights, switches, locks) |
| `controller` | Sends commands (remotes, buttons, wall switches) |
| `virtual` | Software-defined device (timers, computed values) |

---

## Adding a REST API

Override `get_router()` to expose HTTP endpoints. The returned router is mounted at `/api/ui/modules/{name}/`, so a route defined as `/health` becomes `/api/ui/modules/my-module/health`.

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

class BrightnessRequest(BaseModel):
    device_id: str
    brightness: int

class MyModule(SystemModule):
    name = "my-module"

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/health")
        async def health():
            return {"status": "ok", "name": self.name}

        @router.get("/devices")
        async def list_devices():
            devices = await self.fetch_devices()
            return {"devices": devices, "count": len(devices)}

        @router.post("/brightness")
        async def set_brightness(req: BrightnessRequest):
            if not 0 <= req.brightness <= 100:
                raise HTTPException(400, "Brightness must be 0-100")
            await self.patch_device_state(
                req.device_id, {"brightness": req.brightness}
            )
            return {"ok": True}

        return router
```

Tips for REST APIs:

- Use Pydantic models for request validation.
- Raise `HTTPException` for error responses.
- Keep route paths short -- the module name is already in the URL prefix.
- Return JSON-serializable dicts or Pydantic models.

---

## IntentRouter Integration

System modules can register intents for **Tier 1.5** (in-process intent handling). Intents are defined in YAML and compiled by **IntentCompiler** — no hand-written regex files needed.

The intent router cascade: FastMatcher → IntentCompiler (regex) → SmartMatcher (TF-IDF) → Module Bus → LLM → Fallback.

### Step 1: Define intents in YAML

Add your intents to `config/intents/definitions.yaml`:

```yaml
intents:
  mymodule.do_something:
    module: my-module
    noun_class: DEVICE           # semantic category (MEDIA, DEVICE, WEATHER, ENERGY, PRESENCE, AUTOMATION, WATCHDOG)
    verb: on                     # verb category (on, off, play, stop, pause, set, query, search, scan, list)
    priority: 5
    description: "Perform an action"
    templates:                   # expanded using vocabulary YAML
      - "{verb.on} {param.what}"
    params:
      what: {type: freetext}
    overrides:                   # raw regex (when templates are insufficient)
      uk:
        - "зроби\\s+(?P<what>.+)"
      en:
        - "do\\s+(?P<what>.+)"

  mymodule.status:
    module: my-module
    noun_class: DEVICE
    verb: query
    priority: 5
    description: "Get module status"
    overrides:
      uk: ["статус\\s+модуля"]
      en: ["module\\s+status"]
```

Add new vocabulary words to `config/intents/vocab/{en,uk}.yaml` if needed.

### Step 2: Create voice handler

```python
# system_modules/my_module/voice_handler.py
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import MyModule

logger = logging.getLogger(__name__)

class MyVoiceHandler:
    def __init__(self, module: "MyModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        m = self._module
        match intent:
            case "mymodule.do_something":
                what = params.get("what", "")
                await m.speak(f"Done: {what}")
            case "mymodule.status":
                await m.speak("Module is running fine")
```

### Step 3: Register in module.py

```python
async def start(self) -> None:
    # ... existing setup ...

    self._voice = MyVoiceHandler(self)
    self.subscribe(["voice.intent"], self._on_event)

    try:
        from system_modules.llm_engine.intent_router import get_intent_router
        from system_modules.llm_engine.intent_compiler import get_intent_compiler
        entries = get_intent_compiler().get_intents_for_module("my-module")
        for entry in entries:
            get_intent_router().register_system_intent(entry)
    except Exception as exc:
        logger.warning("Failed to register intents: %s", exc)

async def stop(self) -> None:
    try:
        from system_modules.llm_engine.intent_router import get_intent_router
        get_intent_router().unregister_system_intents(self.name)
    except Exception:
        pass

async def _on_event(self, event) -> None:
    if event.type == "voice.intent":
        intent = event.payload.get("intent", "")
        if intent.startswith("mymodule."):
            await self._voice.handle(intent, event.payload.get("params", {}))

async def speak(self, text: str) -> None:
    """Send text to TTS via EventBus → voice-core."""
    await self.publish("voice.speak", {"text": text})
```

### Priority guide

Higher numbers = checked first within Tier 1.5.

| Priority | Use Case |
|----------|----------|
| 10 | Intents with parameters (named regex groups) — must match before generic patterns |
| 5 | Standard intents — simple commands without parameters |

### Existing voice-enabled modules

All intent definitions are in `config/intents/definitions.yaml`:

| Module | Intents | noun_class |
|--------|---------|------------|
| media-player | 14 | MEDIA |
| automation-engine | 4 | AUTOMATION |
| weather-service | 3 | WEATHER |
| presence-detection | 3 | PRESENCE |
| energy-monitor | 2 | ENERGY |
| device-watchdog | 2 | WATCHDOG |

---

## Loading Process

Understanding the loading sequence helps with debugging and knowing when your code runs:

1. **Discovery** -- `PluginManager.scan_local_modules()` walks `system_modules/` and finds directories containing `manifest.json`.
2. **Validation** -- The manifest is parsed and validated. `type` must be `"SYSTEM"`.
3. **Import** -- `importlib.import_module(f"system_modules.{name}")` loads the package.
4. **Class retrieval** -- The loader reads `module_class` from the package's `__init__.py`.
5. **Instantiation** -- `instance = module_class()`.
6. **Injection** -- `instance.setup(bus, session_factory)` provides EventBus and database access.
7. **Start** -- For `"always_on"` modules, `instance.start()` is called immediately.
8. **Router mount** -- If `get_router()` returns a non-None router, it is mounted at `/api/ui/modules/{name}/`.

If any step fails, the error is logged and the module is skipped -- other modules continue loading normally.

---

## Complete Example

Below is a full, working system module that monitors device battery levels and sends notifications when batteries are low.

### `system_modules/battery_monitor/__init__.py`

```python
from .module import BatteryMonitorModule as module_class
```

### `system_modules/battery_monitor/manifest.json`

```json
{
    "name": "battery-monitor",
    "version": "1.0.0",
    "type": "SYSTEM",
    "runtime_mode": "always_on",
    "permissions": ["devices.read"]
}
```

### `system_modules/battery_monitor/module.py`

```python
import asyncio
import logging
from fastapi import APIRouter
from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)

LOW_BATTERY_THRESHOLD = 20  # percent
CHECK_INTERVAL = 3600       # seconds (1 hour)


class BatteryMonitorModule(SystemModule):
    name = "battery-monitor"

    def __init__(self) -> None:
        super().__init__()
        self._check_task: asyncio.Task | None = None
        self._low_battery_devices: dict[str, int] = {}

    async def start(self) -> None:
        # Subscribe to state changes so we catch battery updates in real time
        self.subscribe(
            event_types=["device.state_changed"],
            callback=self._on_state_changed,
        )

        # Also run a periodic full scan
        self._check_task = asyncio.create_task(self._periodic_check())

        await self.publish("module.started", {"name": self.name})
        logger.info("Battery monitor started (threshold=%d%%)", LOW_BATTERY_THRESHOLD)

    async def stop(self) -> None:
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass

        self._cleanup_subscriptions()
        logger.info("Battery monitor stopped")

    # ---- Event handler ----

    async def _on_state_changed(self, event) -> None:
        payload = event.payload
        device_id = payload.get("device_id")
        state = payload.get("state", {})
        battery = state.get("battery_level")

        if battery is None:
            return

        if battery < LOW_BATTERY_THRESHOLD:
            if device_id not in self._low_battery_devices:
                self._low_battery_devices[device_id] = battery
                await self.publish("notification.send", {
                    "title": "Low Battery",
                    "body": f"Device {device_id} battery is at {battery}%",
                    "priority": "warning",
                })
                logger.warning("Low battery: %s at %d%%", device_id, battery)
        else:
            self._low_battery_devices.pop(device_id, None)

    # ---- Background task ----

    async def _periodic_check(self) -> None:
        while True:
            try:
                devices = await self.fetch_devices()
                for device in devices:
                    state = await self.get_device_state(device["id"])
                    battery = state.get("battery_level")
                    if battery is not None and battery < LOW_BATTERY_THRESHOLD:
                        self._low_battery_devices[device["id"]] = battery
            except Exception:
                logger.exception("Error during periodic battery check")

            await asyncio.sleep(CHECK_INTERVAL)

    # ---- REST API ----

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/health")
        async def health():
            return {"status": "ok", "name": self.name}

        @router.get("/low-battery")
        async def low_battery():
            return {
                "threshold": LOW_BATTERY_THRESHOLD,
                "devices": self._low_battery_devices,
                "count": len(self._low_battery_devices),
            }

        return router
```

Once placed in `system_modules/battery_monitor/`, SelenaCore picks it up on the next restart. The API becomes available at:

- `GET /api/ui/modules/battery-monitor/health`
- `GET /api/ui/modules/battery-monitor/low-battery`

---

## System vs User Modules

| Feature | System Module | User Module |
|---|---|---|
| **Execution** | In-process (`importlib`) | Docker container |
| **Communication** | Direct Python calls | WebSocket Module Bus |
| **Base class** | `SystemModule` | `SmartHomeModule` |
| **EventBus** | DirectSubscription (async callback) | Module Bus delivery (serialized) |
| **Database** | Direct SQLAlchemy session | Via API proxy |
| **REST API** | Optional `get_router()` | `handle_api_request()` |
| **RAM overhead** | ~0 MB | Container overhead |
| **Port** | None needed | None needed (bus) |
| **Isolation** | Shares core process | Fully isolated |
| **Crash impact** | Can affect the core | Contained in container |
| **Hot reload** | Requires core restart | Independent restart |

**Choose a system module when:**

- You need sub-millisecond event handling.
- You need direct database queries.
- The module is tightly coupled to core functionality.
- RAM is constrained (e.g., Raspberry Pi with limited memory).

**Choose a user module when:**

- You want fault isolation -- a crash should not bring down the core.
- The module is community-contributed or third-party.
- You need independent versioning and deployment.
- The module has heavy dependencies that should not bloat the core.

---

## Built-in System Modules

SelenaCore ships with 22 system modules:

| Module | Description |
|---|---|
| `voice_core` | STT (Vosk), TTS (Piper), wake word detection |
| `llm_engine` | Ollama LLM client, intent router, fast matcher |
| `ui_core` | Web dashboard UI server (:80) |
| `user_manager` | User profiles, authentication, biometrics |
| `automation_engine` | YAML rule engine for automations |
| `scheduler` | Cron, interval, and sun-based task scheduling |
| `device_watchdog` | Device health monitoring, offline detection |
| `protocol_bridge` | MQTT and Home Assistant protocol bridges |
| `notification_router` | Multi-channel notifications (push, email) |
| `media_player` | Audio playback with VLC |
| `presence_detection` | WiFi/BLE occupancy tracking |
| `hw_monitor` | CPU, RAM, disk, and temperature monitoring |
| `backup_manager` | Local and cloud backups |
| `remote_access` | Tailscale VPN integration |
| `network_scanner` | Network device discovery (ARP, mDNS, SSDP) |
| `import_adapters` | Home Assistant, Tuya, Hue device import |
| `energy_monitor` | Power consumption tracking |
| `update_manager` | Core and module updates |
| `notify_push` | Web Push VAPID notifications |
| `secrets_vault` | AES-256-GCM encrypted token storage |
| `weather_service` | Weather API integration |

Browse `system_modules/` for the full set.

---

## Best Practices

### Startup

- Keep `start()` fast. If you need to do heavy initialization, spawn a background `asyncio.Task` and return immediately.
- Always publish `module.started` at the end of `start()` so other modules can depend on it.

### Shutdown

- **Always** call `self._cleanup_subscriptions()` in `stop()`. Leaked subscriptions cause memory leaks and phantom event handling.
- Cancel all background `asyncio.Task` instances and await them with a `CancelledError` handler.
- Release any file handles, sockets, or external connections.

### Error Handling

- Wrap background loops in `try/except` to prevent a single failure from killing the task.
- Log exceptions with `logger.exception()` to capture full tracebacks.
- Never let an exception escape `start()` or `stop()` -- catch and log instead.

### Logging

- Use `logging.getLogger(__name__)` for module-specific loggers.
- Log at `INFO` for lifecycle events (started, stopped).
- Log at `WARNING` for recoverable issues.
- Log at `ERROR` for failures that need attention.

### EventBus

- Subscribe to the most specific event types possible. Subscribing to broad patterns increases processing overhead.
- Keep event handlers fast. If processing takes more than a few milliseconds, offload to a background task.
- Use meaningful event type names following the `domain.action` convention (e.g., `device.state_changed`, `automation.triggered`).

### Database

- Use the injected `session_factory` for all database operations. Do not create your own engine.
- Prefer the helper methods (`fetch_devices`, `get_device_state`, `patch_device_state`, `register_device`) over raw SQL when possible.
- Keep transactions short to avoid locking issues.

### REST API

- All routes are automatically prefixed with `/api/ui/modules/{name}/` -- do not repeat the module name in your route paths.
- Use Pydantic models for request and response validation.
- Return consistent JSON shapes across endpoints.

### Naming

- Module directory name: `snake_case` (e.g., `battery_monitor`).
- Module `name` field in manifest and class: `kebab-case` (e.g., `battery-monitor`).
- Keep these consistent -- the loader maps between them automatically.

---

## Troubleshooting

### Module not loading

- Check that `manifest.json` exists and `type` is `"SYSTEM"`.
- Check that `__init__.py` exports `module_class`.
- Check the core logs for import errors -- a syntax error in `module.py` will prevent loading.

### Events not arriving

- Confirm you are subscribing to the correct event type string (exact match, case-sensitive).
- Confirm `subscribe()` is called in `start()`, not in `__init__()` (the bus is not available until after `setup()`).
- Check that the publishing module is actually emitting the event.

### API routes returning 404

- Verify `get_router()` returns a non-None `APIRouter`.
- Verify the URL includes the full prefix: `/api/ui/modules/{name}/your-route`.
- Check that the module loaded successfully (look for `module.started` event in logs).

### Database errors

- Ensure you are using `await` with all database helper methods -- they are async.
- If you need raw session access, use `async with self._session_factory() as session:` and commit/rollback properly.

### Module crashes on startup

- Wrap heavy initialization in try/except blocks inside `start()`.
- If the module depends on another module, listen for its `module.started` event before proceeding rather than assuming it is already running.
