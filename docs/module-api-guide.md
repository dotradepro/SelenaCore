# SelenaCore Module Developer API Guide

Complete reference for building system and user modules that communicate with the SelenaCore smart home hub.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Module Types](#module-types)
- [SystemModule API](#systemmodule-api)
  - [Class Attributes](#systemmodule-class-attributes)
  - [Lifecycle Methods](#systemmodule-lifecycle-methods)
  - [EventBus Methods](#systemmodule-eventbus-methods)
  - [TTS Methods](#systemmodule-tts-methods)
  - [Device Registry Methods](#systemmodule-device-registry-methods)
  - [Router Helpers](#systemmodule-router-helpers)
  - [Internal Methods](#systemmodule-internal-methods)
- [SmartHomeModule API](#smarthomemodule-api)
  - [Class Attributes](#smarthomemodule-class-attributes)
  - [Decorators](#smarthomemodule-decorators)
  - [Lifecycle Hooks](#smarthomemodule-lifecycle-hooks)
  - [Event Methods](#smarthomemodule-event-methods)
  - [API Methods](#smarthomemodule-api-methods)
  - [i18n Methods](#smarthomemodule-i18n-methods)
  - [Capabilities](#smarthomemodule-capabilities)
- [EventBus Events Reference](#eventbus-events-reference)
- [WebSocket Module Bus Protocol](#websocket-module-bus-protocol)
  - [Connection](#bus-connection)
  - [Message Types](#bus-message-types)
  - [Capabilities Format](#bus-capabilities-format)
- [Intent System](#intent-system)
  - [Multi-tier Router](#multi-tier-router)
  - [Adding Voice Commands (System Module)](#adding-voice-commands-system-module)
  - [Adding Voice Commands (User Module)](#adding-voice-commands-user-module)
- [Widget and Settings HTML](#widget-and-settings-html)
  - [BASE URL](#widget-base-url)
  - [Theme CSS](#widget-theme-css)
  - [Localization](#widget-localization)
  - [PostMessage Events](#widget-postmessage-events)
- [manifest.json Reference](#manifestjson-reference)
- [Examples](#examples)
  - [System Module: Sensor Aggregator](#example-system-module-sensor-aggregator)
  - [User Module: Smart Plug Controller](#example-user-module-smart-plug-controller)
  - [Integration Module: External API Bridge](#example-integration-module-external-api-bridge)

---

## Architecture Overview

SelenaCore uses a **hub-and-spoke** architecture. The core process runs FastAPI on port 7070 and manages all modules, devices, and events. Modules are **fully isolated** from each other. No module may import from another module. All inter-module communication passes through the core EventBus.

```
                    SelenaCore Process (port 7070)
                    +-----------------------------+
                    |  EventBus   DeviceRegistry   |
                    |  IntentRouter  ModuleBus     |
                    +-----------------------------+
                   /        |            \
          importlib      importlib      WebSocket
             |              |              |
      [voice-core]   [llm-engine]   [user-module]
       (SYSTEM)        (SYSTEM)      (Docker container)
```

---

## Module Types

| Type | Execution | Port | Communication | Container |
|------|-----------|------|---------------|-----------|
| **SYSTEM** | importlib in core process | None | Direct Python calls via `SystemModule` methods | smarthome-core (shared) |
| **UI** | Docker sandbox | 8100-8200 | WebSocket Module Bus | smarthome-modules |
| **INTEGRATION** | Docker sandbox | 8100-8200 | WebSocket Module Bus | smarthome-modules |
| **DRIVER** | Docker sandbox | 8100-8200 | WebSocket Module Bus | smarthome-modules |
| **AUTOMATION** | Docker sandbox | 8100-8200 | WebSocket Module Bus | smarthome-modules |

SYSTEM modules have zero RAM overhead beyond their own objects. User modules run in isolated Docker containers and communicate exclusively over the WebSocket Module Bus.

---

## SystemModule API

**Source:** `core/module_loader/system_module.py`

System modules inherit from `SystemModule` and run inside the core process via importlib.

### SystemModule Class Attributes

| Attribute | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Must match the `"name"` field in `manifest.json`. Set as a class attribute. |

### SystemModule Lifecycle Methods

#### `setup(bus, session_factory) -> None`

Called by the module loader before `start()`. Injects core services. **Do not call this yourself.**

| Parameter | Type | Description |
|-----------|------|-------------|
| `bus` | `EventBus` | Core event bus instance |
| `session_factory` | `async_sessionmaker` | SQLAlchemy async session factory |

#### `async start() -> None` (abstract)

Initialize your service, subscribe to events, register intents. Called by the loader after `setup()`.

```python
async def start(self) -> None:
    self.subscribe(["device.state_changed"], self._on_device_event)
    self._task = asyncio.create_task(self._poll_loop())
```

#### `async stop() -> None` (abstract)

Cancel background tasks, release resources, unsubscribe from events.

```python
async def stop(self) -> None:
    self._task.cancel()
    self._cleanup_subscriptions()
```

#### `get_router() -> APIRouter | None`

Return a FastAPI `APIRouter` to expose REST endpoints. The router is mounted at `/api/ui/modules/{name}/`. Return `None` if no endpoints are needed.

```python
def get_router(self) -> APIRouter:
    router = APIRouter()
    @router.get("/status")
    async def get_status():
        return {"active": True, "readings": self._readings}
    self._register_html_routes(router, __file__)
    self._register_health_endpoint(router)
    return router
```

### SystemModule EventBus Methods

#### `subscribe(event_types, callback) -> str`

Subscribe to EventBus events with a direct async callback. Returns a subscription ID.

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_types` | `list[str]` | Event types to listen for (e.g. `["device.state_changed"]`) |
| `callback` | `Callable` | Async function with signature `async def handler(event) -> None` |

**Returns:** `str` -- subscription ID

```python
sub_id = self.subscribe(["voice.intent"], self._on_intent)

async def _on_intent(self, event) -> None:
    intent = event.payload.get("intent", "")
    if intent == "mymodule.action":
        await self.speak("Done")
```

#### `async publish(event_type, payload) -> None`

Publish an event to the EventBus. All subscribed modules (system and user) receive it.

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | Event type string (e.g. `"device.state_changed"`) |
| `payload` | `dict[str, Any]` | Event payload data |

```python
await self.publish("device.state_changed", {
    "device_id": "abc-123",
    "state": {"temperature": 22.5},
    "previous_state": {"temperature": 21.0},
})
```

### SystemModule TTS Methods

#### `async speak(text, *, timeout=30.0) -> None`

Publish a `voice.speak` event and **block** until TTS completes (`voice.speak_done`). This ensures speech finishes before subsequent actions (e.g., starting radio playback after an announcement).

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | `str` | -- | Text to speak via Piper TTS |
| `timeout` | `float` | `30.0` | Maximum wait time in seconds |

```python
await self.speak("Playing jazz radio")
# Speech is finished here; safe to start playback
await self._start_playback(station_url)
```

### SystemModule Device Registry Methods

#### `async fetch_devices() -> list[dict]`

Return all registered devices as plain dicts.

**Returns:** `list[dict]` with keys: `device_id`, `name`, `type`, `protocol`, `state`, `capabilities`, `last_seen`, `module_id`, `meta`

```python
devices = await self.fetch_devices()
sensors = [d for d in devices if d["type"] == "sensor"]
```

#### `async get_device_state(device_id) -> dict`

Return the state dict of a single device. Returns `{}` if not found.

| Parameter | Type | Description |
|-----------|------|-------------|
| `device_id` | `str` | UUID of the device |

```python
state = await self.get_device_state("abc-123")
temp = state.get("temperature", 0)
```

#### `async patch_device_state(device_id, state) -> None`

Update a device's state in the registry. Auto-commits the transaction.

| Parameter | Type | Description |
|-----------|------|-------------|
| `device_id` | `str` | UUID of the device |
| `state` | `dict[str, Any]` | New state key-value pairs (merged with existing) |

```python
await self.patch_device_state("abc-123", {"temperature": 23.0, "mode": "cool"})
```

#### `async register_device(name, type, protocol, capabilities, meta) -> str`

Register a new device in the registry. Returns the generated `device_id`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Human-readable device name |
| `type` | `str` | `"sensor"`, `"actuator"`, `"controller"`, or `"virtual"` |
| `protocol` | `str` | `"zigbee"`, `"mqtt"`, `"wifi"`, `"bluetooth"`, etc. |
| `capabilities` | `list[str]` | List of capability strings (e.g. `["read_temperature"]`) |
| `meta` | `dict[str, Any]` | Protocol-specific metadata |

**Returns:** `str` -- the new device UUID

```python
device_id = await self.register_device(
    name="Living Room Temp",
    type="sensor",
    protocol="zigbee",
    capabilities=["read_temperature", "read_humidity"],
    meta={"zigbee_addr": "0x5678"},
)
```

### SystemModule Router Helpers

#### `_register_html_routes(router, module_file) -> None`

Register `/widget` and `/settings` HTML endpoints on the router. Serves `widget.html` and `settings.html` from the module directory. Call at the end of `get_router()`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `router` | `APIRouter` | The router to register routes on |
| `module_file` | `str` | Pass `__file__` to locate HTML files relative to the module |

```python
def get_router(self) -> APIRouter:
    router = APIRouter()
    # ... your routes ...
    self._register_html_routes(router, __file__)
    return router
```

#### `_register_health_endpoint(router) -> None`

Register a minimal `GET /health` endpoint returning `{"status": "ok", "module": name}`. Use only for modules that need no extra health status fields.

| Parameter | Type | Description |
|-----------|------|-------------|
| `router` | `APIRouter` | The router to register the endpoint on |

```python
self._register_health_endpoint(router)
# GET /api/ui/modules/my-module/health → {"status": "ok", "module": "my-module"}
```

### SystemModule Internal Methods

#### `_cleanup_subscriptions() -> None`

Unsubscribe all direct EventBus subscriptions registered via `subscribe()`. **Always call this in `stop()`.**

```python
async def stop(self) -> None:
    self._task.cancel()
    self._cleanup_subscriptions()
```

#### `async _db_session() -> AsyncGenerator[AsyncSession, None]`

Async context manager yielding a raw SQLAlchemy `AsyncSession`. Use only when `fetch_devices()` / `patch_device_state()` are insufficient.

```python
async with self._db_session() as session:
    result = await session.execute(select(Device).where(Device.protocol == "zigbee"))
    devices = result.scalars().all()
```

---

## SmartHomeModule API

**Source:** `sdk/base_module.py` (re-exported from `sdk/smarthome_sdk/base.py`)

User modules inherit from `SmartHomeModule` and communicate with core over the WebSocket Module Bus.

### SmartHomeModule Class Attributes

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | `"unnamed_module"` | Module identifier, should match manifest.json |
| `version` | `str` | `"0.1.0"` | Semantic version string |

### SmartHomeModule Decorators

#### `@intent(pattern, order=50, name="", description="")`

Register a regex intent handler. When the core routes a voice/text command to this module, matching handlers are called in order.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pattern` | `str` | -- | Regex pattern (case-insensitive) |
| `order` | `int` | `50` | Priority (lower = higher). Ranges: 0-29 system, 30-49 core, 50-99 user |
| `name` | `str` | `""` | Intent name for LLM catalog (e.g. `"email.check_inbox"`) |
| `description` | `str` | `""` | Human-readable description for LLM context |

**Handler signature:** `async def handler(text: str, context: dict) -> dict | None`

**Return value:** `{"handled": True, "tts_text": "..."}` or `{"handled": True, "data": {...}}` or `None` (not handled)

```python
@intent(r"weather|forecast|pogoda", name="weather.current", description="Get current weather")
async def handle_weather(self, text: str, context: dict) -> dict:
    temp = await self._fetch_temperature()
    return {"handled": True, "tts_text": f"Currently {temp} degrees"}
```

#### `@on_event(event_type)`

Subscribe to an EventBus event type. Supports wildcards (`device.*` matches `device.state_changed`, `device.offline`, etc.).

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | Event type or wildcard pattern (e.g. `"device.*"`) |

**Handler signature:** `async def handler(data: dict) -> None`

```python
@on_event("device.state_changed")
async def on_device_change(self, data: dict) -> None:
    device_id = data.get("device_id")
    new_state = data.get("state", {})
    self._log.info("Device %s changed: %s", device_id, new_state)
```

#### `@scheduled(cron)`

Run a method on a recurring schedule. Supports simple interval notation and standard cron.

| Format | Example | Description |
|--------|---------|-------------|
| `every:Ns` | `every:30s` | Every 30 seconds |
| `every:Nm` | `every:5m` | Every 5 minutes |
| `every:Nh` | `every:1h` | Every 1 hour |
| cron | `*/5 * * * *` | Standard cron (requires apscheduler) |

```python
@scheduled("every:5m")
async def poll_sensor(self) -> None:
    reading = await self._read_sensor()
    await self.publish_event("device.state_changed", {
        "device_id": self._sensor_id,
        "state": {"value": reading},
    })
```

### SmartHomeModule Lifecycle Hooks

#### `async on_start() -> None`

Called once before the bus connection is established. Override for initialization.

```python
async def on_start(self) -> None:
    self._db = await self._init_database()
    self._log.info("Database initialized")
```

#### `async on_stop() -> None`

Called once during graceful stop. Use for resource cleanup (close connections, save state).

```python
async def on_stop(self) -> None:
    await self._db.close()
    self._log.info("Resources released")
```

#### `async on_shutdown() -> None`

Called when core sends a shutdown notification. Lightweight hook for last-moment state save. **Do not do heavy cleanup here** -- use `on_stop()` for that.

```python
async def on_shutdown(self) -> None:
    await self._save_state_snapshot()
```

### SmartHomeModule Event Methods

#### `async publish_event(event_type, payload) -> bool`

Publish an event via the Module Bus. Automatically buffers in an outbox (up to 500 messages) if disconnected.

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | `str` | Event type string |
| `payload` | `dict[str, Any]` | Event payload data |

**Returns:** `bool` -- `True` if sent or buffered, `False` if outbox is full

```python
await self.publish_event("device.state_changed", {
    "device_id": "plug-001",
    "state": {"on": True, "power_w": 150},
})
```

#### `async update_capabilities() -> None`

Send a `re_announce` message to hot-reload intents and subscriptions without reconnecting. Call after dynamically adding or removing intent handlers.

```python
self._intent_handlers.append((new_pattern, 50, handler, name, desc))
await self.update_capabilities()
```

### SmartHomeModule API Methods

#### `async api_request(method, path, body=None, timeout=10.0) -> dict`

Send an API request to core via the bus and wait for a response. Raises `TimeoutError` or `ConnectionError` on failure.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `method` | `str` | -- | HTTP method: `"GET"`, `"POST"`, `"PATCH"`, `"DELETE"` |
| `path` | `str` | -- | API path (e.g. `"/devices"`) |
| `body` | `Any` | `None` | Request body (JSON-serializable) |
| `timeout` | `float` | `10.0` | Maximum wait time in seconds |

**Returns:** `dict` -- response body from core

```python
devices = await self.api_request("GET", "/devices")
for d in devices.get("devices", []):
    self._log.info("Found device: %s", d["name"])
```

#### `async get_device(device_id) -> dict | None`

Fetch a single device from the registry via the bus. Returns `None` on error.

| Parameter | Type | Description |
|-----------|------|-------------|
| `device_id` | `str` | UUID of the device |

```python
device = await self.get_device("abc-123")
if device:
    self._log.info("Device state: %s", device.get("state"))
```

#### `async handle_api_request(method, path, body) -> dict`

Override to handle incoming API requests from core (UI proxy). Default returns a 404-like error.

| Parameter | Type | Description |
|-----------|------|-------------|
| `method` | `str` | HTTP method string |
| `path` | `str` | Request path |
| `body` | `Any` | Request body |

```python
async def handle_api_request(self, method: str, path: str, body) -> dict:
    if method == "GET" and path == "/status":
        return {"power": self._current_power, "on": self._is_on}
    return {"error": f"Not found: {method} {path}"}
```

### SmartHomeModule i18n Methods

#### `t(key, lang=None, **kwargs) -> str`

Translate a key using locale files from the module's `locales/` directory. Falls back to English, then returns the raw key.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `key` | `str` | -- | Translation key |
| `lang` | `str \| None` | `None` (falls back to `"en"`) | Language code |
| `**kwargs` | `Any` | -- | Interpolation values |

Locale files are loaded automatically from `locales/en.json`, `locales/uk.json` etc. next to the module file.

```python
# locales/en.json: {"plug_on": "Smart plug turned on", "power": "Current power: {watts}W"}
msg = self.t("plug_on")               # "Smart plug turned on"
msg = self.t("power", watts=150)       # "Current power: 150W"
msg = self.t("plug_on", lang="uk")     # Ukrainian translation
```

### SmartHomeModule Capabilities

Capabilities are built automatically from decorators and manifest.json, then sent to core during the `announce` handshake. Manifest intents take priority over decorator-discovered intents.

```python
# Auto-built from @intent, @on_event decorators and manifest.json
{
    "intents": [
        {
            "patterns": {"en": ["weather|forecast"], "uk": ["weather|forecast"]},
            "priority": 50,
            "name": "weather.current",
            "description": "Get current weather"
        }
    ],
    "subscriptions": ["device.*"],
    "publishes": ["custom.event"]
}
```

---

## EventBus Events Reference

### Core Events

Published only by the core. Modules cannot publish `core.*` events (403 Forbidden).

| Event | Description |
|-------|-------------|
| `core.startup` | Core process started |
| `core.shutdown` | Core process shutting down |
| `core.integrity_violation` | Integrity Agent detected file changes |
| `core.integrity_restored` | Agent rolled back changes successfully |
| `core.safe_mode_entered` | System entered SAFE MODE |
| `core.safe_mode_exited` | SAFE MODE lifted |

### Device Events

| Event | Payload | Description |
|-------|---------|-------------|
| `device.state_changed` | `{device_id, state, previous_state}` | Device state updated in registry |
| `device.registered` | `{device_id, name, type, protocol}` | New device added |
| `device.removed` | `{device_id}` | Device deleted from registry |
| `device.online` | `{device_id}` | Device available after being offline |
| `device.offline` | `{device_id}` | No heartbeat > 90 seconds |
| `device.discovered` | `{ip, mac, manufacturer}` | Network scanner found new device |
| `device.command` | `{device_id, command, params}` | Control command for a device |
| `device.protocol_heartbeat` | `{protocol, devices_count}` | Protocol health ping |

### Module Events

| Event | Payload | Description |
|-------|---------|-------------|
| `module.started` | `{name, version}` | Module started successfully |
| `module.stopped` | `{name}` | Module stopped normally |
| `module.installed` | `{name, version}` | Module installed and started |
| `module.removed` | `{name}` | Module uninstalled |
| `module.error` | `{name, error}` | Module crashed or returned error |

### Voice Events

| Event | Payload | Description |
|-------|---------|-------------|
| `voice.wake_word` | `{}` | Wake word detected |
| `voice.recognized` | `{text, lang}` | STT transcription complete |
| `voice.intent` | `{intent, params, source, raw_text, latency_ms, user_id, response, action}` | Intent router result |
| `voice.response` | `{text, query}` | LLM/fallback response ready for TTS |
| `voice.speak` | `{text, speech_id}` | TTS speech request |
| `voice.speak_done` | `{speech_id}` | TTS speech completed |
| `voice.tts_start` | `{speech_id}` | TTS engine started generating audio |
| `voice.tts_done` | `{speech_id}` | TTS engine finished generating audio |
| `voice.privacy_on` | `{}` | Privacy mode enabled (mic off) |
| `voice.privacy_off` | `{}` | Privacy mode disabled (mic on) |

### Other Events

| Event | Description |
|-------|-------------|
| `automation.triggered` | Automation rule fired |
| `sync.command_received` | Command from cloud platform |
| `sync.command_ack` | Command acknowledged |
| `registry.entity_changed` | Entity (device/scene/module/station) created/updated/deleted. Payload: `{entity_type, entity_id, action}` |

---

## WebSocket Module Bus Protocol

### Bus Connection

User modules connect to the core via WebSocket.

**URL:** `ws://core:7070/api/v1/bus?token=<MODULE_TOKEN>`

**Environment variables** (set by the container runtime):

| Variable | Description |
|----------|-------------|
| `SELENA_BUS_URL` | WebSocket URL (default: `ws://localhost:7070/api/v1/bus`) |
| `MODULE_TOKEN` | Authentication token for this module |
| `MODULE_DIR` | Path to the module directory |

**Connection lifecycle:**

```
connect(token) → announce → announce_ack → message_loop → reconnect (on error)
```

The `SmartHomeModule` base class handles connection, reconnection with exponential backoff, and message routing automatically. You do not need to manage the WebSocket directly.

### Bus Message Types

#### Module to Core

| Type | Description | Payload |
|------|-------------|---------|
| `announce` | Register capabilities on connect | `{module, capabilities}` |
| `re_announce` | Update capabilities without reconnect | `{capabilities}` |
| `intent_response` | Reply to an intent request | `{id, payload: {handled, tts_text?, data?}}` |
| `event` | Publish an event | `{payload: {event_type, data}}` |
| `api_request` | Request core API | `{id, method, path, body}` |
| `api_response` | Reply to incoming API request | `{id, payload}` |
| `pong` | Health check reply | `{ts}` |

#### Core to Module

| Type | Description | Payload |
|------|-------------|---------|
| `announce_ack` | Confirm registration | `{status, bus_id, warnings?}` |
| `intent` | Route a voice/text command | `{id, payload: {text, lang, context}}` |
| `event` | Deliver a subscribed event | `{payload: {event_type, data}}` |
| `api_request` | Proxy an API request to module | `{id, method, path, body}` |
| `api_response` | Reply to module's API request | `{id, status, body}` |
| `ping` | Health check | `{ts}` |
| `shutdown` | Core is shutting down | `{drain_ms}` |

### Bus Capabilities Format

Sent during `announce` and `re_announce`:

```json
{
  "intents": [
    {
      "patterns": {
        "en": ["weather|forecast|temperature outside"],
        "uk": ["pogoda|prognoz|temperatura"]
      },
      "priority": 50,
      "name": "weather.current",
      "description": "Get current weather conditions"
    }
  ],
  "subscriptions": ["device.state_changed", "core.shutdown"],
  "publishes": ["custom.weather_updated"]
}
```

- **intents**: Regex patterns per language. Core builds a sorted index from all module intents.
- **subscriptions**: Event types this module wants to receive (wildcards supported: `device.*`).
- **publishes**: Event types this module may publish (informational, used for ACL).

---

## Intent System

### Multi-tier Router

User voice and text commands flow through a multi-tier pipeline:

```
User text
  |
  v
Tier 1:   FastMatcher     — keyword/regex from YAML config        (~0 ms)
  |
Tier 1.5: IntentCompiler  — YAML vocabulary -> compiled regex     (~0 ms)
  |
Tier 2:   Module Bus      — user module intents via WebSocket     (~1-10 ms)
  |
Cache:    IntentCache      — SQLite cache of previous LLM results (~0 ms)
  |
Tier 3:   Local LLM       — Ollama (phi-3-mini / gemma-2b)       (300-800 ms)
  |
Tier 4:   Cloud LLM       — OpenAI-compatible API (optional)      (1-3 s)
  |
Fallback: "Sorry, I didn't understand"
```

Each tier is tried in order. The first match wins.

### Adding Voice Commands (System Module)

**Step 1** -- Add intent definition to `config/intents/definitions.yaml`:

```yaml
intents:
  mymodule.check_status:
    module: my-module
    noun_class: DEVICE
    verb: check
    priority: 5
    description: "Check module status"
    templates:
      - "{verb.check} {noun.status}"
    params: {}
    overrides:
      uk:
        - "перевір(?:и|ити)?\\s+статус"
      en:
        - "check\\s+status"
```

**Step 2** -- Add vocabulary to `config/intents/vocab/en.yaml` and `uk.yaml` if needed:

```yaml
# config/intents/vocab/en.yaml
verbs:
  check:
    exact: ["check", "verify", "show"]
nouns:
  status: ["status", "state"]
```

**Step 3** -- Register intents in `start()`:

```python
async def start(self) -> None:
    self.subscribe(["voice.intent"], self._on_intent)
    from system_modules.llm_engine.intent_router import get_intent_router
    from system_modules.llm_engine.intent_compiler import get_intent_compiler
    router = get_intent_router()
    entries = get_intent_compiler().get_intents_for_module("my-module")
    for entry in entries:
        router.register_system_intent(entry)
```

**Step 4** -- Handle the intent:

```python
async def _on_intent(self, event) -> None:
    if event.payload.get("intent") == "mymodule.check_status":
        await self.speak("All systems operational")
```

**Step 5** -- Clean up in `stop()`:

```python
async def stop(self) -> None:
    from system_modules.llm_engine.intent_router import get_intent_router
    get_intent_router().unregister_system_intents(self.name)
    self._cleanup_subscriptions()
```

### Adding Voice Commands (User Module)

**Option A -- `@intent` decorator:**

```python
class MyModule(SmartHomeModule):
    name = "my-module"

    @intent(r"check\s+(?:the\s+)?status", name="mymodule.check_status")
    async def handle_status(self, text: str, context: dict) -> dict:
        return {"handled": True, "tts_text": "All systems operational"}
```

**Option B -- manifest.json intents:**

```json
{
  "intents": [
    {
      "patterns": {
        "en": ["check\\s+status", "show\\s+status"],
        "uk": ["перевір\\s+статус"]
      },
      "priority": 50,
      "name": "mymodule.check_status",
      "description": "Check module status"
    }
  ]
}
```

The handler is still needed in code (via `@intent` or `handle_api_request`). Manifest intents control what patterns are registered in the bus index; decorators control local dispatch.

---

## Widget and Settings HTML

System modules serve `widget.html` and `settings.html` as iframes inside the SelenaCore dashboard.

### Widget BASE URL

Compute the base URL from the iframe location. Never hardcode `localhost:PORT`.

```javascript
// Correct — works in all environments
var BASE = window.location.pathname.replace(/\/(widget|settings)(\.html)?$/, '');
fetch(BASE + '/status')
    .then(function(r) { return r.json(); })
    .then(function(data) { /* ... */ });

// Wrong — breaks in production
var BASE = "http://localhost:8115";  // never do this
```

### Widget Theme CSS

Include the shared theme stylesheet for consistent appearance:

```html
<link rel="stylesheet" href="/api/shared/theme.css">
```

### Widget Localization

Every widget and settings page must implement EN/UK localization:

```html
<script>
var LANG = (function () {
    try { return localStorage.getItem('selena-lang') || 'en'; }
    catch (e) { return 'en'; }
})();

var L = {
    en: {
        title: 'Sensor Status',
        no_data: 'No data available',
        refresh: 'Refresh'
    },
    uk: {
        title: 'Стан сенсора',
        no_data: 'Немає даних',
        refresh: 'Оновити'
    }
};

function t(k) { return (L[LANG] || L.en)[k] || k; }

function applyLang() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
        el.textContent = t(el.getAttribute('data-i18n'));
    });
}
</script>

<h1 data-i18n="title"></h1>
<p data-i18n="no_data"></p>
<button data-i18n="refresh" onclick="refresh()"></button>
```

### Widget PostMessage Events

Listen for theme and language changes from the parent dashboard:

```javascript
window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'lang_changed') {
        try { LANG = localStorage.getItem('selena-lang') || 'en'; } catch (ex) {}
        applyLang();
        refresh();  // reload data in the new language
    }
    if (e.data && e.data.type === 'theme_changed') {
        // Theme CSS variables update automatically via theme.css
        // Re-render any manually styled elements here
    }
});
```

Call `applyLang()` before the first `refresh()` or `load()` call during initialization.

---

## manifest.json Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | Yes | Unique module identifier (e.g. `"weather-service"`) |
| `version` | `string` | Yes | Semantic version (e.g. `"1.0.0"`) |
| `type` | `string` | Yes | `"SYSTEM"`, `"UI"`, `"INTEGRATION"`, `"DRIVER"`, `"AUTOMATION"` |
| `runtime_mode` | `string` | Yes | `"always_on"`, `"on_demand"`, `"scheduled"` |
| `description` | `string` | No | Human-readable description |
| `api_version` | `string` | No | Core API version (e.g. `"1.0"`) |
| `port` | `integer` | No | **User modules only.** Listening port (8100-8200). SYSTEM modules must NOT have this field. |
| `group` | `string` | No | Module group for UI grouping |
| `permissions` | `array` | No | `["device.read", "device.write", "events.subscribe", "events.publish", "secrets.oauth", "secrets.proxy"]` |
| `ui` | `object` | No | `{icon, widget: {file, size}, settings}` |
| `intents` | `array` | No | Intent patterns for Module Bus registration |
| `entities` | `array` | No | Entity definitions for registry |
| `publishes` | `array` | No | Event types this module may publish |
| `resources` | `object` | No | `{memory_mb, cpu}` resource limits |
| `author` | `string` | No | Author name |
| `license` | `string` | No | License identifier |
| `homepage` | `string` | No | Repository or documentation URL |

**SYSTEM module example:**

```json
{
    "name": "sensor-aggregator",
    "version": "1.0.0",
    "type": "SYSTEM",
    "runtime_mode": "always_on",
    "description": "Aggregates sensor data from multiple protocols",
    "permissions": ["device.read", "events.subscribe", "events.publish"]
}
```

**User module example:**

```json
{
    "name": "smart-plug",
    "version": "1.0.0",
    "type": "UI",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "port": 8101,
    "permissions": ["device.read", "device.write", "events.subscribe", "events.publish"],
    "ui": {
        "icon": "icon.svg",
        "widget": {"file": "widget.html", "size": "2x1"},
        "settings": "settings.html"
    },
    "intents": [
        {
            "patterns": {"en": ["toggle\\s+plug", "plug\\s+(on|off)"], "uk": ["розетк"]},
            "priority": 50,
            "name": "plug.toggle",
            "description": "Toggle smart plug on/off"
        }
    ]
}
```

---

## Examples

### Example: System Module -- Sensor Aggregator

A system module that collects temperature readings from all sensors and publishes an aggregate event every 60 seconds.

**File structure:**

```
system_modules/sensor_aggregator/
    __init__.py
    module.py
    manifest.json
    widget.html
```

**`__init__.py`:**

```python
from .module import SensorAggregatorModule as module_class

__all__ = ["module_class"]
```

**`manifest.json`:**

```json
{
    "name": "sensor-aggregator",
    "version": "1.0.0",
    "type": "SYSTEM",
    "runtime_mode": "always_on",
    "description": "Aggregates temperature data from all sensors",
    "permissions": ["device.read", "events.subscribe", "events.publish"]
}
```

**`module.py`:**

```python
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter

from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)


class SensorAggregatorModule(SystemModule):
    name = "sensor-aggregator"

    def __init__(self) -> None:
        super().__init__()
        self._readings: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self.subscribe(["device.state_changed"], self._on_state_changed)
        self._task = asyncio.create_task(self._aggregate_loop())
        logger.info("SensorAggregator started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self._cleanup_subscriptions()
        logger.info("SensorAggregator stopped")

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/summary")
        async def get_summary() -> dict[str, Any]:
            temps = list(self._readings.values())
            return {
                "sensor_count": len(temps),
                "average": sum(temps) / len(temps) if temps else 0,
                "min": min(temps) if temps else 0,
                "max": max(temps) if temps else 0,
                "readings": self._readings,
            }

        self._register_html_routes(router, __file__)
        self._register_health_endpoint(router)
        return router

    async def _on_state_changed(self, event: Any) -> None:
        device_id = event.payload.get("device_id", "")
        state = event.payload.get("state", {})
        if "temperature" in state:
            self._readings[device_id] = state["temperature"]

    async def _aggregate_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            if not self._readings:
                continue
            temps = list(self._readings.values())
            await self.publish("sensor.aggregate", {
                "average": sum(temps) / len(temps),
                "count": len(temps),
            })
```

The router is mounted at `/api/ui/modules/sensor-aggregator/summary`.

### Example: User Module -- Smart Plug Controller

A user module running in Docker that controls smart plugs via voice and events.

**File structure:**

```
smart-plug-module/
    main.py
    manifest.json
    locales/
        en.json
        uk.json
```

**`manifest.json`:**

```json
{
    "name": "smart-plug",
    "version": "1.0.0",
    "type": "UI",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "port": 8101,
    "permissions": ["device.read", "device.write", "events.subscribe", "events.publish"],
    "intents": [
        {
            "patterns": {
                "en": ["(?:turn\\s+)?(on|off)\\s+(?:the\\s+)?plug", "toggle\\s+plug"],
                "uk": ["(?:увімкн|вимкн)\\w*\\s+розетк"]
            },
            "priority": 50,
            "name": "plug.toggle",
            "description": "Toggle smart plug on/off"
        }
    ],
    "publishes": ["plug.state_changed"]
}
```

**`locales/en.json`:**

```json
{
    "plug_on": "Smart plug turned on",
    "plug_off": "Smart plug turned off",
    "power_report": "Current power consumption: {watts} watts"
}
```

**`locales/uk.json`:**

```json
{
    "plug_on": "Розетку увімкнено",
    "plug_off": "Розетку вимкнено",
    "power_report": "Поточне споживання: {watts} ватт"
}
```

**`main.py`:**

```python
from __future__ import annotations

import asyncio
import logging

from sdk.base_module import SmartHomeModule, intent, on_event, scheduled

logger = logging.getLogger(__name__)


class SmartPlugModule(SmartHomeModule):
    name = "smart-plug"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__()
        self._is_on: bool = False
        self._power_w: float = 0.0
        self._device_id: str | None = None

    async def on_start(self) -> None:
        self._log.info("Smart Plug module initializing")

    async def on_stop(self) -> None:
        self._log.info("Smart Plug module stopping")

    @intent(r"(?:turn\s+)?(on|off)\s+(?:the\s+)?plug|toggle\s+plug",
            name="plug.toggle", description="Toggle smart plug")
    async def handle_toggle(self, text: str, context: dict) -> dict:
        lang = context.get("_lang", "en")
        if "off" in text.lower():
            self._is_on = False
            return {"handled": True, "tts_text": self.t("plug_off", lang=lang)}
        else:
            self._is_on = True
            return {"handled": True, "tts_text": self.t("plug_on", lang=lang)}

    @on_event("device.state_changed")
    async def on_device_change(self, data: dict) -> None:
        if data.get("device_id") == self._device_id:
            state = data.get("state", {})
            self._is_on = state.get("on", self._is_on)
            self._power_w = state.get("power_w", self._power_w)

    @scheduled("every:30s")
    async def poll_power(self) -> None:
        if not self._device_id:
            return
        device = await self.get_device(self._device_id)
        if device:
            self._power_w = device.get("state", {}).get("power_w", 0)
            await self.publish_event("plug.state_changed", {
                "device_id": self._device_id,
                "on": self._is_on,
                "power_w": self._power_w,
            })

    async def handle_api_request(self, method: str, path: str, body) -> dict:
        if method == "GET" and path == "/status":
            return {"on": self._is_on, "power_w": self._power_w}
        if method == "POST" and path == "/toggle":
            self._is_on = not self._is_on
            return {"on": self._is_on}
        return {"error": f"Not found: {method} {path}"}


if __name__ == "__main__":
    asyncio.run(SmartPlugModule().start())
```

### Example: Integration Module -- External API Bridge

A module that bridges an external weather API to the SelenaCore device registry, publishing periodic updates.

**`main.py`:**

```python
from __future__ import annotations

import asyncio
import logging

from sdk.base_module import SmartHomeModule, intent, scheduled

logger = logging.getLogger(__name__)

WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherBridgeModule(SmartHomeModule):
    name = "weather-bridge"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__()
        self._lat: float = 50.45
        self._lon: float = 30.52
        self._last_weather: dict = {}

    async def on_start(self) -> None:
        self._log.info("Weather Bridge starting (lat=%s, lon=%s)", self._lat, self._lon)

    async def on_stop(self) -> None:
        self._log.info("Weather Bridge stopped")

    @intent(r"weather|forecast|temperature\s+outside",
            name="weather.current", description="Current weather conditions")
    async def handle_weather(self, text: str, context: dict) -> dict:
        if not self._last_weather:
            return {"handled": True, "tts_text": "Weather data not yet available"}
        temp = self._last_weather.get("temperature", "unknown")
        desc = self._last_weather.get("description", "")
        return {
            "handled": True,
            "tts_text": f"Currently {temp} degrees, {desc}",
            "data": self._last_weather,
        }

    @scheduled("every:5m")
    async def fetch_weather(self) -> None:
        try:
            import urllib.request
            import json
            url = (
                f"{WEATHER_API_URL}"
                f"?latitude={self._lat}&longitude={self._lon}"
                f"&current_weather=true"
            )
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            current = data.get("current_weather", {})
            self._last_weather = {
                "temperature": current.get("temperature"),
                "windspeed": current.get("windspeed"),
                "description": self._weather_code(current.get("weathercode", 0)),
            }
            await self.publish_event("weather.updated", self._last_weather)
        except Exception as exc:
            self._log.error("Weather fetch failed: %s", exc)

    @staticmethod
    def _weather_code(code: int) -> str:
        codes = {0: "clear sky", 1: "mainly clear", 2: "partly cloudy",
                 3: "overcast", 45: "fog", 61: "light rain", 71: "light snow"}
        return codes.get(code, "unknown")

    async def handle_api_request(self, method: str, path: str, body) -> dict:
        if method == "GET" and path == "/current":
            return self._last_weather if self._last_weather else {"error": "No data yet"}
        if method == "POST" and path == "/location":
            self._lat = body.get("lat", self._lat)
            self._lon = body.get("lon", self._lon)
            return {"lat": self._lat, "lon": self._lon}
        return {"error": f"Not found: {method} {path}"}


if __name__ == "__main__":
    asyncio.run(WeatherBridgeModule().start())
```

**`manifest.json`:**

```json
{
    "name": "weather-bridge",
    "version": "1.0.0",
    "type": "INTEGRATION",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "port": 8102,
    "permissions": ["events.publish"],
    "intents": [
        {
            "patterns": {
                "en": ["weather|forecast|temperature\\s+outside"],
                "uk": ["погод|прогноз|температур\\w+\\s+надвор"]
            },
            "priority": 50,
            "name": "weather.current",
            "description": "Current weather conditions"
        }
    ],
    "publishes": ["weather.updated"],
    "resources": {"memory_mb": 64, "cpu": 0.1},
    "author": "SmartHome LK",
    "license": "MIT"
}
```

---

*SelenaCore Module Developer API Guide -- SmartHome LK -- MIT License*
*Repository: https://github.com/dotradepro/SelenaCore*
