# Module Development for SelenaCore

## What is a Module

> **Note:** This guide covers **user modules** (types: UI, INTEGRATION, DRIVER, AUTOMATION) that run in Docker containers.
> **System modules** (type: SYSTEM) run in-process inside the core. They inherit from `SystemModule` (`core/module_loader/system_module.py`) and communicate with the core through direct Python calls, not WebSocket. See `AGENTS.md` §17 for system module architecture.

A user module is an isolated microservice that runs in a Docker container and communicates with the core through the **Module Bus** (WebSocket connection to `ws://core:7070/api/v1/bus`).

A module can:
- Register voice intents via bus announce
- Subscribe to Event Bus events via bus
- Publish events (except `core.*`)
- Request Core API data via bus proxy (devices, secrets)
- Store OAuth tokens through Secrets Vault

A module **cannot**:
- Read `/secure/` directly
- Access the core's SQLite database
- Publish `core.*` events
- Obtain an OAuth token directly (only via API proxy)
- Stop other modules

---

## Module Structure

Minimum ZIP archive structure:

```
my-module.zip
  manifest.json          <- required
  main.py                <- entry point (asyncio.run)
  requirements.txt       <- Python dependencies
  locales/               <- i18n translation files
    en.json
    uk.json
  icon.svg               <- UI icon (if type: UI)
```

---

## manifest.json

```json
{
  "name": "my-module",
  "version": "1.0.0",
  "description": "Brief module description",
  "type": "UI",
  "ui_profile": "FULL",
  "api_version": "1.0",
  "runtime_mode": "always_on",
  "permissions": [
    "devices.read",
    "events.publish"
  ],
  "intents": [
    {
      "patterns": {
        "en": ["weather", "forecast", "temperature outside"],
        "uk": ["погода", "прогноз", "температура надворі"]
      },
      "priority": 50,
      "description": "Answer weather questions"
    }
  ],
  "publishes": [
    "weather.module_started"
  ],
  "ui": {
    "icon": "icon.svg",
    "widget": {
      "file": "widget.html",
      "size": "2x1"
    },
    "settings": "settings.html"
  },
  "resources": {
    "memory_mb": 128,
    "cpu": 0.25
  },
  "author": "Your Name",
  "license": "MIT"
}
```

### Required Fields

| Field | Allowed Values | Notes |
|-------|---------------|-------|
| `name` | `[a-z0-9-]+` | RFC 1123 slug, unique name |
| `version` | `1.2.3` | semver |
| `type` | `UI`, `INTEGRATION`, `DRIVER`, `AUTOMATION`, `IMPORT_SOURCE` | SYSTEM — core only |
| `api_version` | `"1.0"` | Current API version |
| `permissions` | see below | List of permissions |

### Permissions

| Permission | Available for Types | Description |
|------------|-------------------|-------------|
| `devices.read` | all | Read devices via bus API proxy |
| `devices.control` | all | POST /devices/{id}/control |
| `events.publish` | all | Publish events via bus |
| `events.subscribe_all` | all | Subscribe to wildcard `*` events |
| `secrets.read` | all | Read secrets via bus API proxy |
| `secrets.oauth` | INTEGRATION only | Start OAuth flow |
| `secrets.proxy` | INTEGRATION only | API proxy through vault |
| `modules.list` | all | List modules via bus API proxy |

### Intents

Declare voice intent patterns in `manifest.json`. The core uses these for Tier 2 intent routing via Module Bus.

| Field | Required | Description |
|-------|----------|-------------|
| `patterns` | Yes | `{"en": [...], "uk": [...]}` — regex patterns per language |
| `priority` | No | 0-29 system, 30-49 core, 50-99 user (default: 50) |
| `description` | No | Human-readable description |

### runtime_mode

| Value | Behavior |
|-------|----------|
| `always_on` | Starts with core, restarts on failure |
| `on_demand` | Starts on request, stays while active |
| `scheduled` | Starts on schedule (cron expression) |

### ui_profile (for type: UI only)

| Profile | What is Displayed |
|---------|------------------|
| `HEADLESS` | No UI |
| `SETTINGS_ONLY` | Settings page only |
| `ICON_SETTINGS` | Menu icon + settings |
| `FULL` | Icon + dashboard widget + settings |

---

## SDK — base_module.py

