# SelenaCore System Architecture

## Table of Contents

- [Overview](#overview)
- [High-Level Architecture](#high-level-architecture)
- [Boot Sequence](#boot-sequence)
- [Module System](#module-system)
- [EventBus](#eventbus)
- [Module Bus](#module-bus)
- [Intent System](#intent-system)
- [Device Registry](#device-registry)
- [API Layer](#api-layer)
- [Cloud Sync](#cloud-sync)
- [Configuration](#configuration)
- [Internationalization](#internationalization)
- [Integrity Agent](#integrity-agent)
- [Deployment](#deployment)
- [Shutdown Sequence](#shutdown-sequence)
- [Further Reading](#further-reading)

---

## Overview

SelenaCore is a **local-first smart home hub** built on FastAPI, designed to run on low-power hardware such as Raspberry Pi. All automation logic, device management, and voice processing happen on the local machine. Cloud connectivity is optional and limited to heartbeat sync and remote command reception.

**Core technology stack:**

| Component       | Technology                          |
|-----------------|-------------------------------------|
| Web framework   | FastAPI (port 80)                 |
| Database        | SQLite via SQLAlchemy 2.0 async     |
| Async driver    | aiosqlite                           |
| Event loop      | Single asyncio loop                 |
| Entry point     | `core/main.py` (FastAPI lifespan)   |
| Language        | Python 3.11                         |

---

## High-Level Architecture

```
+------------------------------------------------------------------+
|                         SelenaCore Process                        |
|   port 80 (FastAPI)                                            |
|                                                                  |
|  +------------------+   +------------------+   +--------------+  |
|  | 21 SYSTEM        |   |    EventBus      |   |  Device      |  |
|  | modules          |<->| (asyncio.Queue)  |<->|  Registry    |  |
|  | (in-process)     |   |                  |   |  (SQLite)    |  |
|  +------------------+   +--------+---------+   +--------------+  |
|                                  |                               |
|                         +--------+---------+                     |
|                         |   Module Bus     |                     |
|                         |   (WebSocket)    |                     |
|                         +--------+---------+                     |
|                                  |                               |
+----------------------------------+-------------------------------+
                                   |
                    +--------------+--------------+
                    |              |              |
               +----+----+  +----+----+  +------+------+
               | Docker  |  | Docker  |  | Docker      |
               | Module  |  | Module  |  | Module      |
               | (user)  |  | (user)  |  | (user)      |
               +---------+  +---------+  +-------------+

  Separate process:
  +----------------------------+
  | Integrity Agent            |
  | SHA256 hash check / 30s   |
  | Safe mode enforcement      |
  +----------------------------+
```

---

## Boot Sequence

The startup procedure is defined in the FastAPI lifespan handler in `core/main.py`. Steps execute in strict order:

```
1. _setup_logging()
   |  Read logging.yaml or fall back to basic config
   v
2. Create SQLAlchemy async engine + tables
   |  SQLite database initialized
   v
3. Inject session factory into sandbox
   |  System modules gain database access
   v
4. EventBus.start()
   |  asyncio.Queue consumer begins
   v
5. Publish core.startup event
   |  Listeners notified
   v
6. CloudSync.start()
   |  Heartbeat loop begins (optional)
   v
7. Scan system_modules/ -> load in-process -> mount routers
   |  21 built-in modules activated
   v
8. Scan modules/ -> start user modules
   |  Docker containers launched, bus connections accepted
   v
9. "SelenaCore ready on port 80"
```

---

## Module System

SelenaCore supports two distinct module types that share the same EventBus but differ fundamentally in how they run.

### System Modules (in-process)

| Property          | Value                                           |
|-------------------|-------------------------------------------------|
| Count             | 21 built-in                                     |
| Base class        | `SystemModule` (`core/module_loader/system_module.py`) |
| Execution         | In-process via Python `importlib`               |
| Isolation         | None (shared process)                           |
| RAM overhead      | ~0 MB (no container)                            |
| EventBus access   | Direct async callbacks (DirectSubscription)     |
| Database access   | Direct SQLAlchemy session                       |
| API surface       | Optional FastAPI router at `/api/ui/modules/{name}/` |
| Location          | `system_modules/` directory                     |

**Built-in system modules:**

```
voice_core           llm_engine           ui_core
user_manager         automation_engine    scheduler
device_watchdog      protocol_bridge      notification_router
media_player         presence_detection   hw_monitor
backup_manager       remote_access        network_scanner
device_control       energy_monitor       update_manager
notify_push          secrets_vault        weather_service
```

### User Modules (Docker containers)

| Property          | Value                                           |
|-------------------|-------------------------------------------------|
| Base class        | `SmartHomeModule` (`sdk/base_module.py`)        |
| Execution         | Individual Docker containers                    |
| Communication     | WebSocket Module Bus                            |
| Bus endpoint      | `ws://core/api/v1/bus?token=TOKEN`         |
| Individual ports  | None -- all traffic through the single bus      |

**User module types:**

| Type              | Purpose                                         |
|-------------------|-------------------------------------------------|
| UI                | Custom user interface panels                    |
| INTEGRATION       | Third-party service connectors                  |
| DRIVER            | Hardware/protocol device drivers                |
| AUTOMATION        | Custom automation logic                         |
| IMPORT_SOURCE     | External data importers                         |

### Module Lifecycle

```
  [Discovered]
       |
       v
  [Installed] --module.installed-->
       |
       v
  [Started]   --module.started--->  (EventBus subscription active)
       |
       v
  [Running]   <-- normal operation -->
       |
       v
  [Stopped]   --module.stopped--->
       |
       v
  [Removed]   --module.removed--->
```

---

## EventBus

**Source:** `core/eventbus/bus.py`

The EventBus is the central nervous system of SelenaCore. It is an asyncio.Queue-based publish/subscribe system with a maximum queue size of 10,000 messages and a drop-oldest overflow policy.

### Delivery Channels

```
  Publisher
      |
      v
  +---+-----------+
  |   EventBus    |
  | (Queue: 10K)  |
  +---+-------+---+
      |       |
      v       v
  Direct    Module Bus
  Subscr.   WebSocket
  (system)  (user modules)
```

1. **DirectSubscription** -- in-process async callbacks used by system modules. Zero serialization cost, microsecond delivery.
2. **Module Bus WebSocket** -- events serialized to JSON and delivered over the WebSocket connection to user modules running in Docker containers.

### Event Namespace

All event types are defined in `core/eventbus/types.py`:

| Namespace   | Events                                                              |
|-------------|---------------------------------------------------------------------|
| `core.*`    | startup, shutdown, integrity_violation, safe_mode_entered, safe_mode_exited |
| `device.*`  | state_changed, registered, removed, offline, online, discovered     |
| `module.*`  | installed, started, stopped, error, removed                         |
| `sync.*`    | command_received, command_ack, connection_lost, connection_restored  |
| `voice.*`   | wake_word, recognized, intent, response, privacy_on, privacy_off    |

**Protection rule:** Events in the `core.*` namespace can only be published by the core process itself. Modules cannot emit core events.

---

## Module Bus

**Source:** `core/module_bus.py`

The Module Bus is a CAN-bus-inspired communication layer that multiplexes all user module traffic through a single WebSocket endpoint.

### Design Principles

- **Core is the master node.** Modules connect TO core, never the reverse.
- **Single endpoint:** `/api/v1/bus` -- no per-module ports.
- **Dual message queues** per connection to separate critical and best-effort traffic.

### Message Types

| Message            | Direction        | Purpose                            |
|--------------------|------------------|------------------------------------|
| `announce`         | module -> core   | Module registers on connect        |
| `re_announce`      | module -> core   | Module re-registers after reconnect|
| `announce_ack`     | core -> module   | Registration confirmed             |
| `intent`           | core -> module   | Intent routed to handler           |
| `intent_response`  | module -> core   | Handler returns result             |
| `event`            | bidirectional    | EventBus event forwarding          |
| `ping` / `pong`    | bidirectional    | Keepalive                          |
| `api_request`      | module -> core   | Module calls core API              |
| `api_response`     | core -> module   | Core returns API result            |
| `shutdown`         | core -> module   | Graceful shutdown signal           |

### Dual Queue Architecture

Each WebSocket connection maintains two independent queues:

```
  Module Connection
  +---------------------------------------+
  |                                       |
  |  Critical Queue (backpressure)        |
  |  - Max size: 100                      |
  |  - Used for: intent, api_request,     |
  |    api_response, intent_response      |
  |  - Blocks sender when full            |
  |                                       |
  |  Event Queue (drop-oldest)            |
  |  - Max size: 1000                     |
  |  - Used for: event messages           |
  |  - Drops oldest when full             |
  |                                       |
  +---------------------------------------+
```

This design ensures that a flood of non-critical events never blocks intent processing or API calls.

### Circuit Breaker

If a module fails to respond within 30 seconds, the bus activates a circuit breaker for that module. The module is temporarily excluded from intent routing until it recovers.

### ACL Permissions

Each module type has a predefined set of allowed message types and event subscriptions. The bus enforces these permissions on every message.

---

## UI Sync (WebSocket)

**Source:** `core/api/sync_manager.py`, `core/api/routes/ui.py`

Real-time synchronization of UI state (theme, language, widget layout) across all connected clients via WebSocket `/api/ui/sync`.

| Property | Value |
|----------|-------|
| Endpoint | `ws://host/api/ui/sync?v=<version>` |
| Protocol | JSON messages with monotonic versioning |
| State | Settings (theme, language) + widget layout |
| On connect | Full snapshot (`hello`) or delta replay (`replay`) |
| Health check | Server ping every 5s, client pong required within 15s |
| Backend | `SyncManager` singleton with `deque(256)` event log |
| Frontend | Zustand store `connectSyncStream()` with exponential backoff reconnect |
| Kiosk safety | `useConnectionHealth` hook — force reload after 60s of silence |

The SPA (React) and all API endpoints are served from a **single process on port 80**. HTTPS on port 443 is handled by a lightweight TLS proxy (~5 MB RAM).

> See [UI Sync Architecture](ui-sync-architecture.md) for full protocol details and migration notes.

---

## Intent System

**Source:** `system_modules/llm_engine/intent_router.py`

The intent router uses a 6-tier cascade. Each tier is tried in order; the first match wins. This design balances speed against understanding depth.

```
  User utterance
       |
       v
  +----------+
  | Tier 1   |  Fast Matcher (YAML keyword/regex rules)
  | ~0 ms    |  Defined in YAML config files
  +----------+
       | miss
       v
  +----------+
  | Tier 1.5 |  System Module Intents (in-process regex)
  | ~μs      |  Registered by system modules at startup (28 intents)
  +----------+
       | miss
       v
  +----------+
  | Tier 2   |  Module Bus Intents (user modules via WebSocket)
  | ~ms      |  Registered via announce message with regex patterns
  +----------+
       | miss
       v
  +----------+
  | Tier 3a  |  Cloud LLM Classification (Gemini / OpenAI / Anthropic)
  | ~1-2 sec |  Structured JSON intent classification
  +----------+
       | miss
       v
  +----------+
  | Tier 3b  |  Ollama LLM (local semantic understanding)
  | 3-8 sec  |  Requires 5GB+ RAM, runs locally
  +----------+
       | miss
       v
  +----------+
  | Fallback |  i18n "not understood" message
  +----------+
       |
       v
    Response (LLM rephrase for variety → TTS)
```

**Tier details:**

| Tier | Source | Latency | Mechanism | RAM Cost |
|------|--------|---------|-----------|----------|
| 1 | Fast Matcher | ~0 ms | YAML keyword and regex rules | Negligible |
| 1.5 | System Modules | ~μs | In-process regex + priority + named groups | Negligible |
| 2 | Module Bus | ~ms | WebSocket round-trip with regex | Negligible |
| 3a | Cloud LLM | ~1-2 sec | Structured intent classification via cloud API | None (cloud) |
| 3b | Ollama LLM | 3-8 sec | Full semantic model inference | 5 GB+ |
| — | Fallback | ~0 ms | i18n "not understood" | Negligible |

Intent routing supports **priority ordering** and **regex pattern matching** at all tiers. Modules register their intent patterns along with a numeric priority; higher-priority handlers are tried first within each tier.

**Cloud LLM Classification (Tier 3a):** When regex tiers miss, the router dynamically builds a catalog of all known intents and sends a classification prompt to the active cloud LLM provider. The LLM returns structured JSON (`{"intent": "...", "params": {...}}`). This enables natural language understanding on low-RAM devices like Raspberry Pi where local Ollama is disabled.

**LLM Response Rephrase:** After a module executes a voice command and generates a response, voice-core sends the default text to the Cloud LLM for rephrasing (temperature=0.9). This produces variative, natural-sounding TTS responses instead of repetitive templates. A conversation session (last 20 messages, 5-min timeout) provides context for coherent dialogue.

**Voice-enabled modules:** media-player (14 intents), weather-service (3), presence-detection (3), automation-engine (4), energy-monitor (2), device-watchdog (2). See [Voice Pipeline Configuration](voice-settings.md) for the full command reference.

---

## Device Registry

**Source:** `core/registry/`

The device registry is the persistent store for all known devices, their current state, and historical data.

### Database Schema (SQLAlchemy ORM)

**Device table:**

| Column       | Type     | Description                              |
|--------------|----------|------------------------------------------|
| device_id    | UUID     | Primary key, auto-generated              |
| name         | String   | Human-readable device name               |
| type         | String   | Device category (light, sensor, etc.)    |
| protocol     | String   | Communication protocol (zigbee, mqtt...) |
| state        | JSON     | Current device state blob                |
| capabilities | JSON     | Supported features and value ranges      |
| last_seen    | DateTime | Last communication timestamp             |
| module_id    | String   | Owning module identifier                 |
| meta         | JSON     | Arbitrary metadata                       |

**StateHistory table:**

- Records the last **1,000 state changes** per device.
- Each entry stores the previous state, new state, and timestamp.
- Older entries are pruned automatically.

**AuditLog table:**

- Stores up to **10,000 records** with automatic rotation.
- Logs administrative actions: device registration, removal, configuration changes.

---

## API Layer

### Middleware Pipeline

Requests pass through middleware in the following order:

```
  Incoming request
       |
       v
  RequestIdMiddleware       -- Assigns unique X-Request-ID header
       |
       v
  RateLimitMiddleware       -- 120 requests per 60 seconds
       |
       v
  CORSMiddleware            -- Cross-origin policy
       |
       v
  Route handler
```

### Authentication

- **Bearer token** authentication for module and external API access.
- Tokens stored in `/secure/module_tokens/`.
- UI routes (`/api/ui/*`) require no auth but are restricted to localhost only.

### Route Groups

**Core API (`/api/v1/*`) -- authenticated:**

| Route        | Purpose                                  |
|--------------|------------------------------------------|
| `/system`    | System info, health, status              |
| `/devices`   | Device CRUD and state queries            |
| `/events`    | EventBus inspection and publishing       |
| `/integrity` | Integrity check status and reports       |
| `/modules`   | Module lifecycle management              |
| `/secrets`   | Secrets vault access                     |
| `/intents`   | Intent routing and testing               |
| `/bus`       | Module Bus WebSocket endpoint            |

**UI API (`/api/ui/*`) -- localhost only, no auth:**

| Route           | Purpose                               |
|-----------------|---------------------------------------|
| `/ui`           | UI panel serving                      |
| `/setup`        | First-run setup wizard                |
| `/voice_engines`| Voice engine configuration            |

### Swagger Documentation

Available at `/docs` only when `DEBUG=true` in the environment.

---

## Cloud Sync

**Source:** `core/cloud_sync/sync.py`

Cloud connectivity is optional and designed to be minimal. The core never depends on cloud availability for local operation.

| Parameter             | Value                              |
|-----------------------|------------------------------------|
| Remote server         | selenehome.tech                   |
| Heartbeat interval    | 60 seconds                         |
| Request signing       | HMAC-SHA256                        |
| Command poll timeout  | 55 seconds (long-poll)             |
| Backoff (initial)     | 5 seconds                          |
| Backoff (maximum)     | 300 seconds                        |
| Backoff strategy      | Exponential                        |

```
  SelenaCore                          selenehome.tech
     |                                      |
     |--- heartbeat (HMAC-SHA256) --------->|
     |<-- 200 OK ---------------------------|
     |                                      |
     |--- long-poll /commands ------------->|
     |         (55s timeout)                |
     |<-- command payload ------------------|
     |                                      |
     |--- command ack --------------------->|
     |                                      |
```

On network failure, the sync client backs off exponentially from 5 seconds up to 300 seconds before retrying.

---

## Configuration

SelenaCore uses a dual-source configuration model.

### Environment Variables (.env)

Managed by **Pydantic BaseSettings** in `core/config.py` via the `CoreSettings` class. All fields are typed and validated at startup.

**Key settings:**

| Variable          | Default               | Description                    |
|-------------------|-----------------------|--------------------------------|
| `CORE_PORT`       | 80                  | FastAPI listening port         |
| `CORE_DATA_DIR`   | /var/lib/selena       | Persistent data directory      |
| `CORE_SECURE_DIR` | /secure               | Tokens and secrets storage     |
| `DEBUG`           | false                 | Enable debug mode and /docs    |

### YAML Configuration (core.yaml)

Used for structured configuration that does not fit well into flat environment variables (module settings, logging presets, automation rules).

**Precedence:** Environment variables override YAML values where both sources define the same setting.

---

## Internationalization

**Frontend:** `src/i18n/locales/{en,uk}.ts` via i18next + react-i18next.

**Voice responses:** Generated by LLM in real-time via `_generate_via_llm()` in VoiceCoreModule. Voice handlers return structured action context dicts; LLM produces natural-language TTS text in the configured TTS language. No pre-written translations or caching — every response is freshly generated.

**HTML widgets:** Built-in `var L = {en:{…}, uk:{…}}` dictionaries with `data-i18n` attributes.

---

## Integrity Agent

**Source:** `agent/`

The Integrity Agent runs as a **separate process** alongside the core. It is the watchdog that ensures the core codebase has not been tampered with.

### Operation Cycle

```
  every 30 seconds:
       |
       v
  Compute SHA256 hashes of core files
       |
       v
  Compare against known-good manifest
       |
       +-- match -----> OK, sleep 30s
       |
       +-- mismatch --> VIOLATION DETECTED
                             |
                             v
                        Stop all modules
                             |
                             v
                        Notify (core.integrity_violation event)
                             |
                             v
                        Attempt rollback
                             |
                             v
                        Enter SAFE MODE
                             |
                             v
                        Publish core.safe_mode_entered
```

In **Safe Mode**, only essential core functions remain active. All user modules are stopped and cannot be restarted until the integrity issue is resolved.

---

## Deployment

### Docker Compose Architecture

```
  docker-compose.yml
  +--------------------------------------------------+
  |                                                  |
  |  +------------------+    +-------------------+   |
  |  | core             |    | agent             |   |
  |  | Dockerfile.core  |    | Integrity Agent   |   |
  |  | Host networking  |    | Separate process  |   |
  |  | Privileged mode  |    |                   |   |
  |  +------------------+    +-------------------+   |
  |         |                        |               |
  |         v                        v               |
  |  +-------------+    +------------------+         |
  |  | selena_data |    | selena_secure    |         |
  |  | (volume)    |    | (volume)         |         |
  |  +-------------+    +------------------+         |
  |                                                  |
  +--------------------------------------------------+
```

### Core Container (Dockerfile.core)

| Property        | Value                                              |
|-----------------|----------------------------------------------------|
| Base image      | python:3.11-slim                                   |
| Network mode    | host                                               |
| Privileges      | privileged (hardware access)                       |
| System packages | ffmpeg, portaudio, VLC, ALSA libs, PulseAudio      |

Host networking and privileged mode are required for:
- Direct access to audio hardware (microphone, speakers) for voice processing.
- Access to USB devices and GPIO pins for protocol bridges (Zigbee, Z-Wave, Thread dongles).
- Multicast/broadcast for device discovery protocols (mDNS, SSDP, Matter, Thread).
- Bluetooth Low Energy radio for Matter commissioning (see [matter-thread.md](matter-thread.md)).

### Volumes

| Volume          | Purpose                                            |
|-----------------|----------------------------------------------------|
| selena_data     | Database, module data, logs, backups               |
| selena_secure   | Tokens, secrets, certificates                      |

---

## Shutdown Sequence

Graceful shutdown mirrors the boot sequence in reverse, ensuring no data loss:

```
1. CloudSync.stop()
   |  Stop heartbeat and command polling
   v
2. Publish core.shutdown event
   |  All listeners notified
   v
3. Module Bus shutdown_all(drain_ms=5000)
   |  Send shutdown to all user modules
   |  Wait up to 5 seconds for queues to drain
   v
4. Shutdown in-process system modules
   |  Each system module's stop() called
   v
5. EventBus.stop()
   |  Queue consumer halted
   v
6. Database engine dispose
   |  All connections closed, WAL checkpoint
   v
   Process exit
```

The 5-second drain window for the Module Bus ensures that user modules have time to persist their state and acknowledge the shutdown before their WebSocket connections are terminated.

---

## Further Reading

| Topic | Document |
|-------|----------|
| User authentication and QR flow | [user-manager-auth.md](user-manager-auth.md) |
| Module protocol (tokens, HMAC, webhooks) | [module-core-protocol.md](module-core-protocol.md) |
| Module Bus wire protocol | [module-bus-protocol.md](module-bus-protocol.md) |
| Module development (SDK, manifest) | [module-development.md](module-development.md) |
| Widget development (widget.html, i18n) | [widget-development.md](widget-development.md) |
| Configuration reference | [configuration.md](configuration.md) |
| Deployment and systemd | [deployment.md](deployment.md) |
