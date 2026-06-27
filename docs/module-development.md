# Module Development Guide

This guide covers everything you need to build, test, and distribute user modules for SelenaCore.

## Overview

User modules extend SelenaCore with custom functionality. Each module runs in its own Docker container and communicates with the core through the WebSocket Module Bus. This isolation ensures that a misbehaving module cannot crash the core or interfere with other modules.

## Module Types

| Type | Purpose |
|------|---------|
| `UI` | Modules with a visual dashboard widget and/or settings page |
| `INTEGRATION` | Third-party service integrations (cloud APIs, external platforms) |
| `DRIVER` | Hardware device drivers (Zigbee dongles, serial peripherals) |
| `AUTOMATION` | Rule engines, schedulers, scene controllers |
| `IMPORT_SOURCE` | Data importers (CSV, database sync, migration tools) |

> **Note:** The `SYSTEM` type is reserved for core-internal modules. User modules must use one of the five types listed above.

## Module Structure

A minimal module requires only `manifest.json` and `main.py`. The full directory layout:

```
my-module/
  manifest.json        # Module metadata and capabilities (required)
  main.py              # Entry point (required)
  locales/
    en.json            # English translations
    uk.json            # Ukrainian translations
  widget.html          # UI widget rendered on the dashboard
  settings.html        # Settings page for module configuration
  icon.svg             # Module icon (SVG format)
  tests/               # Unit and integration tests
```

## manifest.json Reference

The manifest declares your module's identity, capabilities, permissions, and resource limits. The core reads this file during installation and uses it to enforce access control at runtime.

### Full Example

```json
{
    "name": "weather-module",
    "version": "1.0.0",
    "description": "Current weather and forecast via Open-Meteo",
    "type": "UI",
    "ui_profile": "FULL",
    "api_version": "1.0",
    "runtime_mode": "always_on",
    "permissions": ["devices.read", "events.publish"],
    "intents": [
        {
            "patterns": {
                "uk": ["погода", "прогноз", "температур"],
                "en": ["weather", "forecast", "temperatur"]
            },
            "priority": 50,
            "description": "Answer weather questions"
        }
    ],
    "publishes": ["weather.module_started"],
    "ui": {
        "icon": "icon.svg",
        "widget": {"file": "widget.html", "size": "2x2"},
        "settings": "settings.html"
    },
    "resources": {"memory_mb": 128, "cpu": 0.25}
}
```