```python
import asyncio
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled

class MyModule(SmartHomeModule):
    name = "my-module"
    version = "1.0.0"

    # === Lifecycle ===

    async def on_start(self):
        """Called once before bus connection."""
        self._log.info("Module started")

    async def on_stop(self):
        """Called once during graceful stop (resource cleanup)."""
        pass

    async def on_shutdown(self):
        """Called when core sends shutdown notification (lightweight, no cleanup)."""
        pass

    # === Voice Intents ===

    @intent(r"weather|forecast|temperature", order=50)
    async def handle_weather(self, text: str, context: dict) -> dict:
        """Handle voice command. Return dict with tts_text and/or data."""
        lang = context.get("_lang", "en")
        forecast = await self._get_forecast()
        return {
            "tts_text": self.t("forecast", lang=lang, temp=forecast["temp"]),
            "data": forecast
        }

    # === Event handlers ===

    @on_event("device.state_changed")
    async def handle_state_changed(self, payload: dict):
        """Called on each device state change."""
        device_id = payload["device_id"]
        self._log.debug("Device %s state changed", device_id)

    @on_event("device.*")
    async def handle_all_device_events(self, payload: dict):
        """Wildcard subscription — matches device.state_changed, device.offline, etc."""
        pass

    # === Scheduled tasks ===

    @scheduled("every:5m")
    async def periodic_sync(self):
        """Runs every 5 minutes."""
        devices = await self.api_request("GET", "/devices")
        for device in devices.get("devices", []):
            await self._sync_device(device)

    # === Core API ===

    async def _sync_device(self, device: dict):
        await self.publish_event("climate.updated", {
            "device_id": device["device_id"],
            "temperature": 22.5
        })

# Entry point
if __name__ == "__main__":
    module = MyModule()
    asyncio.run(module.start())
```

### Available SmartHomeModule Methods

```python
# === Lifecycle (override) ===
await self.on_start()       # called once before bus connection
await self.on_stop()        # called once during graceful stop
await self.on_shutdown()    # called on core shutdown notification

# === Events ===
await self.publish_event(event_type, payload)  # publish via bus (buffered if disconnected)

# === Core API proxy (via bus) ===
result = await self.api_request("GET", "/devices")
result = await self.api_request("POST", "/devices/abc/control", body={"action": "on"})
device = await self.get_device("device-123")

# === Capabilities ===
await self.update_capabilities()  # hot-reload intents/subscriptions without reconnect

# === i18n (autonomous, no core dependency) ===
text = self.t("greeting", lang="uk")           # from locales/uk.json
text = self.t("status", count=5, name="sensor") # with interpolation

# === Properties ===
self._log          # logging.Logger with module name
self.name          # module name
self.version       # module version
```

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `SELENA_BUS_URL` | WebSocket bus URL | `ws://selena-core:7070/api/v1/bus` |
| `MODULE_TOKEN` | Authentication token | (set by sandbox) |
| `MODULE_DIR` | Module directory path | `/opt/selena-module` |

---

## Voice Intents — Adding Voice Commands

Any module can receive voice commands by declaring **intent patterns**. When a user says a matching phrase, IntentRouter routes the command via Module Bus.

### Flow

```
User speaks → STT → IntentRouter Tier 2 → Module Bus → @intent handler → TTS response
```

### User Module — Using `@intent` Decorator

```python
from sdk.base_module import SmartHomeModule, intent

class MyModule(SmartHomeModule):
    name = "my-module"

    @intent(r"погода|прогноз|weather|forecast", order=50)
    async def handle_weather(self, text: str, context: dict) -> dict:
        lang = context.get("_lang", "en")
        forecast = await self._get_forecast()
        return {
            "tts_text": self.t("forecast", lang=lang, temp=forecast["temp"]),
            "data": forecast
        }
```

**Declare patterns in `manifest.json`** (takes priority over decorators for bus routing):

```json
{
  "intents": [
    {
      "patterns": {
        "en": ["weather", "forecast", "what.*weather"],
        "uk": ["погода", "прогноз", "яка погода"]
      },
      "priority": 50,
      "description": "Answer weather questions"
    }
  ]
}
```

**Response contract:**

```json
{
  "handled": true,
  "tts_text": "Spoken response text for TTS",
  "data": { "any": "structured data" }
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `handled` | Yes | `true` if the module processed the command |
| `tts_text` | No | Text to speak via TTS. Empty = no speech |
| `data` | No | Arbitrary data (logged, available in events) |

### System Module — Direct Intent Registration

System modules register patterns directly with IntentRouter (no bus overhead).

**Step 1 — Define patterns** (`intent_patterns.py`):

```python
from system_modules.llm_engine.intent_router import SystemIntentEntry

MY_INTENTS = [
    SystemIntentEntry(
        module="my-module",
        intent="mymodule.do_action",
        priority=5,
        description="Execute custom action",
        patterns={
            "uk": [r"зроби\s+(?P<what>.+)"],
            "en": [r"do\s+(?P<what>.+)"],
        },
    ),
]
```

**Step 2 — Register in `start()`, unregister in `stop()`:**

```python
async def start(self):
    self.subscribe(["voice.intent"], self._on_event)

    from system_modules.llm_engine.intent_router import get_intent_router
    from .intent_patterns import MY_INTENTS
    for entry in MY_INTENTS:
        get_intent_router().register_system_intent(entry)

