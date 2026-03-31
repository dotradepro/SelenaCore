# WebSocket Module Bus Protocol Reference

SelenaCore Module Bus is a CAN-bus inspired communication layer where the core acts as the master node and external modules connect as peers over a single WebSocket endpoint. This document is the authoritative protocol reference for module developers.

## Endpoint

```
ws://<host>:7070/api/v1/bus?token=<module_token>
```

All module-to-core communication goes through this single endpoint. There are no per-module ports.

---

## Connection Lifecycle

```
Module                                          Core
  |                                               |
  |  WebSocket connect ?token=TOKEN               |
  |---------------------------------------------->|
  |                          token validation      |
  |                          (reject -> close 4001)|
  |                                               |
  |              WebSocket accept()               |
  |<----------------------------------------------|
  |                                               |
  |  announce {...capabilities}                   |
  |---------------------------------------------->|
  |                                               |
  |              announce_ack {bus_id}            |
  |<----------------------------------------------|
  |                                               |
  |       bidirectional message loop              |
  |<--------------------------------------------->|
  |                                               |
  |              ping (every 15s)                 |
  |<----------------------------------------------|
  |  pong                                         |
  |---------------------------------------------->|
  |                                               |
  |              shutdown {drain_ms}              |
  |<----------------------------------------------|
  |  (finish work, close connection)              |
  |---------------------------------------------->|
```

### Steps

1. **Connect** -- Module opens a WebSocket connection with `?token=TOKEN` as a query parameter.
2. **Authentication** -- Core validates the token _before_ calling `accept()`. An invalid token results in an immediate close with code `4001`.
3. **Announce** -- Module sends an `announce` message declaring its name, version, and capabilities.
4. **Registration** -- Core validates the announcement, registers the module, and replies with `announce_ack` containing the assigned `bus_id`.
5. **Message loop** -- Bidirectional communication begins. The module can send and receive all supported message types.
6. **Health checks** -- Core sends `ping` every 15 seconds. The module must reply with `pong`. Three consecutive missed pings result in a disconnect (close code `4004`).
7. **Shutdown** -- Core sends a `shutdown` message with a `drain_ms` window. The module should finish in-flight work within that window and exit gracefully.

---

## Message Format

Every message is a JSON object with a required `type` field:

```json
{"type": "<message_type>", ...}
```

The following sections define each message type, its direction, and its schema.

---

## Message Types

### announce

**Direction:** module -> core

Sent immediately after the WebSocket connection is accepted. Declares the module identity and its capabilities.

```json
{
  "type": "announce",
  "module": "weather-module",
  "version": "1.0.0",
  "capabilities": {
    "intents": [
      {
        "patterns": {
          "en": ["weather", "forecast"],
          "uk": ["погода", "прогноз"]
        },
        "priority": 50,
        "description": "Weather queries"
      }
    ],
    "subscriptions": ["device.state_changed"],
    "publishes": ["weather.module_started"]
  }
}
```

| Field | Type | Description |
|---|---|---|
| `module` | string | Unique module identifier, must match the registered manifest name. |
| `version` | string | Semver version of the module. |
| `capabilities.intents` | array | List of intent declarations this module can handle. |
| `capabilities.intents[].patterns` | object | Map of language code to list of trigger keywords/phrases. |
| `capabilities.intents[].priority` | integer | Routing priority. Lower values are matched first. |
| `capabilities.intents[].description` | string | Human-readable description of the intent group. |
| `capabilities.subscriptions` | array | Event types the module wants to receive. Supports wildcards (e.g. `device.*`). |
| `capabilities.publishes` | array | Event types the module is permitted to emit. |

If the module does not send `announce` within the configured timeout, the connection is closed with code `4002`.

---

### re_announce

**Direction:** module -> core

Identical schema to `announce` but with `"type": "re_announce"`. Allows a module to hot-reload its capabilities (add/remove intents, change subscriptions) without dropping the WebSocket connection.

```json
{
  "type": "re_announce",
  "module": "weather-module",
  "version": "1.1.0",
  "capabilities": { ... }
}
```

The core replaces the module's registered capabilities atomically and responds with a new `announce_ack`.

