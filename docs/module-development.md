# Module Development for SelenaCore

## What is a Module

> **Note:** This guide covers **user modules** (types: UI, INTEGRATION, DRIVER, AUTOMATION) that run in Docker containers.
> **System modules** (type: SYSTEM) run in-process inside the core. They inherit from `SystemModule` (`core/module_loader/system_module.py`) and communicate with the core through direct Python calls, not HTTP. See `AGENTS.md` §17 for system module architecture.

A user module is an isolated microservice that runs in a Docker container and communicates with the core **only** through Core API (`http://localhost:7070/api/v1`).

A module can:
- Register devices in Device Registry
- Subscribe to Event Bus events via webhook
- Publish events (except `core.*`)
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
  manifest.json          ← required
  main.py                ← entry point
  requirements.txt       ← Python dependencies
  Dockerfile             ← how to run
  icon.svg               ← UI icon (if type: UI)
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
  "port": 8100,
  "permissions": [
    "device.read",
    "device.write",
    "events.subscribe",
    "events.publish"
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
| `port` | `8100`–`8200` | Module port |
| `permissions` | see below | List of permissions |

### Permissions

| Permission | Available for Types | Description |
|------------|-------------------|-------------|
| `device.read` | all | GET /devices |
| `device.write` | all | POST/PATCH/DELETE /devices |
| `events.subscribe` | all | Subscribe to events |
| `events.publish` | all | Publish events |
| `secrets.oauth` | INTEGRATION only | Start OAuth flow |
| `secrets.proxy` | INTEGRATION only | API proxy through vault |

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
from sdk.base_module import SmartHomeModule, on_event, scheduled

class MyModule(SmartHomeModule):
    name = "my-module"
    version = "1.0.0"

    # === Lifecycle ===

    async def on_start(self):
        """Called when the module starts."""
        self.logger.info("Module started")

    async def on_stop(self):
        """Called when the module stops."""
        pass

    # === Event handlers ===

    @on_event("device.state_changed")
    async def handle_state_changed(self, payload: dict):
        """Called on each device state change."""
        device_id = payload["device_id"]
        new_state = payload["new_state"]
        self.logger.debug(f"Device {device_id} → {new_state}")

    @on_event("device.offline")
    async def handle_offline(self, payload: dict):
        self.logger.warning(f"Device offline: {payload['device_id']}")

    # === Scheduled tasks ===

    @scheduled("every:5m")
    async def periodic_sync(self):
        """Runs every 5 minutes."""
        devices = await self.list_devices()
        for device in devices:
            await self._sync_device(device)

    @scheduled("cron:0 * * * *")
    async def hourly_report(self):
        """Runs every hour via cron."""
        pass

    # === Core API helpers ===

    async def _sync_device(self, device: dict):
        # Update state in Registry
        await self.update_device_state(
            device["device_id"],
            {"temperature": 22.5}
        )

        # Publish event
        await self.publish_event("climate.updated", {
            "device_id": device["device_id"],
            "temperature": 22.5
        })
```

### Available SmartHomeModule Methods

```python
# Devices
await self.list_devices()                         # all devices
await self.get_device(device_id)                  # specific device
await self.register_device(name, type, protocol,  # create device
                           capabilities, meta)
await self.update_device_state(device_id, state)  # update state
await self.delete_device(device_id)               # delete

# Events
await self.publish_event(event_type, payload)     # publish event
await self.subscribe_events(event_types,          # subscribe (webhook)
                            webhook_url)

# Properties
self.logger          # logging.Logger with module name
self.token           # module_token for Authorization header
self.core_url        # http://localhost:7070/api/v1
```

---

## Local Development

### Step 1 — Create Module

```bash
cd /your/workspace
smarthome new-module my-climate-module
# Creates: my-climate-module/manifest.json, main.py, Dockerfile, requirements.txt
```

### Step 2 — Run Mock Core API

```bash
smarthome dev
# Starts mock API on http://localhost:7070
# All endpoints work with in-memory storage
# Dev token: DEV_MODULE_TOKEN from .env (default "test-module-token-xyz")
```

### Step 3 — Develop Module

```python
# main.py
from sdk.base_module import SmartHomeModule, on_event
from fastapi import FastAPI

app = FastAPI()
module = MyClimateModule()

@app.on_event("startup")
async def startup():
    await module.on_start()

@app.get("/health")
async def health():
    return {"status": "ok"}
```

### Step 4 — Tests

```bash
smarthome test
# Runs pytest in mock Core API context
```

Test example:

```python
import pytest
from httpx import AsyncClient
from sdk.mock_core import app as mock_app

@pytest.fixture
async def core_client():
    async with AsyncClient(app=mock_app, base_url="http://test") as c:
        yield c

async def test_device_registration(core_client):
    resp = await core_client.post(
        "/api/v1/devices",
        headers={"Authorization": "Bearer test-module-token-xyz"},
        json={"name": "Test Sensor", "type": "sensor",
              "protocol": "mqtt", "capabilities": []}
    )
    assert resp.status_code == 201
```

### Step 5 — Install to SelenaCore

```bash
smarthome publish --core http://localhost:7070
# Builds ZIP, sends to POST /api/v1/modules/install
# Tracks status via SSE
```

---

## Webhooks from Event Bus

If your module subscribed to events, the core will send POST requests to your webhook URL.

```python
# Subscribe
await core_client.post("/api/v1/events/subscribe",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "event_types": ["device.state_changed", "device.offline"],
        "webhook_url": "http://localhost:8100/webhook/events"
    }
)
```

```python
# Webhook handler in module (FastAPI)
from fastapi import FastAPI, Request, HTTPException
import hmac
import hashlib

app = FastAPI()
WEBHOOK_SECRET = "..."  # received during registration

@app.post("/webhook/events")
async def handle_event(request: Request):
    # Verify HMAC-SHA256
    signature = request.headers.get("X-Selena-Signature", "")
    body = await request.body()
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401)

    event = await request.json()
    event_type = event["type"]
    payload = event["payload"]
    # ... handle event
    return {"ok": True}
```

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
await core_client.post("/api/v1/secrets/oauth/start",
    json={"module": "gmail-integration", "provider": "google",
          "scopes": ["gmail.readonly"]})

# Execute API request — core injects the token
resp = await core_client.post("/api/v1/secrets/proxy",
    json={
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
| `403 Forbidden` on `/events/publish` | Event type starts with `core.` | Rename the event type |
| `403 Forbidden` on `/modules/{name}/stop` | Attempting to stop a SYSTEM module | Not allowed |
| `422 Unprocessable Entity` on install | Error in manifest.json | Check required fields |
| `409 Conflict` on install | Module with this name already exists | DELETE first |
| Webhook not received | Invalid `webhook_url` or module not listening | Check port in manifest.json |
| `400 Bad Request` on proxy | URL is not https:// or private IP | Only public HTTPS endpoints |
