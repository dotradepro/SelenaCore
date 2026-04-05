# SelenaCore REST API Reference

**Base URL:** `http://localhost:7070/api/v1`

---

## Authentication

Most endpoints require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <module_token>
```

Tokens are stored on disk in `/secure/module_tokens/`. For development, set the `DEV_MODULE_TOKEN` environment variable.

## Rate Limiting

All authenticated endpoints are rate-limited to **120 requests per 60 seconds** per client. This is configurable via `RateLimitMiddleware`. Exceeding the limit returns `429 Too Many Requests`.

## Request Headers

| Header | Description |
|---|---|
| `Authorization` | `Bearer <token>` (required for most endpoints) |
| `X-Request-Id` | Auto-generated UUID per request (injected by `RequestIdMiddleware`) |

## Swagger UI

Interactive API docs are available at `/docs` when the `DEBUG=true` environment variable is set. Disabled in production.

---

## System Endpoints

### GET /health

Returns the current health status of the SelenaCore instance. **No authentication required.**

**Response 200:**

```json
{
    "status": "ok",
    "version": "0.3.142-beta+0644435",
    "mode": "normal",
    "uptime": 3600,
    "integrity": "ok"
}
```

| Field | Type | Values |
|---|---|---|
| `status` | string | `"ok"` |
| `mode` | string | `"normal"` or `"safe_mode"` |
| `uptime` | int | Seconds since startup |
| `integrity` | string | `"ok"` or `"violation"` |

---

### GET /system/info

Returns detailed system and hardware information. Requires authentication.

**Response 200:**

```json
{
    "initialized": true,
    "wizard_completed": true,
    "version": "0.3.142-beta+0644435",
    "hardware": {
        "model": "raspberrypi",
        "ram_total_mb": 8192,
        "has_hdmi": false,
        "has_camera": false
    },
    "audio": {
        "inputs": [],
        "outputs": []
    },
    "display_mode": "headless"
}
```

| Field | Type | Description |
|---|---|---|
| `initialized` | bool | Whether the core has completed first-run initialization |
| `wizard_completed` | bool | Whether the onboarding wizard has been completed |
| `display_mode` | string | `"headless"` or display identifier |

---

## Device Endpoints

All device endpoints require authentication.

### GET /devices

List all registered devices.

**Response 200:**

```json
{
    "devices": [
        {
            "device_id": "uuid-string",
            "name": "Kitchen Light",
            "type": "actuator",
            "protocol": "zigbee",
            "state": {"power": true, "brightness": 80},
            "capabilities": ["turn_on", "turn_off", "set_brightness"],
            "last_seen": 1711900000.0,
            "module_id": "protocol-bridge",
            "meta": {"manufacturer": "IKEA"}
        }
    ]
}
```

---

### POST /devices

Register a new device.

**Request:**

```json
{
    "name": "Kitchen Light",
    "type": "actuator",
    "protocol": "zigbee",
    "capabilities": ["turn_on", "turn_off"],
    "meta": {}
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Human-readable device name |
| `type` | string | yes | One of: `sensor`, `actuator`, `controller`, `virtual` |
| `protocol` | string | yes | Communication protocol (e.g. `zigbee`, `mqtt`, `http`) |
| `capabilities` | list[string] | yes | Supported actions |
| `meta` | object | no | Arbitrary metadata |

**Response 201:** DeviceResponse (same schema as items in `GET /devices`).

Publishes a `device.registered` event on the event bus.

---

### GET /devices/{device_id}

Retrieve a single device by its UUID.

**Response 200:** DeviceResponse

**Response 404:**

```json
{"detail": "Device not found"}
```

---

### PATCH /devices/{device_id}/state

Update the state of a device.

**Request:**

```json
{
    "state": {"power": true, "brightness": 80}
}
```

The `state` object is a free-form dictionary. Its keys depend on the device capabilities.

**Response 200:** DeviceResponse (with updated state)

Publishes a `device.state_changed` event containing both `old_state` and `new_state`.

---

### DELETE /devices/{device_id}

Remove a device from the registry.

**Response 204:** No body.

Publishes a `device.removed` event.

---

## Event Endpoints

All event endpoints require authentication.

### POST /events/publish

Publish a custom event to the event bus.

**Request:**

```json
{
    "type": "my.custom_event",
    "source": "my-module",
    "payload": {"key": "value"}
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | string | yes | Event type identifier (dot-separated namespace) |
| `source` | string | yes | Module or component that generated the event |
| `payload` | object | no | Arbitrary event data |

**Response 201:**

```json
{
    "event_id": "uuid",
    "type": "my.custom_event",
    "timestamp": 1711900000.0
}
```

**Response 403:** Returned when a module attempts to publish a `core.*` event. Only the core system may emit events in the `core` namespace.

---

### POST /events/subscribe

Subscribe to events via webhook callback.

> **Deprecated.** Use the [Module Bus WebSocket](#websocket---module-bus) instead.

**Request:**

```json
{
    "event_types": ["device.state_changed"],
    "webhook_url": "http://localhost:8100/webhook"
}
```

**Response 201:**

```json
{
    "subscription_id": "uuid",
    "event_types": ["device.state_changed"],
    "webhook_url": "http://localhost:8100/webhook"
}
```

---

## Module Endpoints

All module endpoints require authentication.

### GET /modules

List all installed modules.

**Response 200:**

```json
{
    "modules": [
        {
            "name": "weather-module",
            "version": "1.0.0",
            "type": "UI",
            "status": "RUNNING",
            "runtime_mode": "always_on",
            "port": 0,
            "installed_at": 1711900000.0,
            "ui": {
                "icon": "icon.svg",
                "widget": {"file": "widget.html", "size": "2x2"}
            }
        }
    ]
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Module type (e.g. `UI`, `SYSTEM`, `SERVICE`) |
| `status` | string | `VALIDATING`, `READY`, `RUNNING`, `STOPPED`, `ERROR` |
| `runtime_mode` | string | `always_on` or `on_demand` |
| `port` | int | Assigned port (0 if not applicable) |
| `ui` | object or null | UI widget configuration, if the module provides one |

---

### POST /modules/install

Install a module from a ZIP archive. Uses multipart form upload.

**Request:**

```
Content-Type: multipart/form-data
Field: module (file, .zip)
```

**Response 201:**

```json
{
    "name": "my-module",
    "status": "VALIDATING",
    "message": "Module uploaded, validation in progress"
}
```

Installation is asynchronous. Use the SSE stream endpoint to track progress.

---

### GET /modules/{name}/status/stream

Server-Sent Events stream for tracking module installation and lifecycle changes.

**Response:** `text/event-stream`

```
data: {"status": "VALIDATING", "message": "Manifest validated, installing..."}
data: {"status": "READY", "message": "Validation passed, starting..."}
data: {"status": "RUNNING", "message": "Module started"}
```

A heartbeat message is sent every 30 seconds if there are no status updates.

---

### POST /modules/{name}/start

Start a stopped module.

**Response 200:**

```json
{"name": "my-module", "status": "RUNNING"}
```

---

### POST /modules/{name}/stop

Stop a running module.

**Response 200:**

```json
{"name": "my-module", "status": "STOPPED"}
```

**Response 403:** Returned when attempting to stop a `SYSTEM` module. System modules cannot be stopped.

---

### DELETE /modules/{name}

Remove an installed module and clean up its resources.

**Response 204:** No body.

**Response 403:** Returned when attempting to remove a `SYSTEM` module. System modules cannot be removed.

---

## Secret Endpoints

All secret endpoints require authentication.

### GET /secrets

List stored secret identifiers. Values are never returned in plaintext.

### POST /secrets

Store an OAuth token or other secret. Secrets are encrypted at rest using AES-256-GCM.

---

## Integrity Endpoints

All integrity endpoints require authentication.

### GET /integrity/status

Returns the current status of the Integrity Agent, which monitors file and configuration tampering.

---

## Intent Endpoints (Deprecated)

> **Deprecated.** Intents are now managed via the Module Bus `announce` mechanism. These REST endpoints remain for backward compatibility but will be removed in a future release.

### GET /intents

List intents announced via Module Bus.

### POST /intents/register

Register new intents. Use Module Bus `announce` instead.

---

## WebSocket - Module Bus

### WS /bus?token=TOKEN

WebSocket endpoint for real-time Module Bus communication. Modules connect here to announce capabilities, subscribe to events, and exchange messages with the core.

Pass the module token as the `token` query parameter.

See [Module Bus Protocol](module-bus-protocol.md) for the full message format and handshake reference.

---

## UI Routes

**Base:** `/api/ui`

These routes are intended for the local web UI only. They are protected by iptables rules (localhost access only) and do **not** require Bearer tokens.

| Route | Description |
|---|---|
| `POST /api/ui/setup/*` | Onboarding wizard steps |
| `GET /api/ui/setup/vosk/catalog` | Vosk speech-to-text model catalog |
| Voice engine endpoints | Manage STT/TTS engine configuration |
| Module UI routing | Serve module widget files and icons |

### Audio Setup Endpoints

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/ui/setup/audio/devices` | List detected ALSA input/output devices |
| POST | `/api/ui/setup/audio/select` | Save `{input, output}` device selection to core.yaml |
| POST | `/api/ui/setup/audio/test/output` | Play speaker-test (L/R voice) at configured volume |
| POST | `/api/ui/setup/audio/test/input` | Record 3s from mic `{device}`, measure peak, play back on `{output_device}` |
| GET | `/api/ui/setup/audio/mic-level` | Quick 1s mic sample → `{level: 0.0-1.0}` |
| GET | `/api/ui/setup/audio/levels` | Get `{output_volume, input_gain}` from config |
| POST | `/api/ui/setup/audio/levels` | Set `{output_volume?, input_gain?}` — persists + applies via amixer |
| GET | `/api/ui/setup/audio/sources` | List audio source modules → `{sources: [{module, name, volume}]}` |
| POST | `/api/ui/setup/audio/sources/volume` | Set `{module, volume}` for a specific audio source |

---

## Error Responses

All errors return a JSON body with a `detail` field.

**Standard error:**

```json
{
    "detail": "Error message"
}
```

**Validation error (422 Unprocessable Entity):**

```json
{
    "detail": {"errors": ["error1", "error2"]}
}
```

### Common Status Codes

| Code | Meaning |
|---|---|
| 200 | Success |
| 201 | Created |
| 204 | No Content (successful deletion) |
| 400 | Bad Request |
| 401 | Unauthorized (missing or invalid token) |
| 403 | Forbidden (insufficient permissions) |
| 404 | Not Found |
| 422 | Validation Error |
| 429 | Too Many Requests (rate limit exceeded) |
| 500 | Internal Server Error |

---

## Event Types Reference

Events use a dot-separated namespace. The following namespaces are defined:

### core.*
Reserved for the core system. Modules cannot publish these.

| Event | Description |
|---|---|
| `core.startup` | Core has started |
| `core.shutdown` | Core is shutting down |
| `core.integrity_violation` | File or config tampering detected |
| `core.integrity_restored` | Integrity check passed after a previous violation |
| `core.safe_mode_entered` | System entered safe mode |
| `core.safe_mode_exited` | System exited safe mode |

### device.*

| Event | Description |
|---|---|
| `device.state_changed` | Device state was updated (includes `old_state` and `new_state`) |
| `device.registered` | New device was added |
| `device.removed` | Device was deleted |
| `device.offline` | Device stopped responding |
| `device.online` | Device reconnected |
| `device.discovered` | New device discovered on the network |

### module.*

| Event | Description |
|---|---|
| `module.installed` | Module was installed |
| `module.started` | Module was started |
| `module.stopped` | Module was stopped |
| `module.error` | Module encountered an error |
| `module.removed` | Module was uninstalled |

### sync.*

| Event | Description |
|---|---|
| `sync.command_received` | Remote command received from cloud sync |
| `sync.command_ack` | Command acknowledgment sent |
| `sync.connection_lost` | Cloud sync connection lost |
| `sync.connection_restored` | Cloud sync connection restored |

### voice.*

| Event | Description |
|---|---|
| `voice.wake_word` | Wake word detected |
| `voice.recognized` | Speech recognized |
| `voice.intent` | Intent extracted from speech |
| `voice.response` | Voice response generated |
| `voice.privacy_on` | Microphone muted / privacy mode enabled |
| `voice.privacy_off` | Microphone unmuted / privacy mode disabled |
