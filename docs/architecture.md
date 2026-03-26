# SelenaCore Architecture

## Overview

SelenaCore consists of two independent processes:

```
┌─────────────────────────────────────────────────────────────────┐
│                      smarthome-core (Docker)                     │
│                                                                   │
│  FastAPI :7070 (Core API)          FastAPI :80 (UI Core)       │
│       │                                   │                      │
│  ┌────┴────────────────────────────────────┴───────────────┐     │
│  │             EventBus (asyncio.Queue)                     │     │
│  └────┬────────────────────────────────────┬───────────────┘     │
│       │                                   │                      │
│  Device Registry              Module Loader (Plugin Manager)      │
│  (SQLite)                     + DockerSandbox                    │
│                                                                   │
│  CloudSync ← HMAC             Voice Core (STT/TTS/wake-word)     │
│  IntegrityAgent (30s)         LLM Engine (Ollama)                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│              smarthome-agent (systemd, separate process)         │
│                                                                   │
│  SHA256 check of core files every 30 sec                         │
│  On violation: stop modules → notify → rollback → SAFE MODE      │
└─────────────────────────────────────────────────────────────────┘
```

---

## Components

### Module Execution Model

SelenaCore uses a **two-tier module execution model** to conserve RAM (~580 MB saved on Raspberry Pi):

| Type | Execution | Port | Communication | Container |
|------|-----------|------|---------------|-----------|
| **SYSTEM** | In-process via `importlib` | None | Direct Python calls + `SystemModule` ABC | smarthome-core (shared) |
| **User (UI/INTEGRATION/DRIVER/AUTOMATION)** | Docker sandbox | 8100–8200 | HTTP API + webhook events | smarthome-modules |

**System modules** inherit from `SystemModule` (`core/module_loader/system_module.py`) and are loaded by `sandbox.py → _start_in_process()`. Their `APIRouter` is mounted in the core FastAPI app at `/api/ui/modules/{name}/`. They access the EventBus and Device Registry through direct Python method calls — no HTTP overhead.

**User modules** run in isolated Docker containers with their own ports and communicate with the core exclusively via the REST API and webhook event delivery.

---

### 1. Core API (`core/api/`)

FastAPI REST server, port `7070`. Entry point for all modules.

**Middleware:**
- `X-Request-Id` generated for each request, propagated via `contextvars`
- CORS — only `localhost` allowed
- Rate limiting — 120 req/min per token (external), 600 req/min (LAN/localhost); SSE and static files are exempt

**Authorization (`core/api/auth.py`):**
- `Authorization: Bearer <module_token>` required for all endpoints except `/health`
- Token stored as a plaintext file in `/secure/module_tokens/<name>.token`; also supports `DEV_MODULE_TOKEN` env var in dev mode
- No per-endpoint permission granularity in v1 — any valid token grants access

---

### 2. Device Registry (`core/registry/`)

SQLite device storage via SQLAlchemy 2.0 async.

**Tables:**
- `devices` — devices (id, name, type, protocol, state JSON, capabilities, last_seen, module_id, meta)
- `state_history` — last 1000 states per device (archive)
- `audit_log` — all user actions (10,000 records, rotation)

**Automatic event:**
On `PATCH /devices/{id}/state`, a `device.state_changed` event is automatically published to Event Bus.

---

### 3. Event Bus (`core/eventbus/`)

```python
# Publishing
await bus.publish(event)          # puts into asyncio.Queue

# Subscribing (user modules — webhook)
await bus.subscribe("device.*", webhook_url)  # wildcard

# Subscribing (system modules — in-process)
bus.subscribe_direct(sub_id, module_id, ["device.*"], callback)

# Delivery (background task)
# Webhooks: POST http://module:810X/webhook/events
# Direct:   asyncio.create_task(callback(event))
X-Selena-Signature: sha256=<hmac>    # HMAC-SHA256 signature (webhooks only)
```

**Protection:**
- `core.*` events cannot be published from a module → 403 Forbidden
- HMAC-SHA256 signature on every webhook delivery

---

### 4. Module Loader (`core/module_loader/`)

#### Module Lifecycle

```
UPLOADED → VALIDATING → READY → RUNNING → STOPPED → REMOVED
                                    ↓
                                  ERROR
```

#### Module Installation

1. Upload ZIP → `/api/v1/modules/install`
2. **Validator** (`validator.py`) verifies `manifest.json`:
   - Required fields: `name`, `version`, `type`, `api_version`, `port`, `permissions`
   - `name` — RFC 1123 slug (`[a-z0-9-]+`)
   - `version` — semver (`^\d+\.\d+\.\d+$`)
   - `port` — 8100–8200
   - `permissions` — only allowed values
3. **Sandbox** — testing in `smarthome-sandbox` container (--rm)
4. **DockerSandbox** — launch in `smarthome-modules` on a dedicated port

#### SYSTEM Module Protection

Modules with `type: SYSTEM` cannot be stopped via API → 403 Forbidden.

---

### 5. Integrity Agent (`agent/`)