### Field Reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Lowercase alphanumeric and hyphens, 2-64 characters. Must match the `name` class attribute in your Python module. |
| `version` | string | Semantic version `X.Y.Z`. Must match the `version` class attribute. |
| `type` | string | One of: `UI`, `INTEGRATION`, `DRIVER`, `AUTOMATION`, `IMPORT_SOURCE`. |
| `ui_profile` | string | `HEADLESS` (no UI), `SETTINGS_ONLY`, `ICON_SETTINGS`, or `FULL` (widget + settings). |
| `api_version` | string | Currently `"1.0"`. |
| `runtime_mode` | string | `always_on` (runs continuously), `on_demand` (started when needed), `scheduled` (runs on a timer). |
| `permissions` | array | Capabilities the module requires. See [Permissions](#permissions) below. |
| `intents` | array | Voice intent definitions. See [Intents](#intents) below. |
| `publishes` | array | Event types the module may emit. Events not listed here are rejected by the bus. |
| `ui` | object | UI asset references: `icon` (SVG path), `widget` (HTML file + grid size), `settings` (HTML file). |
| `resources` | object | Docker container limits: `memory_mb` (integer) and `cpu` (float, where 1.0 = one full core). |

### Permissions

Permissions control what a module can access through the bus. Request only what you need.

| Permission | Grants |
|------------|--------|
| `devices.read` | Read device list and state |
| `devices.write` | Modify device state |
| `events.subscribe` | Listen to EventBus events |
| `events.publish` | Emit events (limited to types in `publishes`) |

### Intents

Intent definitions allow your module to respond to voice commands. Each intent includes:

- `patterns`: a dictionary keyed by language code (`en`, `uk`, etc.), where each value is an array of regex substrings that trigger this intent.
- `priority`: an integer from 0-99. Lower values mean higher priority. **User modules should use 50-99** (0-49 is reserved for core modules).
- `description`: a human-readable explanation of what this intent handles.

> **Writing intents that actually get recognized** — the `description` field is cosine-matched by the voice classifier, so the way you word it directly determines accuracy. Before adding a new intent, read [intent-authoring.md](intent-authoring.md) for the description recipe, anchor rules, Helsinki UK→EN quirks, and the PR bench gate. Skipping it typically lands a new intent at 50-70% accuracy instead of ≥ 90%.

---

## SDK Reference

### SmartHomeModule Base Class

All modules inherit from `SmartHomeModule`. Import it along with the decorator functions:

```python
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled
```

Your subclass must declare two class attributes that match the manifest:

```python
class MyModule(SmartHomeModule):
    name = "my-module"       # Must match manifest.json "name"
    version = "1.0.0"        # Must match manifest.json "version"
```

---

## Decorators

### @intent(pattern, order=50)

Register a method as a voice intent handler.

| Parameter | Type | Description |
|-----------|------|-------------|
| `pattern` | str | Regex pattern (case-insensitive) to match against user speech. |
| `order` | int | Priority 0-99. Lower fires first. User modules should use 50-99. |

The decorated method receives the raw text and a context dictionary. Return a result dictionary or signal that this handler cannot process the request:

```python
@intent(r"погода|weather|forecast", order=50)
async def handle_weather(self, text: str, context: dict) -> dict:
    lang = context.get("_lang", "en")

    # If this handler cannot process the request, pass to the next handler:
    if "weekly" in text:
        return {"handled": False}

    # Otherwise, return a response:
    return {
        "tts_text": self.t("current_weather", lang=lang, city="Kyiv", temp=12),
        "data": {"temperature": 12, "condition": "cloudy"},
    }
```

### @on_event(event_type)

Subscribe to EventBus events. Supports wildcard patterns with `*`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `event_type` | str | Event type string. Use `*` for wildcards (e.g., `device.*`). |

```python
@on_event("device.state_changed")
async def on_device_changed(self, data: dict) -> None:
    device_id = data.get("device_id")
    new_state = data.get("state")
    self._log.info("Device %s changed to %s", device_id, new_state)

@on_event("device.*")
async def on_any_device_event(self, data: dict) -> None:
    self._log.debug("Device event: %s", data)
```

### @scheduled(cron)

Run a method on a recurring schedule.

| Format | Example | Description |
|--------|---------|-------------|
| Simple interval | `"every:30s"` | Every 30 seconds |
| Simple interval | `"every:5m"` | Every 5 minutes |
| Simple interval | `"every:1h"` | Every hour |
| Full cron | `"*/5 * * * *"` | Every 5 minutes (requires `apscheduler`) |

```python
@scheduled("every:10m")
async def refresh_cache(self) -> None:
    self._log.debug("Refreshing cache")
    # ... update cached data ...
```

---

## Lifecycle Methods

Override these methods to hook into the module lifecycle. All are optional.

```python
async def on_start(self) -> None:
    """Called once before the bus connection is established.
    Use this to initialize resources, load config, set up state."""

async def on_stop(self) -> None:
    """Called during graceful shutdown.
    Use this to clean up resources, close connections, flush buffers."""

async def on_shutdown(self) -> None:
    """Called when the core sends a shutdown notification.
    Use this only for last-moment state saving. Keep it fast."""
```

---

## Built-in Methods

### publish_event

Emit an event through the bus. The event type must be listed in the manifest `publishes` array.

```python
await self.publish_event("weather.module_started", {"status": "ready"})
await self.publish_event("weather.data_updated", {
    "city": "Kyiv",
    "temperature": 12,
    "condition": "cloudy",
})
```

### api_request

Call the core REST API through the bus proxy. Requests are subject to ACL enforcement based on your manifest permissions.

```python
result = await self.api_request(method: str, path: str, body: dict | None = None) -> dict
```

Examples:

```python
# List all devices
devices = await self.api_request("GET", "/devices")

# Get a specific device
device = await self.api_request("GET", f"/devices/{device_id}")

# Update device state
await self.api_request("PATCH", f"/devices/{device_id}/state", {
    "state": {"power": True}
})

# Publish an event (alternative to publish_event)
await self.api_request("POST", "/events/publish", {
    "type": "my.custom_event",
    "source": self.name,
    "payload": {"key": "value"},
})
```

### update_capabilities

Hot-reload the module's capabilities (re-announce to the bus) without reconnecting. Useful after dynamic configuration changes.

```python
await self.update_capabilities()
```

### t (translate)

Translate a key using the module's locale files. Falls back through: requested language, then English, then the raw key string.

```python
text = self.t(key: str, lang: str | None = None, **kwargs) -> str
```

```python
msg = self.t("current_weather", lang="uk", city="Kyiv", temp=12)
err = self.t("fetch_error", lang="en")
```

### Logging

Every module has a built-in logger at `self._log`:

```python
self._log.debug("Detailed trace info")
self._log.info("Normal operational message")
self._log.warning("Something unexpected but recoverable")
self._log.error("Something failed: %s", error_message)
```

---

## Environment Variables

The core injects these environment variables into the module's Docker container at startup:

| Variable | Description | Default |
|----------|-------------|---------|
| `SELENA_BUS_URL` | WebSocket bus endpoint | `ws://localhost/api/v1/bus` |
| `MODULE_TOKEN` | Authentication token for the bus connection | (generated by core) |
| `MODULE_DIR` | Absolute path to the module's working directory | (set by core) |
| `PYTHONPATH` | Includes the project root and module directory | (set by core) |

---

## Connection Behavior

The SDK handles bus connectivity automatically:

- **Auto-reconnect** with exponential backoff: starts at 1 second, caps at 60 seconds, with 30% jitter to avoid thundering herd.
- **Fatal disconnect reasons** that stop reconnection attempts: `invalid_token`, `permission_denied`. These indicate configuration problems that require manual intervention.
- **Outbox queue**: up to 500 messages are buffered while disconnected. The queue is automatically flushed when the connection is restored. Messages beyond 500 are dropped (oldest first).

---

## Internationalization (i18n)

### Setting Up Locale Files

Create JSON files in the `locales/` directory, one per supported language:

**locales/en.json**
```json
{
    "current_weather": "{emoji} {city}: {sign}{temp}{unit}, {condition}. Feels like {fl_sign}{feels_like}{unit}. Humidity {humidity}%, wind {wind} m/s",
    "fetch_error": "Could not fetch weather data",
    "module_ready": "Weather module is ready"
}
```

**locales/uk.json**
```json
{
    "current_weather": "{emoji} {city}: {sign}{temp}{unit}, {condition}. Відчувається як {fl_sign}{feels_like}{unit}. Вологість {humidity}%, вітер {wind} м/с",
    "fetch_error": "Не вдалося отримати дані про погоду",
    "module_ready": "Модуль погоди готовий"
}
```

### Using Translations

```python
# With named placeholders
msg = self.t("current_weather", lang="uk", city="Kyiv", temp=12,
             sign="+", unit="C", condition="хмарно",
             emoji="cloudy", fl_sign="+", feels_like=9,
             humidity=78, wind=5)

# Simple key without placeholders
err = self.t("fetch_error", lang="en")
```

**Fallback chain:** requested language -> `"en"` -> raw key string. If a key is missing from all locale files, the key name itself is returned.

### Rules

- Always add translations to **both** `en.json` and `uk.json`.
- Logger messages are NOT translated (they stay in English for debugging).
- Key format: `section.key` or flat keys (e.g., `current_weather`, `fetch_error`).

### Locale file tiers (v0.4.0+)

The `locales/` directory supports four file tiers per language. They merge
in priority order — later entries override earlier ones when keys collide:

| File                     | Tier          | Written by                                     |
|--------------------------|---------------|-----------------------------------------------|
| `en.json`                | reference     | Module author (manual)                         |
| `{lang}.auto.json`       | auto (lowest) | `scripts/generate_auto_locales.py --modules`   |
| `{lang}.community.json`  | community     | Community PR                                  |
| `{lang}.json`            | manual (highest) | Module author (human-translated)            |

`.auto.json` files are generated on CI from `en.json` via the project
auto-translation pipeline (Argos). Never hand-edit them — your changes
will be overwritten on the next regeneration. If an auto translation is
wrong, drop a `{lang}.community.json` override instead; it ranks above
auto but below the module author's manual file.

The SDK's `self.t()` and the core `/api/i18n/bundle/{module}?lang={lang}`
endpoint both honor this tier order. For widgets / settings.html, use
the endpoint:

```html
<script>
var LANG = (function () { try { return localStorage.getItem('selena-lang') || 'en'; } catch (e) { return 'en'; } })();
var L = { en: {} };

async function loadI18n() {
    async function fetchLang(lang) {
        const r = await fetch('/api/i18n/bundle/my-module?lang=' + encodeURIComponent(lang));
        if (r.ok) L[lang] = await r.json();
    }
    const targets = ['en'];
    if (LANG !== 'en') targets.push(LANG);
    await Promise.all(targets.map(fetchLang));
}

loadI18n().then(() => {
    applyLang();  // provided by widget-common.js
    // ... rest of your init
});
</script>
```

The endpoint merges `core/i18n/common/*.json` (shared strings like
Save/Cancel/Loading) with your module's `locales/*.json`, so common
UI chrome doesn't need to be re-translated per module.

**Deprecated:** the legacy `var L = { en: {...}, uk: {...} }` inline
pattern inside `settings.html` is deprecated as of v0.4.0. New modules
should use the fetch pattern above; existing system modules are being
migrated to it (see `system_modules/voice_core/settings.html` for the
canonical example).

---

## Complete Example

This is a fully working weather module demonstrating all major SDK features:

```python
"""Weather module for SelenaCore."""
import asyncio
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled


class WeatherModule(SmartHomeModule):
    name = "weather-module"
    version = "1.0.0"

    async def on_start(self) -> None:
        """Initialize the module and announce readiness."""
        self._log.info("Weather module started")
        await self.publish_event("weather.module_started", {"status": "ready"})

    @intent(r"погода|прогноз|weather|forecast|temperatur")
    async def handle_weather(self, text: str, context: dict) -> dict:
        """Handle weather-related voice commands."""
        lang = context.get("_lang", "en")

        # Fetch weather data (simplified for this example)
        temperature = 12
        condition = "cloudy"

        return {
            "tts_text": self.t("current_weather", lang=lang,
                               city="Kyiv", temp=temperature,
                               sign="+", unit="C",
                               condition=condition,
                               emoji="cloudy",
                               fl_sign="+", feels_like=9,
                               humidity=78, wind=5),
            "data": {
                "temperature": temperature,
                "condition": condition,
            },
        }

    @on_event("device.state_changed")
    async def on_device_changed(self, data: dict) -> None:
        """React to device state changes."""
        self._log.info("Device changed: %s", data.get("device_id"))

    @scheduled("every:10m")
    async def refresh_cache(self) -> None:
        """Periodically refresh cached weather data."""
        self._log.debug("Cache refreshed")

    async def on_stop(self) -> None:
        """Clean up on shutdown."""
        self._log.info("Weather module stopping")


if __name__ == "__main__":
    module = WeatherModule()
    asyncio.run(module.start())
```

---

## API Access from Modules

Modules access the core API exclusively through the bus proxy (not direct HTTP). All requests are subject to ACL enforcement based on manifest permissions.

```python
# List all devices (requires devices.read permission)
devices = await self.api_request("GET", "/devices")

# Get a specific device
device = await self.api_request("GET", f"/devices/{device_id}")

# Update device state (requires devices.write permission)
await self.api_request("PATCH", f"/devices/{device_id}/state", {
    "state": {"power": True}
})

# Publish an event (requires events.publish permission)
await self.api_request("POST", "/events/publish", {
    "type": "my.custom_event",
    "source": self.name,
    "payload": {}
})
```

---

## Entry Point

Every module must include this block at the bottom of `main.py`:

```python
if __name__ == "__main__":
    module = MyModule()
    asyncio.run(module.start())
```

The `start()` method (inherited from `SmartHomeModule`) handles the full lifecycle: reading environment variables, connecting to the bus, authenticating, registering capabilities, and entering the event loop.

---

## Testing

Use `mock_core.py` to test modules locally without a running SelenaCore instance. It provides a fake bus endpoint that simulates the core's WebSocket server:

```bash
# Terminal 1: Start the mock core
python mock_core.py

# Terminal 2: Run your module
SELENA_BUS_URL=ws://localhost/api/v1/bus \
MODULE_TOKEN=test-token \
MODULE_DIR=./my-module \
python my-module/main.py
```

The mock core accepts connections, responds to API requests with stub data, and logs all events your module publishes.

---

## Packaging and Installation

### Creating a Package

Bundle your module directory into a ZIP file:

```bash
cd my-module/
zip -r ../my-module-1.0.0.zip manifest.json main.py locales/ widget.html settings.html icon.svg
```

Ensure `manifest.json` is at the root of the ZIP archive, not nested inside a subdirectory.

### Installing a Module

Upload the ZIP file to a running SelenaCore instance:

```bash
curl -X POST http://localhost/api/v1/modules/install \
  -F "file=@my-module-1.0.0.zip"
```

The core will:
1. Validate the manifest.
2. Extract the module files.
3. Build a Docker container with the specified resource limits.
4. Start the module and establish the bus connection.

---

## Common Patterns

### Responding to a Voice Command and Updating a Device

```python
@intent(r"turn on|увімкни", order=60)
async def handle_turn_on(self, text: str, context: dict) -> dict:
    lang = context.get("_lang", "en")
    device_id = self._parse_device(text)

    await self.api_request("PATCH", f"/devices/{device_id}/state", {
        "state": {"power": True}
    })

    return {"tts_text": self.t("device_on", lang=lang, device=device_id)}
```

### Reacting to Events and Publishing New Ones

```python
@on_event("sensor.temperature_changed")
async def on_temp_change(self, data: dict) -> None:
    temp = data.get("value", 0)
    if temp > 30:
        await self.publish_event("automation.alert", {
            "message": f"High temperature: {temp}C",
            "severity": "warning",
        })
```

### Periodic Data Fetch with Error Handling

```python
@scheduled("every:5m")
async def poll_external_api(self) -> None:
    try:
        result = await self._fetch_data()
        await self.publish_event("integration.data_updated", result)
    except Exception as exc:
        self._log.error("Failed to poll API: %s", exc)
```

---

## Quick Reference Checklist

1. Create `manifest.json` with a unique `name`, correct `type`, and minimal `permissions`.
2. Create `main.py` with a class inheriting from `SmartHomeModule`.
3. Set `name` and `version` class attributes matching the manifest.
4. Implement `on_start` for initialization.
5. Add intent handlers, event listeners, and scheduled tasks as needed.
6. Create locale files in `locales/` for all supported languages.
7. Add the `if __name__ == "__main__"` entry point.
8. Test locally with `mock_core.py`.
9. Package as a ZIP and install via `POST /api/v1/modules/install`.
