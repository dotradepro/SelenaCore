# docs/module-core-protocol.md — Module-Core Interaction Protocol

**Version:** 2.0 (Module Bus)
**Status:** Normative document — implement strictly according to it
**Scope:** `core/module_bus.py`, `core/api/routes/bus.py`, `sdk/base_module.py`

---

## Overview

Modules and the core communicate through the **WebSocket Module Bus** (`ws://core:7070/api/v1/bus`). Each module is a WebSocket client, the core is the server (master node, CAN-bus architecture). Direct module access to SQLite, the `/secure/` filesystem, or other modules is prohibited.

```
MODULE (Docker bridge)                CORE (:7070, host network)
   │                                      │
   │──── WebSocket + token ─────────────► │  ws://core:7070/api/v1/bus?token=XXX
   │                                      │
   │──── announce ────────────────────► │  capabilities registration
   │◄─── announce_ack ────────────────── │  confirmation + bus_id
   │                                      │
   │◄─── intent ──────────────────────── │  voice command
   │──── intent_response ─────────────► │  processing result
   │                                      │
   │◄─── event ───────────────────────── │  EventBus events
   │──── event ───────────────────────► │  event publishing
   │                                      │
   │◄─── ping ────────────────────────── │  health check (15s)
   │──── pong ────────────────────────► │
   │                                      │
   │──── api_request ─────────────────► │  Core API proxy
   │◄─── api_response ───────────────── │
   │                                      │
   │◄─── shutdown ────────────────────── │  graceful shutdown (drain)
```

---

## Module Bus — WebSocket Protocol

### Connection Lifecycle

```
1. Module connects: ws://core:7070/api/v1/bus?token=MODULE_TOKEN
2. Token validated BEFORE WebSocket accept()
3. Module sends: {"type": "announce", "module": "name", "capabilities": {...}}
4. Core responds: {"type": "announce_ack", "status": "ok", "bus_id": "uuid", "warnings": [...]}
5. Message loop (bidirectional JSON messages)
6. On disconnect: core cleans up, module reconnects with exponential backoff
```

### Message Types

#### announce (module → core)
```json
{
  "type": "announce",
  "module": "weather-module",
  "capabilities": {
    "intents": [
      {
        "patterns": {"en": ["weather", "forecast"], "uk": ["погода", "прогноз"]},
        "priority": 50,
        "description": "Weather queries"
      }
    ],
    "subscriptions": ["device.state_changed", "device.*"],
    "publishes": ["weather.module_started"]
  }
}
```

#### intent (core → module)
```json
{"type": "intent", "id": "req-uuid", "payload": {"text": "яка погода?", "lang": "uk", "context": {"user_id": "u1"}}}
```

#### intent_response (module → core)
```json
{"type": "intent_response", "id": "req-uuid", "payload": {"handled": true, "tts_text": "Зараз +12°С", "data": {...}}}
```

#### event (bidirectional)
```json
{"type": "event", "payload": {"event_type": "device.state_changed", "data": {"device_id": "d1", "state": {...}}}}
```

Core enriches events with `event_id`, `source`, `timestamp` when delivering to subscribers.

#### ping/pong (core → module → core)
```json
{"type": "ping", "ts": 1711800000.0}
{"type": "pong", "ts": 1711800000.0}
```

Core disconnects after 3 missed pongs (45s).

#### api_request / api_response (bidirectional)
```json
{"type": "api_request", "id": "req-uuid", "method": "GET", "path": "/devices", "body": null}
{"type": "api_response", "id": "req-uuid", "status": 200, "body": {"devices": [...]}}
```

Module→core: proxy to Core API with ACL check.
Core→module: UI proxy forwarding requests to module.

#### re_announce (module → core)
```json
{"type": "re_announce", "capabilities": {...}}
```

Hot-reload capabilities without reconnect.

#### shutdown (core → module)
```json
{"type": "shutdown", "reason": "core_restart", "drain_ms": 5000}
```

Module should save state and prepare for disconnect.

### Intent Routing

1. Core maintains a sorted intent index from all connected modules
2. Priority: 0-29 system, 30-49 core, 50-99 user (lower = higher priority)
3. Fallthrough: max 3 modules tried per intent
4. Circuit breaker: 10s timeout → 30s open → half-open probe
5. Concurrency: max 50 simultaneous intent requests

### API ACL Table