Independent process (systemd unit `smarthome-agent.service`), **does not import** the core.

```
Every 30 sec:
  1. Read /secure/master.hash
  2. Compute SHA256 of /secure/core.manifest
  3. Compare → if mismatch: MANIFEST TAMPERED
  4. For each core file: SHA256 from manifest vs SHA256 on disk
  5. If changes found:
       a) Log to /var/log/selena/integrity.log
       b) Stop all modules (Docker stop)
       c) Notify platform
       d) Rollback from /secure/core_backup/ (3 attempts, 5 sec pause)
       e) If rollback failed → SAFE MODE
```

**SAFE MODE:**
- Core API read-only (`GET` methods only)
- Installation and launch of new modules prohibited
- `GET /health` returns `"mode": "safe_mode"`

---

### 6. Cloud Sync (`core/cloud_sync/`)

Background task: heartbeat every 60 sec + long-poll commands.

```
Heartbeat:
  POST /api/v1/device/heartbeat
  Headers:
    X-Device-Hash: <hash>
    X-Signature: sha256=<hmac>    # HMAC-SHA256 body + timestamp + key from /secure/platform.key
  Body: { status, uptime, modules, integrity }

Long-poll:
  GET /api/v1/device/commands?device_hash=...&wait=30
  → Handling: INSTALL_MODULE | STOP_MODULE | REBOOT | SYNC_STATE | FACTORY_RESET
  → Response: POST /api/v1/device/commands/{id}/ack
```

**Retry:** exponential backoff 2^n sec, max 300 sec.

---

### 7. Voice Core (`system_modules/voice_core/`)

| Component | File | Technology |
|-----------|------|------------|
| Wake-word | `wake_word.py` | openWakeWord |
| STT | `stt.py` | Vosk |
| TTS | `tts.py` | Piper TTS |
| Speaker ID | `speaker_id.py` | resemblyzer |
| Audio I/O | `audio_manager.py` | ALSA + PipeWire |
| WebRTC | — | browser → Whisper pipeline |

**Audio input priorities:** `usb > i2s_gpio > bluetooth > hdmi > builtin`

---

### 8. LLM Engine (`system_modules/llm_engine/`)

Two-level router:

```
Level 1: Fast Matcher
  Loads YAML with keyword/regex rules
  Matching in ~50 ms
  No network, no GPU

Level 2: Ollama (fallback)
  phi-3-mini (2.3 GB VRAM — Pi 5 8GB)
  gemma:2b (1.5 GB)
  Auto-disable when RAM < 5 GB
```

---

### 9. Secrets Vault (`system_modules/secrets_vault/`)

```
/secure/tokens/<module>/<key>.enc
```

Each secret: `nonce(12 bytes) + ciphertext` (AES-256-GCM).
Encryption key: PBKDF2(HMAC-SHA256, passphrase, salt=module_name, iterations=480000).

**OAuth Device Flow (RFC 8628):**
1. `POST /api/v1/secrets/oauth/start` → device_code, QR code
2. Polling → token stored encrypted
3. Module uses `POST /api/v1/secrets/proxy` — token **never leaves** the core

**SSRF protection for proxy:**
- Only `https://`
- Private IP blocking: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- No allowed redirects (follow_redirects=False)

---

## Network Isolation

```
External network
    ↓ :443 (HTTPS — platform and OAuth only)
    ↓ :80  (redirect)
WiFi interface (wlan0 / wlan1)
    ↓
iptables FORWARD DROP
    ↓
localhost
  :7070  Core API        (modules + UI only)
  :80  UI Core         (user browser)
  :8100  User Module 1   (Docker sandbox only — NOT system modules)
  :8101  User Module 2
  ...
  :8200  User Module 100
```

Docker network: `selena_modules` (Compose-managed bridge network).
User modules run inside the `smarthome-modules` container and reach the core via `extra_hosts: selena-core:host-gateway`.
System modules run inside the core process and have NO network ports.

---

## Data Storage

```
/var/lib/selena/selena.db     SQLite (Registry, AuditLog, Voice History)
/var/lib/selena/modules/      Unpacked modules
/var/lib/selena/backups/      Local archives

/secure/platform.key          Platform API key (600 bytes, AES-256-GCM)
/secure/tls/                  Self-signed HTTPS certificates
/secure/tokens/<module>/      Encrypted OAuth tokens
/secure/core.manifest         SHA256 of core files
/secure/master.hash           SHA256 of the manifest itself
/secure/core_backup/v0.3.0/   Core files backup copy
```

---

## Further Reading

| Topic | Document |
|-------|----------|
| User authentication & QR flow | [docs/user-manager-auth.md](user-manager-auth.md) |
| Module protocol (tokens, HMAC, webhooks) | [docs/module-core-protocol.md](module-core-protocol.md) |
| Module development (SDK, manifest) | [docs/module-development.md](module-development.md) |
| Widget development (widget.html, i18n) | [docs/widget-development.md](widget-development.md) |
| Deployment & systemd | [docs/deployment.md](deployment.md) |