---

### announce_ack

**Direction:** core -> module

Confirms successful registration or re-registration.

```json
{
  "type": "announce_ack",
  "bus_id": "uuid-1234",
  "warnings": []
}
```

| Field | Type | Description |
|---|---|---|
| `bus_id` | string | UUID assigned by the core. Used internally for routing. |
| `warnings` | array | List of non-fatal warning strings (e.g. unknown subscription patterns). |

---

### intent

**Direction:** core -> module

Dispatched when a user query matches one of the module's registered intent patterns.

```json
{
  "type": "intent",
  "id": "uuid-request",
  "payload": {
    "text": "what's the weather?",
    "lang": "en",
    "context": {}
  }
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique request identifier. Must be echoed back in `intent_response`. |
| `payload.text` | string | The raw user query text. |
| `payload.lang` | string | Detected language code (`en`, `uk`, etc.). |
| `payload.context` | object | Arbitrary context from the originating session. |

The module must respond within **10 seconds** or the request is considered timed out.

---

### intent_response

**Direction:** module -> core

Response to an `intent` message. The `id` field must match the original request.

```json
{
  "type": "intent_response",
  "id": "uuid-request",
  "payload": {
    "handled": true,
    "tts_text": "It's currently 12°C and cloudy",
    "data": {
      "temperature": 12,
      "condition": "cloudy"
    }
  }
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Must match the `id` from the corresponding `intent` message. |
| `payload.handled` | boolean | `true` if the module successfully handled the intent. `false` triggers fallthrough to the next matching module. |
| `payload.tts_text` | string | Text-to-speech response for the user. |
| `payload.data` | object | Structured data accompanying the response. Schema is module-specific. |

If `handled` is `false`, the core routes the intent to the next eligible module (up to 3 fallthrough attempts total).

---

### event

**Direction:** bidirectional

Used for publish/subscribe event broadcasting.

```json
{
  "type": "event",
  "payload": {
    "event_type": "device.state_changed",
    "data": {
      "device_id": "xxx",
      "state": {"power": true}
    }
  }
}
```

| Field | Type | Description |
|---|---|---|
| `payload.event_type` | string | Dot-separated event type identifier. |
| `payload.data` | object | Arbitrary event payload. |

**Module -> core:** The `event_type` is validated against the module's declared `publishes` list. Events not in the list are rejected.

**Core -> module:** Delivered only if the `event_type` matches one of the module's `subscriptions` patterns. Wildcard matching is supported -- `device.*` matches `device.state_changed`, `device.added`, etc.

---

### ping / pong

**Direction:** bidirectional

Health check mechanism.

```json
{"type": "ping", "ts": 1711900000}
```

```json
{"type": "pong", "ts": 1711900000}
```

| Field | Type | Description |
|---|---|---|
| `ts` | integer | Unix timestamp (seconds) of the ping origin. Echoed back in pong. |

The core sends `ping` every **15 seconds**. The module must respond with `pong` containing the same `ts` value. After **3 consecutive missed pings**, the core closes the connection with code `4004`.

---

### api_request

**Direction:** module -> core

Allows a module to call SelenaCore REST API endpoints over the bus without making a separate HTTP connection. Permissions are enforced via the ACL system.

```json
{
  "type": "api_request",
  "id": "req-uuid",
  "payload": {
    "method": "GET",
    "path": "/devices",
    "body": null
  }
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique request identifier. Echoed in the corresponding `api_response`. |
| `payload.method` | string | HTTP method: `GET`, `POST`, `PATCH`, `DELETE`. |
| `payload.path` | string | API path (without the `/api/v1` prefix). |
| `payload.body` | object or null | JSON request body. `null` for GET/DELETE. |

---

### api_response

**Direction:** core -> module

Response to an `api_request`.

```json
{
  "type": "api_response",
  "id": "req-uuid",
  "payload": {
    "status": 200,
    "body": [
      {"device_id": "...", "name": "Kitchen Light"}
    ]
  }
}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Matches the `id` from the originating `api_request`. |
| `payload.status` | integer | HTTP-equivalent status code. |
| `payload.body` | any | Response body. Structure matches the corresponding REST API endpoint. |

Unauthorized requests receive status `403`.

---

### shutdown

**Direction:** core -> module

Sent when the core is shutting down or explicitly disconnecting the module.

```json
{
  "type": "shutdown",
  "drain_ms": 5000
}
```

| Field | Type | Description |
|---|---|---|
| `drain_ms` | integer | Milliseconds the module has to finish in-flight work before the connection is terminated. |

The module should complete any pending operations within the specified window and then close its end of the connection.

---

## Dual Channel System

Each WebSocket connection maintains two internal queues to separate traffic by priority:

| Queue | Max Size | Overflow Policy | Message Types |
|---|---|---|---|
| **Critical** | 100 | Backpressure (blocks sender) | `intent`, `intent_response`, `api_request`, `api_response` |
| **Event** | 1000 | Drop oldest | `event` |

The writer coroutine always drains the critical queue first. This guarantees that intent handling and API calls are never starved by a burst of event traffic.

---

## ACL System

Module permissions are declared in the module's `manifest.json` and enforced on every `api_request`. The permission-to-endpoint mapping:

| Permission | Allowed Operations |
|---|---|
| `devices.read` | `GET /devices`, `GET /devices/{id}` |
| `devices.write` | `POST /devices`, `PATCH /devices/{id}/state`, `DELETE /devices/{id}` |
| `events.subscribe` | Receive events matching subscription patterns |
| `events.publish` | `POST /events/publish`, emit `event` messages on the bus |

Requests that exceed the module's granted permissions receive a `403` status in the `api_response`.

---

## Circuit Breaker

A per-module circuit breaker protects the system from unresponsive modules:

1. **Closed (normal)** -- Intents are routed to the module as usual.
2. **Open (tripped)** -- The module is excluded from intent routing. Triggered when the module consistently times out on intent requests.
3. **Recovery** -- After **30 seconds** in the open state, the breaker allows a trial request. A successful `intent_response` resets the breaker to closed.

The circuit breaker only affects intent routing. Events and API requests continue to flow normally while the breaker is open.

---

## Intent Routing

When a user query arrives, the core resolves it through the bus:

1. `route_intent(text, lang, context)` is called.
2. The input text is matched against a compiled regex index built from all connected modules' intent patterns. Matching is **case-insensitive**.
3. All matches are sorted by `priority` (lower value = higher priority).
4. The `intent` message is sent to the first matching module.
5. If the module responds with `handled: false`, the next match is tried.
6. Maximum **3 fallthrough attempts** before the query is considered unhandled.
7. Each module has a **10-second timeout** to respond.

---

## Close Codes

| Code | Name | Description |
|---|---|---|
| `4001` | `invalid_token` | Authentication failed. The provided token is missing, expired, or invalid. |
| `4002` | `announce_timeout` | Module did not send an `announce` message within the required timeout after connection. |
| `4003` | `invalid_json` / `expected_announce` | Protocol violation. The first message was not valid JSON or was not an `announce` message. |
| `4004` | `ping_timeout` | Health check failed. Three consecutive pings went unanswered. |
| `1001` | `core_shutdown` | The core is shutting down gracefully. |

---

## Quick Start Example

A minimal module session:

```
1. Connect:    ws://localhost:7070/api/v1/bus?token=abc123
2. Send:       {"type":"announce","module":"my-module","version":"0.1.0","capabilities":{"intents":[],"subscriptions":["device.*"],"publishes":[]}}
3. Receive:    {"type":"announce_ack","bus_id":"550e8400-e29b-41d4-a716-446655440000","warnings":[]}
4. Receive:    {"type":"ping","ts":1711900000}
5. Send:       {"type":"pong","ts":1711900000}
6. Receive:    {"type":"event","payload":{"event_type":"device.state_changed","data":{"device_id":"light-1","state":{"power":true}}}}
7. Send:       {"type":"api_request","id":"r1","payload":{"method":"GET","path":"/devices","body":null}}
8. Receive:    {"type":"api_response","id":"r1","payload":{"status":200,"body":[{"device_id":"light-1","name":"Kitchen Light"}]}}
```