| Permission | Allowed Methods |
|------------|----------------|
| `devices.read` | GET /devices, GET /devices/* |
| `devices.control` | POST /devices/*/control |
| `secrets.read` | GET /secrets, GET /secrets/* |
| `modules.list` | GET /modules |

### Reconnection

- Exponential backoff: 1s, 2s, 4s, ... max 60s
- Random jitter: 0-30% of delay
- Fatal close codes (no reconnect): `invalid_token`, `permission_denied`
- Shutdown-aware: stops reconnecting when core sends shutdown

### Dual Queue Architecture

Core maintains two queues per connection:
- **Critical queue** (backpressure, maxsize=100): intents, pings, shutdown, API responses
- **Event queue** (drop-oldest, maxsize=1000): event delivery

Critical messages always have priority over events.

---

> **LEGACY PROTOCOL (deprecated):** The sections below describe the old HTTP-based protocol.
> New modules should use the WebSocket Module Bus described above.
> HTTP intent registration (`POST /api/v1/intents/register`) and webhook event delivery are deprecated.

---

---

## 1. Module Lifecycle and Token Issuance

### 1.1 Full Installation Cycle

```
User uploads ZIP
        │
        ▼
POST /api/v1/modules/install (multipart/form-data, file=module.zip)
        │
        ▼
ModuleLoader.install():
  1. Extract ZIP → /var/lib/selena/modules/<name>/
  2. Validator.validate(manifest.json)
     → name, version, port, permissions — strict validation
     → on error: 422 + description
  3. SandboxRunner.test()
     → docker run --rm smarthome-sandbox ...
     → timeout 60s
     → on failure: 400 + sandbox_output
  4. Generate module_token and save:
       token_file = /secure/module_tokens/<name>.token
       Path(token_file).write_text(token)  # plaintext, chmod 600
       webhook_secret = secrets.token_hex(32) # stored in module memory via env
  5. Create env file for the container:
       /var/lib/selena/modules/<name>/.env.module
       (contents — see section 1.3)
  6. Start container via DockerSandbox (see section 1.4)
  7. Wait for GET /health → 200 (timeout 30s)
  8. SDK inside the module automatically subscribes to events
     from manifest.json during on_start (see section 3.3)
  9. Return response:
       201 { "name": "...", "status": "RUNNING", "port": 8100 }
     ⚠️ token is NOT returned in the response — it is already inside the container
```

### 1.2 Token Storage

The token is stored as a plaintext file `/secure/module_tokens/<name>.token` (chmod 600).
When verifying a request, the core reads all `*.token` files from this directory and compares them directly with the presented token.
`DEV_MODULE_TOKEN` from `.env` is accepted as an additional valid token in dev mode (`DEBUG=true`).

> **Security note:** In production, the token storage `/secure/` should be mounted with `700` permissions and accessible only to the core user.

### 1.3 `.env.module` File — Passing Secrets to the Container

The core creates this file **before** starting the container. The file is mounted as `--env-file`. After the container starts — the file is **deleted** from disk (secrets live only in process memory).

```bash
# /var/lib/selena/modules/<name>/.env.module
# Created by the core during installation. Deleted immediately after docker run.

SELENA_MODULE_TOKEN=<raw_token_64_chars>
SELENA_WEBHOOK_SECRET=<webhook_secret_64_hex_chars>
SELENA_CORE_URL=http://localhost:7070/api/v1
SELENA_MODULE_NAME=<name>
SELENA_MODULE_PORT=<port>
```

**Why it is deleted:** a file on disk is a potential leak. After being passed via `--env-file`, the variables live only in `/proc/<pid>/environ` of the container, which is inaccessible from outside.

**Deletion implementation:**
```python
# core/module_loader/sandbox.py
import os, subprocess, tempfile

env_path = f"{install_path}/.env.module"
try:
    _write_env_file(env_path, token, webhook_secret, ...)
    proc = subprocess.run([
        "docker", "run", "--env-file", env_path, ...
    ])
finally:
    os.unlink(env_path)   # delete immediately after docker run call
```

### 1.4 Docker run — Startup Parameters

```python
# core/module_loader/sandbox.py

subprocess.run([
    "docker", "run",
    "--detach",
    "--name",        f"selena-module-{module.name}",
    "--network",     "selena_selena_internal",  # Docker Compose network selena_modules
    "--hostname",    module.name,
    "--publish",     f"127.0.0.1:{module.port}:{module.port}",  # localhost only
    "--env-file",    env_path,              # SELENA_* variables
    "--memory",      f"{manifest.resources.memory_mb}m",
    "--cpus",        str(manifest.resources.cpu),
    "--pids-limit",  "100",
    "--read-only",                          # readonly rootfs
    "--tmpfs",       "/tmp:size=32m",       # only /tmp writable
    "--cap-drop",    "ALL",
    "--security-opt","no-new-privileges:true",
    "--restart",     restart_policy,        # always / no / on-failure
    "--label",       f"selena.module={module.name}",
    "--label",       f"selena.port={module.port}",
    f"selena-module-{module.name}:latest",  # image tag
])
```

`restart_policy`:
- `always_on` → `"always"`
- `on_demand` → `"no"`
- `scheduled` → `"no"` (core manages startup itself)

### 1.5 Lifecycle on Container Restart

If the container restarts (crash, OOM, `always` policy):

```
Container restarts
        │
        ▼
SDK.on_start() is called again
        │
        ▼
SDK reads SELENA_MODULE_TOKEN from env (env lives in memory, not on disk)
        │
        ▼
SDK automatically resubscribes to all events from manifest.json
(subscriptions are stored in Event Bus in memory → on core restart —
 resubscription also happens, see section 3.4)
        │
        ▼
Module continues operation
```

**Token does not change on restart** — it is stored in `.env.module` until deletion, then lives in the container's `environ`. On Docker container restart, env variables are preserved (Docker stores them in the container layer, not in a file). The token remains valid until module uninstallation.

---

## 2. Request Authentication: Module → Core

### 2.1 Bearer Token Scheme

Every HTTP request from a module to the Core API must contain the header:

```
Authorization: Bearer <module_token>
```

**Verification on the core side (`core/api/auth.py`):**

```python
# core/api/auth.py

import os
from pathlib import Path
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer = HTTPBearer(auto_error=False)


def _load_valid_tokens() -> set[str]:
    """Load valid tokens from /secure/module_tokens/ directory."""
    tokens: set[str] = set()
    tokens_dir = Path(os.environ.get("CORE_SECURE_DIR", "/secure")) / "module_tokens"
    if tokens_dir.exists():
        for token_file in tokens_dir.glob("*.token"):
            token = token_file.read_text().strip()
            if token:
                tokens.add(token)
    dev_token = os.environ.get("DEV_MODULE_TOKEN", "")
    if dev_token:
        tokens.add(dev_token)
    return tokens


async def verify_module_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
) -> str:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = credentials.credentials
    valid_tokens = _load_valid_tokens()
    if token not in valid_tokens:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token
```

> **Note:** In the current implementation, permissions from manifest.json are not checked
> at the individual endpoint level — any valid token has full API access.
> Granular permission checks are planned for v1.1.

**Usage in routers:**

```python
# core/api/routes/devices.py

@router.get("/devices")
async def list_devices(
    token: str = Depends(verify_module_token)
):
    ...

@router.post("/devices")
async def register_device(
    body: DeviceCreate,
    token: str = Depends(verify_module_token)
):
    ...
```

### 2.2 Table: Endpoints by Module Type

> **Note:** In the current implementation (v1.0), module type is not checked at the endpoint level.
> Any valid token has access to all Public API endpoints.
> The table below reflects the **planned** architecture for v1.1.

| Endpoint | INTEGRATION | DRIVER | AUTOMATION | SYSTEM |
|---|:---:|:---:|:---:|:---:|
| `GET /health` | ✅ | ✅ | ✅ | ✅ |
| `GET /devices` | ✅ | ✅ | ✅ | ✅ |
| `POST /devices` | ✅ | ✅ | — | ✅ |
| `PATCH /devices/{id}/state` | ✅ | ✅ | — | ✅ |
| `DELETE /devices/{id}` | — | ✅ | — | ✅ |
| `POST /events/publish` | ✅ | ✅ | ✅ | ✅ |
| `POST /events/subscribe` | ✅ | ✅ | ✅ | ✅ |
| `POST /secrets/oauth/start` | ✅ | — | — | ✅ |
| `POST /secrets/proxy` | ✅ | — | — | ✅ |
| `GET /modules` | — | — | — | ✅ |
| `POST /modules/install` | — | — | — | ✅ |
| `POST /modules/{name}/stop` | — | — | — | ✅ (not SYSTEM) |
| `GET /system/info` | — | — | — | ✅ |
| `GET /integrity/status` | — | — | — | ✅ |

### 2.3 Rate limiting

```python
# core/api/middleware.py
# Sliding window per-IP

LIMIT_LOCAL    = 600   # req/min for localhost and LAN (192.168.x, 10.x, 127.x)
LIMIT_EXTERNAL = 120   # req/min for external IPs
WINDOW_SEC     = 60

# SSE streams and static files — not counted
# On exceeding: 429 Too Many Requests
# Header: Retry-After: <window_sec>
```

### 2.4 Token Rotation (Uninstallation)

The token is invalidated **only** upon module uninstallation:

```
DELETE /api/v1/modules/<name>   (only SYSTEM module or UI)
        │
        ▼
1. Docker stop selena-module-<name>
2. Docker rm selena-module-<name>
3. UPDATE modules SET status='REMOVED' WHERE name=<name>
   (token_hash remains in DB for audit, but status REMOVED → 401 on verification)
4. Delete /var/lib/selena/modules/<name>/
5. Event Bus: unsubscribe all subscriptions for this module
```

Token rotation without uninstallation is not supported. If a token is compromised — the only option is uninstallation and reinstallation.

---

## 3. Event Delivery: Core → Module (Event Bus)

### 3.1 Event Bus Scheme

```
Event source                      Event Bus                  Subscribers
      │                          (asyncio.Queue)                   │
      │                                │                           │
PATCH /devices/{id}/state ──────► bus.publish(event) ────► delivery_worker
POST  /events/publish     ──────►       │                         │
                                        │              ┌──────────┘
                                        │              │
                                        ▼              ▼
                              wildcard filter        POST http://localhost:810X/webhook/events
                                                    X-Selena-Signature: sha256=<hmac>
                                                    Content-Type: application/json
```

### 3.2 Event Format

```python
# Event structure (TypedDict)

class SelenaEvent(TypedDict):
    id:         str        # UUID, unique for deduplication
    type:       str        # "device.state_changed", "climate.updated", etc.
    source:     str        # publisher module name or "core"
    timestamp:  str        # ISO 8601, UTC
    payload:    dict       # arbitrary data


# Example:
{
    "id":        "550e8400-e29b-41d4-a716-446655440000",
    "type":      "device.state_changed",
    "source":    "core",
    "timestamp": "2026-03-21T14:32:00.123Z",
    "payload": {
        "device_id":  "dev_abc123",
        "old_state":  {"temperature": 21.0},
        "new_state":  {"temperature": 22.4},
        "changed_by": "climate-control"
    }
}
```

### 3.3 Event Subscription

**Via API (module subscribes manually):**

```python
# Module calls on startup:
POST /api/v1/events/subscribe
Authorization: Bearer <module_token>

{
    "event_types": ["device.state_changed", "device.offline"],
    "webhook_url": "http://localhost:8100/webhook/events"
    # wildcard: "device.*" — all events with prefix device.
}
```

**Response:**
```json
{
    "subscription_id": "sub_xyz",
    "event_types": ["device.state_changed", "device.offline"],
    "webhook_url": "http://localhost:8100/webhook/events"
}
```

**Subscription storage in memory (Event Bus):**

```python
# core/eventbus/bus.py

class EventBus:
    # Subscriptions are stored ONLY in memory.
    # On core restart — all modules resubscribe themselves (see 3.4)
    _subscriptions: dict[str, list[Subscription]] = {}
    # key: event_type or wildcard pattern
    # value: list of Subscription(module_name, webhook_url, webhook_secret)
```

**Prohibition of `core.*` publishing from modules:**

```python
@router.post("/events/publish")
async def publish_event(
    body: EventPublish,
    module = Depends(require_permission("events.publish"))
):
    if body.event_type.startswith("core."):
        raise HTTPException(status_code=403,
            detail="Modules cannot publish core.* events")
    await bus.publish(SelenaEvent(
        id=str(uuid4()),
        type=body.event_type,
        source=module.name,
        timestamp=datetime.utcnow().isoformat() + "Z",
        payload=body.payload
    ))
    return {"published": True}
```

### 3.4 Resubscription on Core Restart

Since subscriptions are stored only in memory, on core restart all modules lose their subscriptions. Recovery mechanism:

**Core on startup:**
```python
# core/main.py → startup event

async def on_startup():
    # 1. Start all modules with RUNNING status in DB
    running_modules = await db.fetch(
        "SELECT * FROM modules WHERE status='RUNNING' AND runtime_mode='always_on'"
    )
    for mod in running_modules:
        await module_loader.restart_container(mod)
    # Containers will call on_start themselves → resubscribe
```

**SDK on module startup:**
```python
# sdk/base_module.py → SmartHomeModule.start()

async def start(self):
    # Called on container startup (FastAPI startup event)
    self._token = os.environ["SELENA_MODULE_TOKEN"]
    self._webhook_secret = os.environ["SELENA_WEBHOOK_SECRET"]
    self._core_url = os.environ["SELENA_CORE_URL"]

    # Resubscribe to all events from manifest.json
    # (@on_event decorators collect the list during class import)
    await self._resubscribe_all()

    # Call user-defined on_start
    await self.on_start()


async def _resubscribe_all(self):
    """Registers webhook for all @on_event handlers."""
    event_types = list(self._event_handlers.keys())
    if not event_types:
        return
    webhook_url = f"http://localhost:{self._port}/webhook/events"
    await self._post("/events/subscribe", {
        "event_types": event_types,
        "webhook_url": webhook_url
    })
```

### 3.5 Webhook Delivery and HMAC Verification

**Core sends:**

```python
# core/eventbus/delivery.py

import hmac, hashlib, json, httpx

async def deliver(subscription: Subscription, event: SelenaEvent):
    body = json.dumps(event, ensure_ascii=False).encode()
    signature = "sha256=" + hmac.new(
        subscription.webhook_secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                subscription.webhook_url,
                content=body,
                headers={
                    "Content-Type":      "application/json",
                    "X-Selena-Signature": signature,
                    "X-Event-Id":        event["id"],
                    "X-Event-Type":      event["type"],
                }
            )
        if resp.status_code not in (200, 204):
            logger.warning(f"Webhook delivery failed: {resp.status_code}")
    except httpx.TimeoutException:
        logger.error(f"Webhook timeout for {subscription.webhook_url}")
    # Retry is not provided — module must be idempotent
```

**Module verifies (SDK does this automatically):**

```python
# sdk/base_module.py — webhook endpoint is registered automatically

@app.post("/webhook/events")
async def _handle_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Selena-Signature", "")

    expected = "sha256=" + hmac.new(
        self._webhook_secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = json.loads(body)
    event_type = event["type"]

    # Dispatch to handlers
    handler = self._event_handlers.get(event_type)
    if handler is None:
        # Try wildcard
        for pattern, h in self._event_handlers.items():
            if pattern.endswith(".*") and event_type.startswith(pattern[:-2]):
                handler = h
                break

    if handler:
        await handler(self, event["payload"])

    return {"ok": True}
```

**`@on_event` decorator — handler registration:**

```python
# sdk/base_module.py

def on_event(event_type: str):
    """Decorator. Registers a method as an event handler."""
    def decorator(func):
        func._on_event = event_type   # label on the function
        return func
    return decorator


class SmartHomeModuleMeta(type):
    """Metaclass that collects all @on_event handlers during class creation."""
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        cls._event_handlers: dict[str, Callable] = {}
        for attr_name, attr in namespace.items():
            if callable(attr) and hasattr(attr, "_on_event"):
                cls._event_handlers[attr._on_event] = attr
        return cls


class SmartHomeModule(metaclass=SmartHomeModuleMeta):
    ...
```

---

## 4. UI Widgets and settings.html

### 4.1 How UI Core Loads a Widget

UI Core (:80) renders the main screen. For each module with `ui_profile != HEADLESS`:

```
UI Core gets the module list:
GET http://localhost:7070/api/v1/modules
→ [ { name, port, manifest.ui.widget.size, status, ... } ]

For each module with status RUNNING:
  Creates <iframe src="http://localhost:{port}/widget.html"
                  sandbox="allow-scripts allow-same-origin"
                  scrolling="no">

  iframe size is determined by manifest.ui.widget.size:
    "1x1" → 1 cell × 1 grid row
    "2x1" → 2 cells × 1 row
    "2x2" → 2 cells × 2 rows
    "4x1" → full width × 1 row
    "1x2" → 1 cell × 2 rows
```

### 4.2 Endpoints That Every Module Must Serve

```
GET  /health          → {"status": "ok", "name": "<name>", "version": "..."}
GET  /widget.html     → Widget HTML file (manifest.ui.widget.file)
GET  /settings.html   → Settings HTML file (manifest.ui.settings)
GET  /icon.svg        → SVG icon (manifest.ui.icon)
```

SDK registers these routes automatically on startup:

```python
# sdk/base_module.py → register_static_routes()

def register_static_routes(self, app: FastAPI):
    install_path = Path(os.environ.get("SELENA_INSTALL_PATH", "."))

    @app.get("/health")
    async def health():
        return {"status": "ok", "name": self.name, "version": self.version}

    @app.get("/widget.html", response_class=HTMLResponse)
    async def widget():
        path = install_path / self._manifest["ui"]["widget"]["file"]
        return path.read_text()

    @app.get("/settings.html", response_class=HTMLResponse)
    async def settings():
        path = install_path / self._manifest["ui"]["settings"]
        return path.read_text()

    @app.get("/icon.svg", response_class=Response)
    async def icon():
        path = install_path / self._manifest["ui"]["icon"]
        return Response(content=path.read_bytes(), media_type="image/svg+xml")
```

### 4.3 Authentication for Requests from widget.html

The widget runs in a browser iframe. For requests to the Core API from the widget:

```javascript
// The core passes a read-only UI token to widget.html via query parameter on load:
// GET /widget.html?ui_token=<ui_token>

// UI token — a separate token with limited permissions:
//   only: device.read, events.subscribe (read-only)
//   issued by UI Core on page load, TTL = 1 hour
//   is NOT a module_token

// widget.html receives it:
const params = new URLSearchParams(window.location.search)
const uiToken = params.get('ui_token')

// Requests to Core API from the widget:
const resp = await fetch('http://localhost:7070/api/v1/devices', {
    headers: { 'Authorization': `Bearer ${uiToken}` }
})
```

**UI token issuance — UI Core:**

```python
# core/system_modules/ui_core/routes.py

@router.get("/widget-frame/{module_name}")
async def widget_frame(module_name: str, user = Depends(require_user)):
    module = await module_loader.get(module_name)
    if not module or module.status != "RUNNING":
        raise HTTPException(404)

    # Generate a short-lived UI token
    ui_token = await token_service.create_ui_token(
        scope=["device.read"],
        ttl_seconds=3600,
        issued_for=f"widget:{module_name}"
    )

    widget_url = f"http://localhost:{module.port}/widget.html?ui_token={ui_token}"
    # Return iframe src
    return {"iframe_src": widget_url}
```

### 4.4 settings.html — Settings Save Mechanism

```
User opens module settings in the UI:
  → GET http://localhost:{port}/settings.html?ui_token=<ui_token>

Settings are saved via Core API (not directly to a file!):
  POST /api/v1/modules/{name}/config
  Authorization: Bearer <ui_token>
  { "key": "temperature_unit", "value": "celsius" }

Module reads its settings:
  GET /api/v1/modules/{name}/config
  Authorization: Bearer <module_token>
```

**Module settings storage in SQLite:**

```sql
CREATE TABLE module_config (
    module_name  TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,   -- JSON-serialized value
    updated_at   DATETIME NOT NULL,
    PRIMARY KEY (module_name, key)
);
```

---

## 5. Secrets Vault and OAuth Proxy

### 5.1 OAuth Request (INTEGRATION only + permission `secrets.oauth`)

```
Step 1: Module initiates OAuth flow

POST /api/v1/secrets/oauth/start
Authorization: Bearer <module_token>
{
    "provider": "google",
    "scopes": ["gmail.readonly", "gmail.send"]
}

Response:
{
    "device_code":  "AH-1Bx...",
    "user_code":    "ABCD-EFGH",
    "verification_url": "https://accounts.google.com/device",
    "expires_in":   1800,
    "qr_data_url":  "data:image/png;base64,..."   # QR code for UI
}

Step 2: UI Core shows QR code to the user

Step 3: Core polls OAuth provider (background task)
        Upon receiving token:
          → encrypt with AES-256-GCM
          → save to /secure/tokens/<module_name>/google.enc
          → Event Bus: publish "core.oauth.completed" { module, provider }

Step 4: Module receives "core.oauth.completed" event
        (SYSTEM modules can subscribe to core.* events)
        Regular INTEGRATION modules — receive via:
          GET /api/v1/secrets/oauth/status?provider=google
          → { "status": "completed" | "pending" | "expired" }
```

### 5.2 API Proxy (INTEGRATION only + permission `secrets.proxy`)

```python
# Module makes request through the core — token NEVER leaves the core

POST /api/v1/secrets/proxy
Authorization: Bearer <module_token>
{
    "provider": "google",
    "url":      "https://gmail.googleapis.com/gmail/v1/users/me/messages",
    "method":   "GET",
    "headers":  { "Accept": "application/json" },  # optional
    "body":     null                                # optional
}

# Core:
# 1. Validates url: only https://, blocks private IP
# 2. Decrypts token from /secure/tokens/<module>/google.enc
# 3. Adds Authorization: Bearer <decrypted_token> to the request
# 4. Executes request with follow_redirects=False
# 5. Returns provider response:

{
    "status_code": 200,
    "headers": { "Content-Type": "application/json" },
    "body": { "messages": [...] }
}
```

**SSRF protection:**

```python
# core/system_modules/secrets_vault/proxy.py

import ipaddress, re
from urllib.parse import urlparse

BLOCKED_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
]

def validate_proxy_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Only https:// URLs allowed")
    host = parsed.hostname
    try:
        addr = ipaddress.ip_address(host)
        for net in BLOCKED_RANGES:
            if addr in net:
                raise ValueError(f"Private IP blocked: {host}")
    except ValueError:
        pass  # hostname — resolved later, additional check during request
```

---

## 6. Cloud Sync — Interaction with the SmartHome LK Platform

### 6.1 Heartbeat

```
Every 60 seconds:

POST https://smarthome-lk.com/api/v1/device/heartbeat
Headers:
  X-Device-Hash:  <PLATFORM_DEVICE_HASH from .env>
  X-Signature:    sha256=<hmac>
  Content-Type:   application/json

Body:
{
    "timestamp":  "2026-03-21T14:00:00Z",
    "status":     "online",
    "uptime":     86400,
    "modules": [
        { "name": "climate-control", "status": "RUNNING", "version": "1.2.1" }
    ],
    "integrity": {
        "status": "ok",
        "last_check": "2026-03-21T13:59:30Z",
        "files_checked": 847
    },
    "hardware": {
        "cpu_percent":  23.0,
        "ram_mb_used":  2100,
        "cpu_temp_c":   48.0
    }
}

HMAC is computed:
  key     = contents of /secure/platform.key (AES-256-GCM key, read and decrypted)
  message = json_body + "." + timestamp   (timestamp from request header)
  sig     = hmac-sha256(key, message)
```

### 6.2 Long-poll Commands

```
GET https://smarthome-lk.com/api/v1/device/commands
    ?device_hash=<hash>
    &wait=30
Headers:
  X-Signature: sha256=<hmac>

# Platform holds the connection for up to 30 sec or until there are commands

Response when a command is available:
{
    "command_id": "cmd_abc123",
    "type":       "INSTALL_MODULE",   # or STOP_MODULE, REBOOT, SYNC_STATE, FACTORY_RESET
    "payload":    { ... }
}

After executing the command:
POST https://smarthome-lk.com/api/v1/device/commands/{command_id}/ack
{
    "success":   true,
    "error_msg": null
}
```

**Command handling:**

```python
# core/cloud_sync/command_handler.py

COMMAND_HANDLERS = {
    "INSTALL_MODULE": handle_install_module,    # payload: { zip_url, name }
    "STOP_MODULE":    handle_stop_module,       # payload: { name }
    "REBOOT":         handle_reboot,            # payload: {}
    "SYNC_STATE":     handle_sync_state,        # payload: {} → send full status
    "FACTORY_RESET":  handle_factory_reset,     # payload: { confirm_token }
}
```

### 6.3 Retry Policy

```python
# Exponential backoff when platform is unavailable

delay = min(2 ** attempt, 300)  # maximum 5 minutes
# attempt: 0→1s, 1→2s, 2→4s, ..., 8→256s, 9+→300s

# When OFFLINE: core continues working fully locally
# Platform unavailable — not critical for local functionality
```

---

## 7. Integrity Agent — Interaction with Core

### 7.1 Process Independence

```
smarthome-agent.service (systemd)
  ↓
agent/integrity_agent.py
  ↓
NEVER does: import core.*
NEVER does: from core import ...

Interaction ONLY through:
  1. Filesystem (/secure/, /var/lib/selena/)
  2. Docker CLI (subprocess)
  3. HTTP request to :7070 (for notify and status)
```

### 7.2 Verification Algorithm

```python
# agent/integrity_agent.py

async def check_once() -> IntegrityResult:
    # 1. Read master.hash
    master_hash = Path("/secure/master.hash").read_text().strip()

    # 2. Compute SHA256 of core.manifest
    manifest_bytes = Path("/secure/core.manifest").read_bytes()
    manifest_hash = sha256(manifest_bytes).hexdigest()

    if manifest_hash != master_hash:
        return IntegrityResult(status="MANIFEST_TAMPERED",
                               detail="core.manifest hash mismatch")

    # 3. Parse manifest (JSON: { "file_path": "expected_hash", ... })
    manifest = json.loads(manifest_bytes)
    violations = []

    for file_path, expected_hash in manifest.items():
        try:
            actual_hash = sha256(Path(file_path).read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                violations.append({"file": file_path,
                                    "expected": expected_hash,
                                    "actual": actual_hash})
        except FileNotFoundError:
            violations.append({"file": file_path, "error": "missing"})

    if violations:
        return IntegrityResult(status="VIOLATED", violations=violations)

    return IntegrityResult(status="OK", files_checked=len(manifest))
```

### 7.3 Reaction Chain on Violation

```python
# agent/responder.py

async def respond_to_violation(result: IntegrityResult):
    log.critical(f"INTEGRITY VIOLATION: {result}")

    # Step 1: Stop all modules via Docker CLI
    proc = subprocess.run(
        ["docker", "ps", "--filter", "label=selena.module", "-q"],
        capture_output=True, text=True
    )
    container_ids = proc.stdout.strip().split()
    for cid in container_ids:
        subprocess.run(["docker", "stop", "--time", "5", cid])

    # Step 2: Notify platform (not through core import!)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "http://localhost:7070/api/v1/integrity/violation",
                json={"violations": result.violations},
                headers={"X-Agent-Secret": _read_agent_secret()}
            )
    except Exception:
        pass  # core unavailable — continue rollback

    # Step 3: Attempt rollback (3 attempts with 5 sec pause)
    for attempt in range(3):
        success = await attempt_rollback()
        if success:
            log.info("Rollback successful, restarting core")
            subprocess.run(["systemctl", "restart", "smarthome-core"])
            return
        await asyncio.sleep(5)

    # Step 4: SAFE MODE — if rollback failed
    await enter_safe_mode()


async def attempt_rollback() -> bool:
    backup_dir = Path("/secure/core_backup")
    versions = sorted(backup_dir.iterdir(), reverse=True)
    if not versions:
        return False
    latest_backup = versions[0]
    # Copy files from backup over current ones
    # Recalculate core.manifest and master.hash
    ...


async def enter_safe_mode():
    # Write flag to file
    Path("/var/lib/selena/SAFE_MODE").write_text("1")
    # Core checks this file on startup → restricts API
    subprocess.run(["systemctl", "restart", "smarthome-core"])
```

### 7.4 `/api/v1/integrity/violation` — Core Endpoint for Agent

```python
# core/api/routes/integrity.py
# Protected by a separate agent secret (not module_token)

AGENT_SECRET = os.environ["INTEGRITY_AGENT_SECRET"]  # from .env

@router.post("/integrity/violation")
async def report_violation(
    body: ViolationReport,
    request: Request
):
    agent_secret = request.headers.get("X-Agent-Secret", "")
    if not hmac.compare_digest(agent_secret, AGENT_SECRET):
        raise HTTPException(status_code=403)

    # Activate SAFE MODE in core immediately
    core_state.safe_mode = True
    logger.critical(f"SAFE MODE activated by Integrity Agent: {body.violations}")
    return {"acknowledged": True}
```

**SAFE MODE in core:**

```python
# core/api/middleware.py

async def safe_mode_middleware(request: Request, call_next):
    if core_state.safe_mode:
        # Allow only GET requests and /health
        if request.method != "GET" and request.url.path != "/api/v1/health":
            return JSONResponse(
                status_code=503,
                content={"error": "SAFE_MODE",
                         "detail": "Core is in safe mode. Only read operations allowed."}
            )
    return await call_next(request)
```

---

## 8. Development Environment — Mock Core API

### 8.1 DEV_MODULE_TOKEN

In development mode (`smarthome dev`):

```bash
# .env
DEV_MODULE_TOKEN=test-module-token-xyz
MOCK_PLATFORM=true
```

Mock Core API accepts `DEV_MODULE_TOKEN` as a valid token with SYSTEM permissions. The module does not need to be installed — the token is passed manually.

### 8.2 Environment Variables in Dev Mode

```bash
# Developer sets manually when running module locally:
export SELENA_MODULE_TOKEN=test-module-token-xyz
export SELENA_WEBHOOK_SECRET=dev-webhook-secret-hex
export SELENA_CORE_URL=http://localhost:7070/api/v1
export SELENA_MODULE_NAME=my-module
export SELENA_MODULE_PORT=8100
export SELENA_INSTALL_PATH=.

python main.py
```

### 8.3 mock_core.py — What It Simulates

```python
# sdk/mock_core.py — minimal implementation for tests

# Accepts any Bearer token as valid
# Stores devices in-memory (dict)
# Event Bus: synchronous delivery in the same process
# Secrets: tokens are not encrypted, stored in-memory
# HMAC signatures: computed with SELENA_WEBHOOK_SECRET from env
```

---

## 9. Complete Environment Variables Reference

### Core .env

```bash
# Main
CORE_PORT=7070
UI_PORT=80
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
DEBUG=false

# Platform
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=                    # filled during registration
MOCK_PLATFORM=false                      # true = do not connect to platform

# Secrets (generate during installation)
INTEGRITY_AGENT_SECRET=<32 random bytes hex>  # for X-Agent-Secret header

# Dev
DEV_MODULE_TOKEN=test-module-token-xyz   # only when DEBUG=true
```

### .env.module (created by core, not manually edited)

```bash
SELENA_MODULE_TOKEN=<64 chars base64url>
SELENA_WEBHOOK_SECRET=<64 chars hex>
SELENA_CORE_URL=http://localhost:7070/api/v1
SELENA_MODULE_NAME=<name>
SELENA_MODULE_PORT=<8100-8200>
SELENA_INSTALL_PATH=/var/lib/selena/modules/<name>
```

---

## 10. Intent Protocol — Voice and Text Commands

### 10.1 Overview

The Intent System allows any module to receive voice and text commands from the user. Routing is handled by IntentRouter with 4 priority levels (see `AGENTS.md` §20).

```
Voice/text → IntentRouter → voice.intent event → module
```

### 10.2 Intent Registration (User Module)

**Endpoint:** `POST /api/v1/intents/register`

```http
POST /api/v1/intents/register
Authorization: Bearer <module_token>
Content-Type: application/json

{
  "module": "weather-module",
  "port": 8100,
  "intents": [
    {
      "patterns": {
        "en": ["weather", "forecast"],
        "uk": ["погода", "прогноз"]
      },
      "description": "Weather queries",
      "endpoint": "/api/intent"
    }
  ]
}
```

**Response 201:**
```json
{
  "registered": true,
  "module": "weather-module",
  "intent_count": 1
}
```

**SDK performs automatically** on module startup if `manifest.json` has an `intents` field or if the class has `@intent` decorators.

### 10.3 Intent Dispatch to Module

When IntentRouter (Tier 2) finds a match with a module's pattern, it sends:

```http
POST http://localhost:{port}{endpoint}
Content-Type: application/json

{
  "text": "what's the weather",
  "lang": "en",
  "context": {
    "user_id": null
  }
}
```

**Module response contract:**

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "handled": true,
  "tts_text": "Currently 22°C, clear sky",
  "data": {
    "temp": 22,
    "desc": "clear"
  }
}
```

| Field | Type | Required | Description |
|------|-----|-------------|----------|
| `handled` | `bool` | Yes | `true` = module handled the request |
| `tts_text` | `str` | No | Text for speech synthesis (TTS). Empty = no voice |
| `data` | `dict` | No | Arbitrary data |

If `handled: false` — IntentRouter continues searching (Tier 3 LLM).

### 10.4 Intent Deletion

When a module is removed, the core calls:

```http
DELETE /api/v1/intents/{module_name}
Authorization: Bearer <module_token>

Response: 204 No Content
```

### 10.5 List of Registered Intents

```http
GET /api/v1/intents
Authorization: Bearer <module_token>

Response 200:
{
  "modules": [
    {
      "module": "weather-module",
      "port": 8100,
      "intents": [
        {
          "patterns": {"en": ["weather"], "uk": ["погода"]},
          "description": "Weather queries",
          "endpoint": "/api/intent"
        }
      ]
    }
  ],
  "total": 1
}
```

### 10.6 voice.intent Event (EventBus)

After determining the intent, IntentRouter publishes a `voice.intent` event. System modules subscribe to it via `DirectSubscription` (without HTTP).

```json
{
  "event_id": "uuid",
  "type": "voice.intent",
  "source": "core.intent_router",
  "payload": {
    "intent": "media.play_genre",
    "response": "",
    "action": null,
    "params": {"genre": "jazz"},
    "source": "system_module",
    "user_id": null,
    "latency_ms": 2
  },
  "timestamp": 1711814400.0
}
```

### 10.7 System Modules — Direct Registration

System modules (type: SYSTEM) register intents **in code**, without HTTP:

```python
from system_modules.llm_engine.intent_router import get_intent_router, SystemIntentEntry

get_intent_router().register_system_intent(SystemIntentEntry(
    module="media-player",
    intent="media.play_radio",
    priority=5,
    patterns={
        "uk": [r"(?:увімкни|включи)\s+радіо"],
        "en": [r"(?:play|turn on)\s+radio"],
    },
))
```

System modules receive events via `self.subscribe(["voice.intent"], callback)` — the callback is called directly (asyncio.create_task), without HTTP/webhook.

---

## 11. Summary Table — Who Reads and Writes What

| Component | Reads | Writes | Prohibited |
|---|---|---|---|
| Module | `SELENA_*` env vars | — | `/secure/`, core SQLite, other modules |
| Core API | SQLite modules | SQLite modules | `/secure/` (only through Secrets Vault) |
| Secrets Vault | `/secure/tokens/<name>/` | `/secure/tokens/<name>/` | — |
| Integrity Agent | `/secure/core.manifest`, `/secure/master.hash` | `/var/lib/selena/SAFE_MODE` | `import core.*` |
| Cloud Sync | `/secure/platform.key` | — | — |
| Module Loader | `/var/lib/selena/modules/` | `/var/lib/selena/modules/`, `.env.module` (then deletes) | — |
| SDK (widget.html) | `ui_token` from URL query | — | `module_token`, `/secure/` |

---

## 11. Implementation Readiness Criteria

- [ ] `module_token` is generated during installation, stored as plaintext file `/secure/module_tokens/<name>.token` (chmod 600)
- [ ] `.env.module` is deleted from disk immediately after `docker run`
- [ ] `webhook_secret` is stored in SQLite in plaintext, never returned via API
- [ ] HMAC-SHA256 is verified on every incoming webhook in SDK
- [ ] `core.*` events are blocked with 403 when a module attempts to publish them
- [ ] On core restart, all `always_on` modules are restarted and resubscribed
- [ ] UI token is issued by UI Core, has TTL of 1 hour, permissions limited to `device.read`
- [ ] `GET /widget.html`, `/settings.html`, `/icon.svg`, `/health` are registered by SDK automatically
- [ ] SSRF protection: only `https://`, blocking private IP ranges
- [ ] Integrity Agent does not import `core.*`, uses only subprocess and HTTP
- [ ] SAFE MODE: only GET requests pass when `core_state.safe_mode = True`
- [ ] `/api/v1/integrity/violation` is protected by `INTEGRITY_AGENT_SECRET`, not `module_token`