async def stop(self):
    from system_modules.llm_engine.intent_router import get_intent_router
    get_intent_router().unregister_system_intents(self.name)
    self._cleanup_subscriptions()
```

### Parameter Extraction with Named Groups

Use `(?P<name>...)` in regex to extract parameters:

```python
# Pattern: r"set volume (?:to )?(?P<level>\d+)"
# Input:   "set volume to 50"
# Result:  params = {"level": "50"}
```

### Multi-language Patterns

Always provide patterns for all supported languages (`uk`, `en`). If the current language has no patterns, `en` is used as fallback.

```python
patterns={
    "uk": [r"увімкни\s+радіо"],
    "en": [r"(?:play|turn on)\s+radio"],
}
```

---

## Local Development

### Step 1 — Create Module

```bash
mkdir my-climate-module && cd my-climate-module
# Create: manifest.json, main.py, requirements.txt, locales/
```

### Step 2 — Run Module

```bash
# Set environment variables
export SELENA_BUS_URL=ws://localhost:7070/api/v1/bus
export MODULE_TOKEN=test-module-token-xyz
export MODULE_DIR=$(pwd)

# Run (connects to core via WebSocket bus)
python main.py
```

### Step 3 — Develop Module

```python
# main.py
import asyncio
from sdk.base_module import SmartHomeModule, intent, on_event

class MyClimateModule(SmartHomeModule):
    name = "my-climate-module"
    version = "1.0.0"

    async def on_start(self):
        self._log.info("Climate module started")

    @intent(r"temperature|temp|how hot")
    async def handle_temp(self, text: str, context: dict) -> dict:
        return {"tts_text": "Current temperature is 22 degrees"}

if __name__ == "__main__":
    module = MyClimateModule()
    asyncio.run(module.start())
```

### Step 4 — Tests

```bash
pytest tests/
```

### Step 5 — Install to SelenaCore

```bash
smarthome publish --core http://localhost:7070
# Builds ZIP, sends to POST /api/v1/modules/install
# Tracks status via SSE
```

---

## Localization (i18n) for Modules

All user-facing strings (TTS responses, error messages) must use the `self.t()` function. No hardcoded text.

### User Module (Docker)

User modules bundle their own locale files and use **autonomous i18n** (no `core.i18n` dependency):

```
my-module/
  locales/
    en.json
    uk.json
  main.py
```

Use `self.t()` — loads from `locales/` directory:

```python
class MyModule(SmartHomeModule):
    name = "my-module"

    @intent(r"weather|forecast")
    async def handle(self, text: str, context: dict) -> dict:
        lang = context.get("_lang", "en")
        return {"tts_text": self.t("forecast", lang=lang, temp=22)}
```

`locales/en.json`:
```json
{
  "forecast": "Temperature is {temp} degrees"
}
```

`locales/uk.json`:
```json
{
  "forecast": "Температура {temp} градусів"
}
```

### System Module

System modules use `core.i18n` and register translations in `config/locales/`:

```python
from core.i18n import t

text = t("mymodule.greeting", lang="uk")
text = t("mymodule.status", count=5, name="sensor")
```

### Rules

- Always add translations to **both** `en.json` and `uk.json`
- Fallback chain: requested language -> `en` -> raw key (never crashes)
- Logger messages are NOT translated
- Key format: `section.key` (e.g. `media.paused`, `api.device_not_found`)

---

## manifest.json Structure for OAuth Integration

```json
{
  "name": "gmail-integration",
  "type": "INTEGRATION",
  "permissions": [
    "secrets.oauth",
    "secrets.proxy"
  ],
  "oauth": {
    "provider": "google",
    "scopes": ["gmail.readonly", "gmail.send"]
  }
}
```

Usage in code:

```python
# Start OAuth flow (QR code on screen)
result = await self.api_request("POST", "/secrets/oauth/start", body={
    "module": "gmail-integration",
    "provider": "google",
    "scopes": ["gmail.readonly"]
})

# Execute API request — core injects the token
resp = await self.api_request("POST", "/secrets/proxy", body={
    "module": "gmail-integration",
    "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages",
    "method": "GET"
})
# Token NEVER leaves the core
```

---

## Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `403 Forbidden` on event publish | Event type starts with `core.` or not in `publishes` | Update manifest `publishes` |
| `403 Forbidden` on `/modules/{name}/stop` | Attempting to stop a SYSTEM module | Not allowed |
| `422 Unprocessable Entity` on install | Error in manifest.json | Check required fields |
| `409 Conflict` on install | Module with this name already exists | DELETE first |
| Module not connecting to bus | Wrong `SELENA_BUS_URL` or `MODULE_TOKEN` | Check env variables |
| `intent.module_unavailable` TTS | Module disconnected or circuit breaker open | Check module logs |
| `400 Bad Request` on proxy | URL is not https:// or private IP | Only public HTTPS endpoints |
