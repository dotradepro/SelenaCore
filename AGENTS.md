# AGENTS.md — SelenaCore Agent Contract
## SmartHome LK · Local Device Core · MUST READ before every session

---

## 0. BEFORE STARTING — MANDATORY CHECKLIST

```
AGENTS.md          ← this file (read completely)
docs/TZ.md         ← technical specification v0.3 (source of truth)
README.md          ← project structure, launch commands
```

**Workflow — strictly step by step:**

```
1. Read AGENTS.md (this file)
2. Read docs/TZ.md — understand the full picture
3. Break down the spec into tasks → create GitHub Issues
4. Take one task → implement → commit → push
5. Close Issue → take next one
```

> ⛔ Do not start writing code before creating an Issue.
> ⛔ Do not take a second task until the first one is closed.
> ⛔ Do not push to main with broken tests.

---

## 1. PROJECT STRUCTURE

```
selena-core/
  core/
    main.py                    # FastAPI + asyncio entry point
    config.py                  # core.yaml + .env loading
    registry/
      service.py               # DeviceRegistry
      models.py                # SQLAlchemy ORM
    eventbus/
      bus.py                   # EventBus (asyncio.Queue)
      types.py                 # event type constants
    module_loader/
      loader.py                # Plugin Manager + lifecycle
      sandbox.py               # Docker isolation + in-process loading
      system_module.py         # SystemModule ABC (base class)
      validator.py             # manifest.json validation
    api/
      routes/
        devices.py             # GET/POST /api/v1/devices
        events.py              # /api/v1/events/*
        modules.py             # /api/v1/modules/*
        integrity.py           # /api/v1/integrity/status
        system.py              # /api/v1/health, /api/v1/system/*
      auth.py                  # module_token verification
      middleware.py            # CORS, X-Request-Id, rate limiting
    cloud_sync/
      sync.py                  # CloudSync (asyncio background task)
      commands.py              # platform command handlers
  system_modules/
    voice_core/
      stt.py                   # Vosk STT wrapper (offline, ARM-optimized)
      tts.py                   # Piper wrapper
      wake_word.py             # Vosk grammar-based wake word
      speaker_id.py            # resemblyzer
      privacy.py               # privacy mode (GPIO + command)
    llm_engine/
      ollama_client.py         # Ollama REST client
      intent_router.py         # Fast Matcher + LLM tiers
      fast_matcher.py          # keyword/regex rules
      model_manager.py         # model loading and selection
    network_scanner/
      arp_scanner.py           # ARP sweep
      mdns_listener.py         # mDNS/Bonjour
      ssdp_listener.py         # SSDP/UPnP
      zigbee_scanner.py        # Zigbee via USB dongle
      classifier.py            # OUI lookup + auto-classification
    user_manager/
      profiles.py              # User profile CRUD
      voice_biometric.py       # voice prints (resemblyzer)
      face_auth.py             # video authorization (face_recognition)
      audit_log.py             # action audit log
    secrets_vault/
      vault.py                 # AES-256-GCM storage
      oauth_flow.py            # Device Authorization Grant (RFC 8628)
      proxy.py                 # API proxy for modules
    backup_manager/
      local_backup.py          # USB/SD backup
      cloud_backup.py          # E2E cloud backup
      qr_transfer.py           # QR secret transfer
    remote_access/
      tailscale.py             # Tailscale VPN client
    hw_monitor/
      monitor.py               # CPU temperature, RAM, disk
      throttle.py              # automatic load reduction
    notify_push/
      vapid.py                 # Web Push VAPID
    ui_core/
      server.py                # FastAPI server :80
      pwa.py                   # PWA manifest + service worker
      wizard.py                # Onboarding wizard endpoints
      routes/                  # ui-core pages
  agent/
    integrity_agent.py         # Integrity Agent (separate process)
    manifest.py                # core.manifest + SHA256
    responder.py               # response chain + SAFE MODE
  sdk/
    smarthome_sdk/
      base.py                  # SmartHomeModule base class
      decorators.py            # @on_event, @schedule
      client.py                # Core API client
      cli.py                   # smarthome CLI (new-module, dev, test)
  config/
    core.yaml                  # core configuration
    logging.yaml               # logging configuration
  tests/
    test_registry.py
    test_eventbus.py
    test_module_loader.py
    test_integrity.py
    test_api.py
    test_cloud_sync.py
    test_voice.py
    test_wizard.py
  requirements.txt
  requirements-dev.txt
  Dockerfile.core              # smarthome-core image
  Dockerfile.modules           # smarthome-modules image
  Dockerfile.sandbox           # smarthome-sandbox image
  docker-compose.yml
  smarthome-core.service       # core systemd unit
  smarthome-agent.service      # agent systemd unit
  smarthome-modules.service    # module container systemd unit
  .env.example
  core.yaml.example
```

---

## 2. STACK AND VERSIONS

### 2.1 SelenaCore Versioning

Format: `MAJOR.MINOR.PATCH-LABEL+COMMIT`

| Part | Source | Example | Description |
|------|--------|---------|-------------|
| `MAJOR.MINOR` | Manually in `core/version.py` | `0.3` | Release number, bumped manually |
| `PATCH` | `git rev-list --count HEAD` | `142` | Commit count — grows automatically with each commit |
| `LABEL` | Manually in `core/version.py` | `beta` | `beta` → `rc` → empty (release) |
| `COMMIT` | `git rev-parse --short HEAD` | `0644435` | 7-character SHA of latest commit |

Full version example: `0.3.142-beta+0644435`

**Single source of truth:** `core/version.py`

```python
# core/version.py
MAJOR = 0
MINOR = 3
LABEL = "beta"   # "beta" | "rc" | ""
# PATCH and COMMIT are computed automatically from git
```

**Rules:**

```
✅ Version is computed centrally — `from core.version import VERSION`
✅ PATCH grows automatically with each commit (no need to change manually)
✅ COMMIT is tied to the current git HEAD — you can always find the exact commit

⛔ Do not hardcode the version string — only `from core.version import VERSION`
⛔ Do not change PATCH manually — it is computed from git automatically
⛔ When bumping MAJOR/MINOR — change only in core/version.py
```

**Where it is used:**

| Location | How it is obtained |
|----------|--------------------|
| `GET /api/v1/health` | `from core.version import VERSION` |
| `GET /api/v1/system/info` | `from core.version import VERSION` |
| FastAPI OpenAPI docs | `from core.version import VERSION` |
| Event `core.startup` payload | `from core.version import VERSION` |
| Frontend (SystemPage) | From API → `health.version` / `stats.version` |

### 2.2 Technology Stack

| Component | Version | Purpose |
|-----------|---------|---------|
| Python | 3.11+ | Core language |
| FastAPI | 0.111+ | HTTP server (Core API + UI Core) |
| SQLAlchemy | 2.0+ | ORM for SQLite |
| SQLite | built-in | Device Registry storage, audit log |
| Docker SDK (docker-py) | 7.0+ | Container management |
| Vosk | 0.3.45 | Local STT (offline, ARM/aarch64) |
| Piper (piper-tts) | latest | Local TTS |
| Vosk grammar | — | Wake-word (through the main STT model) |
| resemblyzer | latest | Speaker ID (voice prints) |
| face_recognition (dlib) | latest | Face ID |
| Ollama | latest | LLM runner (phi-3-mini, gemma-2b) |
| cryptography (Fernet/AES) | 46.0.5+ | Secrets vault encryption |
| qrcode | latest | QR codes (wizard, transfer) |
| bleak / bluez | latest | Bluetooth control |
| pyaudio + ALSA | latest | Audio I/O |
| pytest + httpx | latest | Tests |

---

## 3. CODE WRITING RULES

### Python — general rules

```python
# ✅ Correct
class DeviceRegistry:
    async def get(self, device_id: str) -> Device | None:
        ...

    async def update_state(self, device_id: str, state: dict) -> Device:
        ...

# ❌ Wrong — no types, no async
class DeviceRegistry:
    def get(self, id):
        ...
```

- All public methods — async
- Type hints are mandatory (Python type hints)
- One file = one responsibility
- Logging through `logging.getLogger(__name__)` — no `print()`
- Exceptions — through custom classes, no bare `raise Exception("...")`
- `X-Request-Id` must be propagated through all services via `contextvars`

### FastAPI — rules

```python
# ✅ Correct — router only parses and calls service
@router.get("/devices/{device_id}")
async def get_device(
    device_id: str,
    registry: DeviceRegistry = Depends(get_registry),
    token: str = Depends(verify_module_token),
) -> DeviceResponse:
    device = await registry.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceResponse.from_orm(device)

# ❌ Wrong — business logic in router
@router.get("/devices/{device_id}")
async def get_device(device_id: str):
    db = sqlite3.connect("data.db")  # ← not allowed
    ...
```

- Router = HTTP only (parse → service → response)
- All logic in services
- Pydantic models for all request/response
- Dependency Injection via `Depends()`
- HTTPException for all errors

### Forbidden patterns

```python
# ⛔ Not allowed
print("debug")                   # logging only
import os; os.system("rm -rf")   # shell injection risk
eval(user_input)                 # RCE risk
open("/secure/platform.key")     # only through SecretVault API
subprocess.run(shell=True)       # only with a specific argument list
except:                          # only except Exception as e:
    pass                         # never empty catch
```

---

## 3.1. LOCALIZATION (i18n) — RULES

### Supported languages

| Code | Language | Status |
|------|----------|--------|
| `en` | English | Primary (fallback) |
| `uk` | Українська | Primary |

### Infrastructure

```
src/i18n/
  i18n.ts              # i18next configuration + changeLanguage()
  locales/
    en.ts              # English translations
    uk.ts              # Ukrainian translations
```

- Library: `i18next` + `react-i18next`
- Default language: `en`
- Fallback: `en`
- Selected language storage: `localStorage('selena-lang')`
- Switching: via `changeLanguage()` from `src/i18n/i18n.ts`

### Frontend rules

```tsx
// ✅ Correct — all strings through t()
import { useTranslation } from 'react-i18next';

function MyComponent() {
  const { t } = useTranslation();
  return <h1>{t('dashboard.welcomeHome')}</h1>;
}

// ❌ Wrong — hardcoded text
function MyComponent() {
  return <h1>Welcome home</h1>;
}
```

**Mandatory rules:**

- ⛔ Do not hardcode UI text in any language — only through `t('key')`
- All translation keys are stored in `src/i18n/locales/en.ts` and `src/i18n/locales/uk.ts`
- Key structure: `section.key` (e.g. `dashboard.welcomeHome`, `wizard.selectLanguage`)
- When adding new text — add translation to BOTH files (`en.ts` and `uk.ts`)
- Interpolation: `t('devices.registryInfo', { count: 5 })` → `"5 devices registered."`
- Do not use `t` as a variable name in `map()` and loops (conflicts with `useTranslation`)

### Rules for system HTML widgets (widget.html / settings.html)

Every `widget.html` and `settings.html` file of a system module **must** implement
built-in EN/UK localization following this standard template:

```javascript
// 1. At the start of <script> (before any other declarations):
var LANG = (function () { try { return localStorage.getItem('selena-lang') || 'en'; } catch (e) { return 'en'; } })();
var L = {
    en: { key: 'English text', ... },
    uk: { key: 'Текст', ... }
};
function t(k) { return (L[LANG] || L.en)[k] || k; }
function applyLang() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
        el.textContent = t(el.getAttribute('data-i18n'));
    });
}
window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'lang_changed') {
        try { LANG = localStorage.getItem('selena-lang') || 'en'; } catch (ex) { }
        applyLang();
        // Call data reload functions (refresh(), loadStatus(), load(), etc.)
    }
});

// 2. Static HTML elements get the data-i18n="key" attribute:
// <h1 data-i18n="title"></h1>
// <label data-i18n="lbl_name"></label>

// 3. Dynamic strings in JS use t('key') instead of literals:
// innerHTML = '<span>' + t('no_devices') + '</span>';
// showToast(t('saved_ok'));

// 4. applyLang() is called during initialization (before the first refresh/load)
```

**Mandatory rules:**

- ⛔ Do not hardcode UI text in any language in HTML or JavaScript code of widgets/settings
- Language is read from `localStorage('selena-lang')` — values `'en'` | `'uk'`
- Dictionaries for both languages (`en` and `uk`) must contain the same set of keys
- `applyLang()` must be called before the first `refresh()` / `load()` / `loadStatus()` call
- On language change (`lang_changed` postMessage) — call `applyLang()` and reload data
- Abbreviations (MQTT, STT, TTS, LLM, ID) and technical names do not need translation
- In template literals `${...}` use `t('key')` for localizable strings

### Rules for Python backend (system and user modules)

Infrastructure:

```
core/i18n.py                # t(key, lang, **kwargs) — central translation function
config/locales/
  en.json                   # English translations (fallback)
  uk.json                   # Ukrainian translations
```

```python
from core.i18n import t

# TTS responses
await m.speak(t("media.playing_radio", station=name))

# API errors
raise HTTPException(status_code=404, detail=t("api.device_not_found"))

# Terminal UI
print(t("tty.mobile_setup"))

# Explicit language
text = t("media.paused", lang="uk")
```

**Mandatory rules:**

- ⛔ Do not hardcode user-facing strings in Python — only through `t('key')`
- All keys are stored in `config/locales/en.json` and `config/locales/uk.json`
- Key structure: `section.key` (e.g. `media.playing_radio`, `api.device_not_found`)
- When adding new text — add translation to BOTH files (`en.json` and `uk.json`)
- Interpolation: `t('media.volume_set', level=50)` → `"Volume set to 50"`
- Fallback chain: requested language → `en` → raw key (never crashes)
- Language source: `core.yaml system.language` (via `get_system_lang()`)
- Logger messages (`logger.info/debug/warning/error`) are **NOT** translated
- User modules: `locales/` in the module directory + `self.t('key')`

**Key categories:**

| Prefix | Description | Example |
|--------|-------------|---------|
| `media.*` | Media TTS responses | `media.playing_radio`, `media.paused` |
| `fast_matcher.*` | Fast matcher responses | `fast_matcher.light_on` |
| `intent.*` | IntentRouter messages | `intent.fallback` |
| `tty.*` | Terminal UI | `tty.mobile_setup`, `tty.first_run` |
| `wizard.*` | Onboarding wizard | `wizard.req_internet` |
| `presence.*` | Presence detection | `presence.invite_not_found` |
| `api.*` | API errors | `api.device_not_found`, `api.text_empty` |

### Documentation rules

- All documentation (`docs/`, `README.md`, `CONTRIBUTING.md`) is maintained in **two languages**:
  - Primary file — in English
  - Ukrainian version — in `docs/uk/` with suffix or in a subfolder
- Format: `docs/architecture.md` (EN) + `docs/uk/architecture.md` (UK)
- When changing documentation — update BOTH languages

### Adding a new language

1. Create file `src/i18n/locales/<code>.ts` (copy structure from `en.ts`)
2. Register in `src/i18n/i18n.ts` in `resources`
3. Create file `config/locales/<code>.json` (copy structure from `en.json`)
4. Translate all keys (frontend + backend)
5. Add option in Wizard (step 1 — language selection)
6. Add documentation in `docs/<code>/`

---

## 4. CORE API — FULL SPECIFICATION

Base URL: `http://localhost:7070/api/v1`
Auth: `Authorization: Bearer <module_token>`

### 4.1 Health

```http
GET /api/v1/health
Authorization: (not required)

Response 200:
{
  "status": "ok",
  "version": "0.3.0-beta",
  "mode": "normal",        // "normal" | "safe_mode"
  "uptime": 86400,
  "integrity": "ok"        // "ok" | "violated" | "restoring"
}
```

### 4.2 Device Registry

```http
GET /api/v1/devices
Authorization: Bearer <token>

Response 200:
{
  "devices": [
    {
      "device_id": "uuid-...",
      "name": "Kitchen Thermostat",
      "type": "actuator",           // sensor | actuator | controller | virtual
      "protocol": "zigbee",
      "state": { "temperature": 22.5, "mode": "heat" },
      "capabilities": ["set_temperature", "set_mode"],
      "last_seen": 1710936000.0,
      "module_id": "climate-module",
      "meta": {}
    }
  ]
}
```

```http
POST /api/v1/devices
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Kitchen Thermostat",
  "type": "actuator",
  "protocol": "zigbee",
  "capabilities": ["set_temperature", "set_mode"],
  "meta": { "zigbee_addr": "0x1234" }
}

Response 201:
{
  "device_id": "uuid-generated",
  "name": "Kitchen Thermostat",
  "type": "actuator",
  "protocol": "zigbee",
  "state": {},
  "capabilities": ["set_temperature", "set_mode"],
  "last_seen": null,
  "module_id": null,
  "meta": { "zigbee_addr": "0x1234" }
}
```

```http
GET /api/v1/devices/{device_id}
Authorization: Bearer <token>

Response 200: <Device object>
Response 404: { "detail": "Device not found" }
```

```http
PATCH /api/v1/devices/{device_id}/state
Authorization: Bearer <token>
Content-Type: application/json

{
  "state": { "temperature": 23.0, "mode": "cool" }
}

Response 200: <Device object with updated state>

// Automatically publishes device.state_changed event to Event Bus
```

```http
DELETE /api/v1/devices/{device_id}
Authorization: Bearer <token>

Response 204: (no content)
```

### 4.3 Event Bus

```http
POST /api/v1/events/publish
Authorization: Bearer <token>
Content-Type: application/json

{
  "type": "device.state_changed",
  "source": "climate-module",
  "payload": {
    "device_id": "uuid-...",
    "old_state": { "temperature": 22.0 },
    "new_state": { "temperature": 23.0 }
  }
}

Response 201:
{
  "event_id": "uuid-...",
  "type": "device.state_changed",
  "timestamp": 1710936000.0
}

// ⛔ Error if type starts with "core." — 403 Forbidden
Response 403:
{
  "detail": "Publishing core.* events is forbidden for modules"
}
```

```http
POST /api/v1/events/subscribe
Authorization: Bearer <token>
Content-Type: application/json

{
  "event_types": ["device.state_changed", "device.offline"],
  "webhook_url": "http://localhost:8100/webhook/events"
}

Response 201:
{
  "subscription_id": "sub-uuid-...",
  "event_types": ["device.state_changed", "device.offline"],
  "webhook_url": "http://localhost:8100/webhook/events"
}
```

**Event delivery to module webhook:**

```http
POST http://localhost:8100/webhook/events
Content-Type: application/json
X-Selena-Event: device.state_changed
X-Selena-Signature: sha256=<hmac>

{
  "event_id": "uuid-...",
  "type": "device.state_changed",
  "source": "climate-module",
  "payload": { ... },
  "timestamp": 1710936000.0
}
```

### 4.4 Module Loader

```http
GET /api/v1/modules
Authorization: Bearer <token>

Response 200:
{
  "modules": [
    {
      "name": "climate-module",
      "version": "1.0.0",
      "type": "UI",
      "status": "RUNNING",    // UPLOADED|VALIDATING|READY|RUNNING|STOPPED|ERROR|REMOVED
      "runtime_mode": "always_on",
      "port": 8100,
      "installed_at": 1710936000.0
    }
  ]
}
```

```http
POST /api/v1/modules/install
Authorization: Bearer <token>
Content-Type: multipart/form-data

module: <zip-archive>

Response 201:
{
  "name": "climate-module",
  "status": "VALIDATING",
  "message": "Module uploaded, validation in progress"
}

// Statuses arrive via SSE: GET /api/v1/modules/{name}/status/stream
```

```http
GET /api/v1/modules/{name}/status/stream
Authorization: Bearer <token>

// Server-Sent Events
data: {"status": "VALIDATING", "message": "Checking manifest.json..."}
data: {"status": "READY", "message": "Validation passed, starting..."}
data: {"status": "RUNNING", "message": "Module started on port 8100"}
```

```http
POST /api/v1/modules/{name}/stop
POST /api/v1/modules/{name}/start
DELETE /api/v1/modules/{name}
Authorization: Bearer <token>

Response 200: { "name": "climate-module", "status": "STOPPED" }
Response 403: if module type is SYSTEM
```

### 4.5 Integrity Status

```http
GET /api/v1/integrity/status
Authorization: Bearer <token>

Response 200:
{
  "status": "ok",              // "ok" | "violated" | "restoring" | "safe_mode"
  "last_check": 1710936000.0,
  "check_interval_sec": 30,
  "changed_files": [],         // list if violation
  "restore_attempts": 0,
  "safe_mode_since": null
}
```

### 4.6 Secrets (for integrations)

```http
POST /api/v1/secrets/oauth/start
Authorization: Bearer <token>
Content-Type: application/json

{
  "module": "gmail-integration",
  "provider": "google",
  "scopes": ["gmail.readonly", "gmail.send"]
}

Response 201:
{
  "session_id": "oauth-uuid-...",
  "qr_code_url": "/api/v1/secrets/oauth/qr/oauth-uuid-...",
  "verification_uri": "https://accounts.google.com/device?user_code=XXXX",
  "user_code": "XXXX-YYYY",
  "expires_in": 1800,
  "poll_interval": 5
}
```

```http
GET /api/v1/secrets/oauth/status/{session_id}
Authorization: Bearer <token>

Response 200:
{
  "status": "pending",    // "pending" | "authorized" | "expired" | "error"
  "module": "gmail-integration"
}

// When status == "authorized":
{
  "status": "authorized",
  "module": "gmail-integration",
  "connected": true
  // token is NOT returned — stored in vault
}
```

```http
POST /api/v1/secrets/proxy
Authorization: Bearer <token>
Content-Type: application/json

{
  "module": "gmail-integration",
  "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages",
  "method": "GET",
  "headers": { "Content-Type": "application/json" },
  "body": null
}

Response 200:
{
  "status_code": 200,
  "headers": { ... },
  "body": { ... }     // response from external API
}

// Core injects the token, executes the request, returns the result
// Module NEVER sees the token
```

### 4.7 System / Onboarding

```http
GET /api/v1/system/info
Authorization: (not required on first launch)

Response 200:
{
  "initialized": false,
  "wizard_completed": false,
  "version": "0.3.0-beta",
  "hardware": {
    "model": "Raspberry Pi 5 Model B Rev 1.0",
    "ram_total_mb": 8192,
    "has_hdmi": true,
    "has_camera": false
  },
  "audio": {
    "inputs": [
      { "id": "hw:1,0", "name": "USB Audio", "type": "usb" }
    ],
    "outputs": [
      { "id": "hw:0,0", "name": "bcm2835 Headphones", "type": "jack" },
      { "id": "bluez_sink.AA:BB:CC", "name": "JBL Flip", "type": "bluetooth" }
    ]
  },
  "display_mode": "framebuffer"
}
```

```http
POST /api/v1/wizard/step
Content-Type: application/json

{
  "step": "wifi",
  "data": {
    "ssid": "MyHomeNetwork",
    "password": "secret123"
  }
}

Response 200:
{
  "step": "wifi",
  "status": "ok",
  "next_step": "language",
  "message": "Connected to MyHomeNetwork. IP: 192.168.1.45"
}

// Available steps: wifi | language | device_name | timezone |
//                 stt_model | tts_voice | admin_user | platform | import
```

---

## 5. MANIFEST.JSON — FULL SCHEMA

```json
{
  "name": "climate-module",
  "version": "1.0.0",
  "description": "Climate control via Zigbee thermostats",
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
  "oauth": null,
  "resources": {
    "memory_mb": 128,
    "cpu": 0.25
  },
  "author": "SmartHome LK",
  "license": "MIT",
  "homepage": "https://github.com/dotradepro/SelenaCore"
}
```

**manifest.json validation on install:**

```python
REQUIRED_FIELDS = ["name", "version", "type", "api_version", "port", "permissions"]
VALID_TYPES = ["SYSTEM", "UI", "INTEGRATION", "DRIVER", "AUTOMATION", "IMPORT_SOURCE"]
VALID_PROFILES = ["HEADLESS", "SETTINGS_ONLY", "ICON_SETTINGS", "FULL"]
VALID_RUNTIME = ["always_on", "on_demand", "scheduled"]
ALLOWED_PERMISSIONS = [
    "device.read", "device.write",
    "events.subscribe", "events.publish",
    "secrets.oauth",     # only for INTEGRATION
    "secrets.proxy",     # only for INTEGRATION
]
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"  # semver
```

---

## 6. EVENT BUS EVENTS — FULL LIST

```python
# Built-in event types (published only by core — core.*)
CORE_EVENTS = {
    "core.integrity_violation": "Agent detected core file changes",
    "core.integrity_restored":  "Agent successfully rolled back changes",
    "core.safe_mode_entered":   "System entered SAFE MODE",
    "core.safe_mode_exited":    "SAFE MODE lifted",
    "core.startup":             "Core started",
    "core.shutdown":            "Core shutting down",
}

# Devices
DEVICE_EVENTS = {
    "device.state_changed":  "Device state changed in Registry",
    "device.registered":     "New device added to Registry",
    "device.removed":        "Device removed from Registry",
    "device.offline":        "No heartbeat > 90 sec",
    "device.online":         "Device available again",
    "device.discovered":     "Scanner found a new device on the network",
}

# Modules
MODULE_EVENTS = {
    "module.installed":  "Module installed and started",
    "module.stopped":    "Module stopped normally",
    "module.started":    "Module started",
    "module.error":      "Module returned an error or crashed",
    "module.removed":    "Module removed",
}

# Platform synchronization
SYNC_EVENTS = {
    "sync.command_received":   "Command received from platform",
    "sync.command_ack":        "Command acknowledged",
    "sync.connection_lost":    "Connection to platform lost",
    "sync.connection_restored":"Connection restored",
}

# Voice
VOICE_EVENTS = {
    "voice.wake_word":      "Wake-word detected",
    "voice.recognized":     "STT recognized query",
    "voice.intent":         "Intent Router determined intent (see §20)",
    "voice.response":       "LLM/fallback response ready (text for TTS)",
    "voice.speak":          "TTS speech request (from any module)",
    "voice.speak_done":     "TTS speech completed",
    "voice.privacy_on":     "Privacy mode enabled",
    "voice.privacy_off":    "Privacy mode disabled",
}

# Media (published by media-player)
MEDIA_EVENTS = {
    "media.state_changed":  "Playback state changed",
}
```

---

## 7. INTEGRITY AGENT — ALGORITHM

```python
# agent/integrity_agent.py — SEPARATE PROCESS, does not import core

CORE_FILES_GLOB = "/opt/selena-core/core/**/*.py"
MANIFEST_PATH   = "/secure/core.manifest"
MASTER_HASH     = "/secure/master.hash"
BACKUP_DIR      = "/secure/core_backup/v0.3.0/"
LOG_PATH        = "/var/log/selena/integrity.log"

async def check_loop():
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SEC)  # 30 sec
        await run_check()

async def run_check():
    # 1. Verify the manifest itself
    manifest_hash = sha256_file(MANIFEST_PATH)
    stored_hash   = Path(MASTER_HASH).read_text().strip()
    if manifest_hash != stored_hash:
        await trigger_response("manifest_tampered", [MANIFEST_PATH])
        return

    # 2. Check each core file
    manifest = json.loads(Path(MANIFEST_PATH).read_text())
    changed  = []
    for path, expected_hash in manifest.items():
        actual = sha256_file(path)
        if actual != expected_hash:
            changed.append({"path": path, "expected": expected_hash, "actual": actual})

    if changed:
        await trigger_response("files_changed", changed)

async def trigger_response(reason: str, changed: list):
    # Step 1: log
    log_incident(reason, changed)

    # Step 2: stop modules
    await stop_all_modules()

    # Step 3: notify platform
    await notify_platform(reason, changed)

    # Step 4: rollback (3 attempts)
    for attempt in range(1, 4):
        success = await restore_from_backup(changed)
        if success:
            await restart_core()
            await notify_platform_restored()
            return
        await asyncio.sleep(5)

    # Step 5: SAFE MODE if rollback failed
    await enter_safe_mode()
    await notify_platform_safe_mode()
```

---

## 8. AUDIO SUBSYSTEM — IMPLEMENTATION

### Device auto-detection

```python
# system_modules/voice_core/audio_manager.py

PRIORITY_INPUT  = ["usb", "i2s_gpio", "bluetooth", "hdmi", "builtin"]
PRIORITY_OUTPUT = ["usb", "i2s_gpio", "bluetooth", "hdmi", "jack"]

def detect_audio_devices() -> AudioDevices:
    devices = AudioDevices(inputs=[], outputs=[])

    # ALSA — all cards from /proc/asound/cards
    for card in parse_alsa_cards():
        dtype = classify_card(card)  # usb | i2s_gpio | hdmi | jack | builtin
        if has_capture(card):
            devices.inputs.append(AudioDevice(id=card.alsa_id, name=card.name, type=dtype))
        if has_playback(card):
            devices.outputs.append(AudioDevice(id=card.alsa_id, name=card.name, type=dtype))

    # PulseAudio / PipeWire — BT devices
    if is_pulse_running():
        for sink in pactl_list_sinks():
            if "bluez" in sink.name:
                devices.outputs.append(AudioDevice(
                    id=sink.name, name=sink.description, type="bluetooth"
                ))
        for source in pactl_list_sources():
            if "bluez" in source.name:
                devices.inputs.append(AudioDevice(
                    id=source.name, name=source.description, type="bluetooth"
                ))

    # Sort by priority
    devices.inputs.sort(key=lambda d: priority_score(d.type, PRIORITY_INPUT))
    devices.outputs.sort(key=lambda d: priority_score(d.type, PRIORITY_OUTPUT))

    return devices
```

### I2S GPIO microphone (INMP441 / SPH0645)

```bash
# /boot/config.txt — add overlay
dtoverlay=googlevoicehat-soundcard   # for INMP441 on GPIO 18-21
# OR
dtoverlay=i2s-mmap

# After reboot — verify:
arecord -l
# **** List of CAPTURE Hardware Devices ****
# card 1: sndrpisimplecar [snd_rpi_simple_card], device 0: ...
```

### Bluetooth pairing via API

```python
# POST /api/v1/system/bluetooth/pair
# Launches bluetoothctl scan + pair + trust + connect

async def pair_bluetooth_device(mac: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    commands = [
        f"pair {mac}\n",
        f"trust {mac}\n",
        f"connect {mac}\n",
        "quit\n",
    ]
    stdout, _ = await proc.communicate(
        input="".join(commands).encode()
    )
    return proc.returncode == 0
```

---

## 9. OAUTH VIA QR — IMPLEMENTATION

```python
# system_modules/secrets_vault/oauth_flow.py
# RFC 8628 — Device Authorization Grant

PROVIDERS = {
    "google": {
        "device_auth_url": "https://oauth2.googleapis.com/device/code",
        "token_url":       "https://oauth2.googleapis.com/token",
        "client_id":       env("GOOGLE_CLIENT_ID"),
        "client_secret":   env("GOOGLE_CLIENT_SECRET"),
    },
    "tuya": {
        "device_auth_url": "https://auth.tuya.com/oauth/device/code",
        "token_url":       "https://auth.tuya.com/oauth/token",
        "client_id":       env("TUYA_CLIENT_ID"),
        "client_secret":   env("TUYA_CLIENT_SECRET"),
    },
}

async def start_oauth_flow(module: str, provider: str, scopes: list[str]) -> OAuthSession:
    cfg = PROVIDERS[provider]

    # Step 1: request device_code
    resp = await http.post(cfg["device_auth_url"], data={
        "client_id": cfg["client_id"],
        "scope": " ".join(scopes),
    })
    data = resp.json()
    # data: { device_code, user_code, verification_uri, interval, expires_in }

    # Step 2: generate QR
    qr_url = f"{data['verification_uri']}?user_code={data['user_code']}"
    qr_img = generate_qr(qr_url)

    # Step 3: save session + start polling
    session = OAuthSession(module=module, provider=provider,
                           device_code=data["device_code"],
                           interval=data["interval"])
    asyncio.create_task(poll_for_token(session, cfg))

    return session

async def poll_for_token(session: OAuthSession, cfg: dict):
    while not session.expired:
        await asyncio.sleep(session.interval)
        resp = await http.post(cfg["token_url"], data={
            "client_id":     cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "device_code":   session.device_code,
            "grant_type":    "urn:ietf:params:oauth:grant-type:device_code",
        })
        if resp.status_code == 200:
            tokens = resp.json()
            # Encrypt and save
            await vault.store(session.module, tokens)
            session.status = "authorized"
            return
        elif resp.json().get("error") == "authorization_pending":
            continue
        else:
            session.status = "error"
            return
```

---

## 10. BREAKING DOWN THE SPEC INTO TASKS AND GITHUB ISSUES

> Before starting work, the agent MUST create all Issues on GitHub following this plan.
> Repository: **https://github.com/dotradepro/SelenaCore**

### Phase 1 — Project Initialization

| Issue | Title | Labels |
|-------|-------|--------|
| #1 | `chore: init project structure, Dockerfile, docker-compose` | `phase-1`, `chore`, `infra` |
| #2 | `chore: setup SQLite + SQLAlchemy models (Device, AuditLog)` | `phase-1`, `chore`, `backend` |
| #3 | `chore: setup FastAPI skeleton, health endpoint, middleware` | `phase-1`, `chore`, `backend` |
| #4 | `chore: systemd units + watchdog configuration` | `phase-1`, `chore`, `infra` |

### Phase 2 — Core (Core API)

| Issue | Title | Labels |
|-------|-------|--------|
| #5 | `feat(registry): Device Registry CRUD + state history` | `phase-2`, `feat`, `backend` |
| #6 | `feat(eventbus): Event Bus asyncio.Queue + webhook delivery` | `phase-2`, `feat`, `backend` |
| #7 | `feat(api): Core API /devices endpoints + module_token auth` | `phase-2`, `feat`, `backend` |
| #8 | `feat(api): Core API /events endpoints + core.* protection` | `phase-2`, `feat`, `backend` |
| #9 | `feat(loader): Plugin Manager + manifest validation` | `phase-2`, `feat`, `backend` |
| #10 | `feat(loader): Module install/start/stop via Docker sandbox` | `phase-2`, `feat`, `backend` |
| #11 | `feat(api): Module Loader API /modules endpoints + SSE status` | `phase-2`, `feat`, `backend` |

### Phase 3 — Integrity Agent

| Issue | Title | Labels |
|-------|-------|--------|
| #12 | `feat(agent): SHA256 manifest creation on first init` | `phase-3`, `feat`, `security` |
| #13 | `feat(agent): periodic file check loop (30s interval)` | `phase-3`, `feat`, `security` |
| #14 | `feat(agent): response chain: stop modules → notify → restore` | `phase-3`, `feat`, `security` |
| #15 | `feat(agent): SAFE MODE — read-only Core API, no module start` | `phase-3`, `feat`, `security` |

### Phase 4 — Cloud Sync

| Issue | Title | Labels |
|-------|-------|--------|
| #16 | `feat(sync): heartbeat ping to SmartHome LK platform` | `phase-4`, `feat`, `backend` |
| #17 | `feat(sync): long-poll command receiver + ACK` | `phase-4`, `feat`, `backend` |
| #18 | `feat(sync): handle INSTALL_MODULE, STOP_MODULE, REBOOT commands` | `phase-4`, `feat`, `backend` |
| #19 | `feat(sync): integrity event reporting to platform` | `phase-4`, `feat`, `security` |

### Phase 5 — UI Core + Onboarding

| Issue | Title | Labels |
|-------|-------|--------|
| #20 | `feat(ui): FastAPI server :80 + static files + PWA manifest` | `phase-5`, `feat`, `frontend` |
| #21 | `feat(ui): AP mode + QR code generation on first boot` | `phase-5`, `feat`, `frontend` |
| #22 | `feat(ui): wizard endpoints (9 steps: wifi→import)` | `phase-5`, `feat`, `frontend` |
| #23 | `feat(ui): dashboard page + device list + module list` | `phase-5`, `feat`, `frontend` |
| #24 | `feat(ui): display mode autodetect (headless/kiosk/framebuffer/tty)` | `phase-5`, `feat`, `infra` |
| #25 | `feat(ui): TTY1 Textual TUI status display` | `phase-5`, `feat`, `frontend` |
| #26 | `feat(ui): Service Worker + offline page (PWA)` | `phase-5`, `feat`, `frontend` |

### Phase 6 — System Modules: Audio and Voice

| Issue | Title | Labels |
|-------|-------|--------|
| #27 | `feat(voice): audio device autodetect (USB/I2S/BT/HDMI/jack)` | `phase-6`, `feat`, `voice` |
| #28 | `feat(voice): Vosk STT wrapper + streaming` | `phase-6`, `feat`, `voice` |
| #29 | `feat(voice): Piper TTS wrapper + voice selection` | `phase-6`, `feat`, `voice` |
| #30 | `feat(voice): Vosk grammar wake word + background loop` | `phase-6`, `feat`, `voice` |
| #31 | `feat(voice): resemblyzer Speaker ID + enrollment flow` | `phase-6`, `feat`, `voice` |
| #32 | `feat(voice): privacy mode (GPIO button + voice command)` | `phase-6`, `feat`, `voice` |
| #33 | `feat(voice): WebRTC audio stream from browser → Whisper` | `phase-6`, `feat`, `voice` |
| #34 | `feat(voice): voice history storage in SQLite` | `phase-6`, `feat`, `voice` |

### Phase 7 — LLM and Intent Router

| Issue | Title | Labels |
|-------|-------|--------|
| #35 | `feat(llm): Fast Matcher (keyword/regex rules YAML config)` | `phase-7`, `feat`, `llm` |
| #36 | `feat(llm): Ollama client + phi-3-mini/gemma-2b support` | `phase-7`, `feat`, `llm` |
| #37 | `feat(llm): dynamic system prompt with module registry` | `phase-7`, `feat`, `llm` |
| #38 | `feat(llm): Intent Router orchestration (Fast → LLM fallback)` | `phase-7`, `feat`, `llm` |
| #39 | `feat(llm): model manager (download/select/switch)` | `phase-7`, `feat`, `llm` |
| #40 | `feat(llm): auto-disable LLM when RAM < 5GB` | `phase-7`, `feat`, `llm` |

### Phase 8 — Users and Security

| Issue | Title | Labels |
|-------|-------|--------|
| #41 | `feat(users): user profiles CRUD (admin/resident/guest roles)` | `phase-8`, `feat`, `security` |
| #42 | `feat(users): PIN auth + rate limiting (5 attempts → 10 min lock)` | `phase-8`, `feat`, `security` |
| #43 | `feat(users): Face ID enrollment + browser webcam auth flow` | `phase-8`, `feat`, `security` |
| #44 | `feat(users): audit log (SQLite, 10k records rotation)` | `phase-8`, `feat`, `security` |
| #45 | `feat(security): self-signed HTTPS certificate generation` | `phase-8`, `feat`, `security` |
| #46 | `feat(security): iptables rules setup script` | `phase-8`, `feat`, `security` |
| #47 | `feat(security): Tailscale integration (remote-access module)` | `phase-8`, `feat`, `security` |

### Phase 9 — Secrets Vault and OAuth

| Issue | Title | Labels |
|-------|-------|--------|
| #48 | `feat(vault): AES-256-GCM secrets storage in /secure/tokens/` | `phase-9`, `feat`, `security` |
| #49 | `feat(vault): OAuth Device Authorization Grant flow (RFC 8628)` | `phase-9`, `feat`, `backend` |
| #50 | `feat(vault): API proxy endpoint (no token exposure to modules)` | `phase-9`, `feat`, `security` |
| #51 | `feat(vault): token auto-refresh (5 min before expiry)` | `phase-9`, `feat`, `backend` |

### Phase 10 — Network Scanner and Import

| Issue | Title | Labels |
|-------|-------|--------|
| #52 | `feat(scanner): ARP sweep (passive + on-demand)` | `phase-10`, `feat`, `backend` |
| #53 | `feat(scanner): mDNS/Bonjour listener` | `phase-10`, `feat`, `backend` |
| #54 | `feat(scanner): SSDP/UPnP listener` | `phase-10`, `feat`, `backend` |
| #55 | `feat(scanner): OUI database lookup (manufacturer detection)` | `phase-10`, `feat`, `backend` |
| #56 | `feat(import): Home Assistant import adapter + ha-bridge module` | `phase-10`, `feat`, `backend` |
| #57 | `feat(import): Tuya import adapter + tuya-bridge module` | `phase-10`, `feat`, `backend` |
| #58 | `feat(import): Philips Hue local API adapter` | `phase-10`, `feat`, `backend` |

### Phase 11 — Monitoring, Notifications, Backup

| Issue | Title | Labels |
|-------|-------|--------|
| #59 | `feat(monitor): CPU temp + RAM + disk monitoring + alerts` | `phase-11`, `feat`, `infra` |
| #60 | `feat(monitor): RAM degradation strategy (auto-stop by priority)` | `phase-11`, `feat`, `infra` |
| #61 | `feat(notify): Web Push VAPID implementation` | `phase-11`, `feat`, `backend` |
| #62 | `feat(backup): local USB/SD backup + restore` | `phase-11`, `feat`, `backend` |
| #63 | `feat(backup): E2E cloud backup (PBKDF2 + AES-256-GCM)` | `phase-11`, `feat`, `security` |
| #64 | `feat(backup): QR secrets transfer between devices` | `phase-11`, `feat`, `security` |

### Phase 12 — SDK and Tests

| Issue | Title | Labels |
|-------|-------|--------|
| #65 | `feat(sdk): SmartHomeModule base class + decorators` | `phase-12`, `feat`, `sdk` |
| #66 | `feat(sdk): smarthome CLI (new-module / dev / test / publish)` | `phase-12`, `feat`, `sdk` |
| #67 | `feat(sdk): mock Core API for local development` | `phase-12`, `feat`, `sdk` |
| #68 | `test: registry, eventbus, module_loader, integrity` | `phase-12`, `test`, `backend` |
| #69 | `test: Core API endpoints + auth + rate limiting` | `phase-12`, `test`, `backend` |
| #70 | `test: wizard flow + onboarding` | `phase-12`, `test`, `frontend` |
| #71 | `docs: README, CONTRIBUTING, module development guide` | `phase-12`, `docs` |

---

## 11. GIT WORKFLOW

### Branches

- Work in **`main`** for tasks under 200 lines
- Task > 200 lines: branch `feat/<issue-number>-<slug>`

```bash
git checkout -b feat/5-device-registry   # for Issue #5
git checkout main                        # return
git merge feat/5-device-registry         # merge
git push origin main
```

### Commit messages (Conventional Commits)

Format: `<type>(<scope>): <description> [#<N>]`

| Type | When |
|------|------|
| `feat` | new feature |
| `fix` | bug fix |
| `chore` | setup, dependencies, configs |
| `refactor` | refactoring without behavior change |
| `test` | tests |
| `docs` | documentation |
| `security` | vulnerability fix |
| `perf` | performance optimization |

```bash
# ✅ Correct
git commit -m "feat(registry): add Device Registry CRUD with state history [#5]"
git commit -m "fix(agent): handle missing manifest file on first init [#12]"
git commit -m "test(registry): add pytest for state_changed event emission [#68]"
git commit -m "chore: add docker-compose.yml with core+modules+sandbox [#1]"

# ❌ Wrong
git commit -m "fix"
git commit -m "update code"
git commit -m "wip"
git commit -m "."
```

### When to commit

Atomic steps — each commit should leave the project in a working state:

```
Data model created             → commit
Service written                → commit
Router added                   → commit
Test written                   → commit
Test passed                    → commit + push
```

### Push to main

```bash
# Before each push — verify:
pytest tests/ -x -q                    # all tests green
python -m mypy core/ --ignore-missing  # type checking

git push origin main
```

### Deploy to container (MANDATORY after every push)

> Python source code (core/, system_modules/, agent/, tests/) is mounted into the container via volume mounts.
> After changing Python files, restarting the container is sufficient.
> `docker cp` is NOT needed for these directories.

```bash
# 1. Rebuild frontend
npx vite build

# 2. Copy built static files into container
docker cp system_modules/ui_core/static/. selena-core:/opt/selena-core/system_modules/ui_core/static/

# 3. Update .version (PATCH from commit count, COMMIT from HEAD)
python3 -c "
import subprocess, pathlib
MAJOR, MINOR, LABEL = 0, 3, 'beta'
patch = subprocess.check_output(['git','rev-list','--count','HEAD']).decode().strip()
commit = subprocess.check_output(['git','rev-parse','--short','HEAD']).decode().strip()
v = f'{MAJOR}.{MINOR}.{patch}'
if LABEL: v += f'-{LABEL}'
if commit: v += f'+{commit}'
pathlib.Path('.version').write_text(v)
print(f'[version] {v}')
"
docker cp .version selena-core:/opt/selena-core/.version

# 4. Restart container (Python code is picked up via volume mounts)
docker restart selena-core

# 5. Verify everything works
sleep 3
curl -s http://localhost:7070/api/v1/health | python3 -m json.tool
curl -s -o /dev/null -w "UI :80 → HTTP %{http_code}\n" http://localhost:80/

# 6. Refresh device screen (kiosk Chromium)
sudo XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0 wtype -k F5
```

> **Why step 5 is mandatory:** The device screen is Chromium in kiosk mode
> inside the Wayland compositor `cage` (systemd: `smarthome-display.service`).
> `docker restart` restarts the backend and UI server, but does NOT reload the browser.
> Chromium with `--disable-background-networking` flags caches the old page.
> The `wtype -k F5` command sends an F5 keypress via the Wayland protocol to Chromium.
>
> If `wtype` is unavailable or not working — alternative:
> `sudo systemctl restart smarthome-display.service`
> (restarts cage + chromium completely, slower but more reliable)

**What gets updated:**

| Step | What is updated | Where visible |
|------|-----------------|---------------|
| `npx vite build` | Frontend (React SPA) | — |
| `docker cp static/` | UI in container | Browser `:80` |
| `.version` + `docker cp` | Build version | API + UI |
| `docker restart` | FastAPI + UI restart | Server |
| `wtype -k F5` | Page refresh in kiosk | Device screen |

**Rules:**

- If changes are only in `src/` (frontend) — steps 1, 2, 3, 4, 5, 6
- If changes are only in `core/` (backend) — steps 3, 4, 5, 6 (volume mount — automatic)
- If changes are in both — all 6 steps
- ⛔ A task cannot be considered complete without verifying `curl` on step 5
- ⛔ A task cannot be considered complete without refreshing the device screen (step 6)

---

## 12. WORKING WITH GITHUB ISSUES

### Task workflow algorithm

```
1. Check that Issue exists on GitHub
   → If not — CREATE per the plan from section 10
   → gh issue create --title "feat(registry): ..." --label "phase-2,feat,backend"

2. Add label "in-progress"
   → gh issue edit <N> --add-label "in-progress"

3. Read Issue completely + related TZ.md sections

4. Plan execution steps (write list in Issue comment)

5. Execute steps, committing each atomic step with [#N]

6. Write final comment:
   → "✅ Done. Commits: abc1234, def5678, ghi9012"

7. Close Issue:
   → gh issue close <N>

8. Remove label "in-progress"
```

### Creating Issue via gh CLI

```bash
gh issue create \
  --repo dotradepro/SelenaCore \
  --title "feat(registry): Device Registry CRUD + state history" \
  --body "## Task
Implement Device Registry with full CRUD and state history storage.

## Read before starting
- docs/TZ.md section 2 (Device Registry)
- AGENTS.md section 4.2 (API specification)

## Acceptance criteria
- [ ] POST /api/v1/devices — device creation
- [ ] GET /api/v1/devices — list all devices
- [ ] GET /api/v1/devices/{id} — specific device
- [ ] PATCH /api/v1/devices/{id}/state — state update
- [ ] DELETE /api/v1/devices/{id} — deletion
- [ ] History: last 1000 states in SQLite
- [ ] Publish device.state_changed to Event Bus
- [ ] pytest test_registry.py → 0 failed" \
  --label "phase-2,feat,backend"
```

### Project labels

```
phase-1 … phase-12    implementation phase
feat / fix / chore / refactor / test / docs / security / perf
backend / frontend / infra / voice / llm / sdk
in-progress / blocked / needs-review
```

---

## 13. CRITICAL PROHIBITIONS

```
⛔ Starting code without creating a GitHub Issue
⛔ Taking a second task while the first one is not closed
⛔ Pushing to main with failing tests
⛔ Empty except: pass — always log the error
⛔ print() — only logging.getLogger(__name__)
⛔ Storing secrets in .env in plain text (only .env.example)
⛔ Reading /secure from a module directly (only through secrets-vault API)
⛔ Publishing core.* events from a module (403 at API level)
⛔ Returning OAuth token to a module directly (only through proxy)
⛔ Biometrics in any outgoing HTTP requests
⛔ shell=True in subprocess without absolute necessity
⛔ eval() / exec() in any code
⛔ Modifying core files without updating core.manifest
⛔ Commit with message "fix", "update", "wip", "."
⛔ Creating virtualenv / venv inside a Docker container (dependencies are installed globally via pip)
⛔ Using docker cp to update core/ or system_modules/ (volume mounts are used)
⛔ Running system modules as separate processes/containers with ports (only in-process via importlib)
⛔ Specifying "port" in manifest.json for SYSTEM modules (ports are only for user modules)
⛔ Using httpx/HTTP for communication between system module and core (only direct Python calls)
⛔ Hardcoding localhost:PORT in HTML widgets (use window.location.pathname for BASE URL)
⛔ Hardcoding UI text in widget.html / settings.html without localization via var L = {en:{...}, uk:{...}} / t('key') / data-i18n
```

---

## 14. TESTING

### Test structure

```python
# tests/test_registry.py

import pytest
from httpx import AsyncClient
from core.main import app

@pytest.fixture
async def client():
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c

@pytest.fixture
def module_token(client):
    # Get test module token
    return "test-module-token-xyz"

async def test_create_device(client, module_token):
    resp = await client.post("/api/v1/devices",
        headers={"Authorization": f"Bearer {module_token}"},
        json={
            "name": "Test Sensor",
            "type": "sensor",
            "protocol": "mqtt",
            "capabilities": ["read_temperature"],
        }
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Sensor"
    assert data["device_id"] is not None

async def test_state_changed_event(client, module_token, event_bus):
    # Create device
    resp = await client.post("/api/v1/devices", ...)
    device_id = resp.json()["device_id"]

    # Update state
    await client.patch(f"/api/v1/devices/{device_id}/state",
        headers={"Authorization": f"Bearer {module_token}"},
        json={"state": {"temperature": 22.5}}
    )

    # Verify event was published
    event = await event_bus.get_last_event("device.state_changed")
    assert event["payload"]["device_id"] == device_id
    assert event["payload"]["new_state"]["temperature"] == 22.5

async def test_core_event_forbidden(client, module_token):
    resp = await client.post("/api/v1/events/publish",
        headers={"Authorization": f"Bearer {module_token}"},
        json={
            "type": "core.integrity_violation",  # forbidden
            "source": "evil-module",
            "payload": {}
        }
    )
    assert resp.status_code == 403
```

### Running tests

```bash
# All tests
pytest tests/ -v

# Specific file
pytest tests/test_registry.py -v

# With coverage
pytest tests/ --cov=core --cov-report=term-missing

# Stop on first failure
pytest tests/ -x
```

---

## 15. ENVIRONMENT VARIABLES (.env.example)

```bash
# SmartHome LK Platform
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=                    # filled during registration
# API key is stored in /secure/platform.key — not in .env!

# Core API
CORE_PORT=7070
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO                       # DEBUG | INFO | WARNING | ERROR

# UI
UI_PORT=80
UI_HTTPS=true

# Integrity Agent
AGENT_CHECK_INTERVAL=30                   # seconds
AGENT_MAX_RESTORE_ATTEMPTS=3

# Docker
DOCKER_SOCKET=/var/run/docker.sock
MODULE_CONTAINER_IMAGE=smarthome-modules:latest
SANDBOX_IMAGE=smarthome-sandbox:latest

# Audio (auto-detection override)
AUDIO_FORCE_INPUT=                        # or "hw:2,0"
AUDIO_FORCE_OUTPUT=                       # or "bluez_sink.AA_BB_CC"

# OAuth providers
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
TUYA_CLIENT_ID=
TUYA_CLIENT_SECRET=

# Tailscale
TAILSCALE_AUTH_KEY=                       # tskey-auth-...

# Development mode
DEBUG=false
MOCK_PLATFORM=false                       # for local development without platform
```

---

## 16. SERVER SECURITY AND DOCKER

### 16.1 Port Isolation (MOST IMPORTANT)

Docker by default **ignores UFW** and directly opens ports via iptables.
Databases and internal services **must never** be accessible from outside.

```yaml
# ❌ Wrong — open to the entire internet
services:
  redis:
    ports:
      - "6379:6379"
  postgres:
    ports:
      - "5432:5432"

# ✅ Correct — accessible only within the server
services:
  redis:
    command: redis-server --requirepass STRONG_PASSWORD
    ports:
      - "127.0.0.1:6379:6379"
  postgres:
    environment:
      POSTGRES_PASSWORD: STRONG_PASSWORD
    ports:
      - "127.0.0.1:5432:5432"
```

> Containers within the same `docker-compose` still communicate with each other by service names (`redis:6379`) — external port mapping is not needed for them.

**For SelenaCore** — in `docker-compose.yml` Core API ports (7070) and UI (80) should be bound to `127.0.0.1` if external access is not required.

### 16.2 UFW Setup (System Firewall)

```bash
sudo ufw default deny incoming   # deny all incoming by default
sudo ufw default allow outgoing  # allow server to access the internet
sudo ufw allow 22/tcp            # SSH — mandatory, otherwise you lose access!
sudo ufw allow 80/tcp            # HTTP
sudo ufw allow 443/tcp           # HTTPS
sudo ufw enable                  # enable
sudo ufw status verbose          # check status
```

### 16.3 No dev mode in production

`npm run dev` is insecure: high memory usage, slow, opens debug ports (RCE possible).

```bash
# ✅ Correct for Node.js / Next.js / Vite
npm run build
npm run start

# ✅ In Dockerfile
RUN npm run build
CMD ["npm", "run", "start"]
```

In SelenaCore `npx vite build` — only for building static files. Built files are served through FastAPI `StaticFiles`. In the production container `npm` is never launched.

### 16.4 SSH Protection

```bash
# Check authorized_keys for unauthorized keys
cat ~/.ssh/authorized_keys

# If foreign keys are found — remove them
nano ~/.ssh/authorized_keys

# Change root password
passwd root
```

### 16.5 Regular Checks

```bash
# System load
htop

# Container stats (CPU / RAM)
docker stats

# System updates (closes vulnerabilities)
sudo apt update && sudo apt upgrade -y

# Check open ports
ss -tlnp
```

### 16.6 Rules for the agent

```
⛔ Do not map DB ports without 127.0.0.1: prefix
⛔ Do not run npm run dev in production container
⛔ Do not store DB passwords in plain text (only through .env / secrets)
⛔ Do not add SSH keys to authorized_keys without explicit user request
✅ All new services in docker-compose — verify port bindings
✅ After changing docker-compose — check sudo ufw status
```

---

## 17. SYSTEM MODULE ARCHITECTURE (CRITICALLY IMPORTANT)

> **System modules (type: SYSTEM) run IN-PROCESS inside the smarthome-core container.**
> **Separate Docker containers / subprocess / ports — ONLY for user modules.**
> **This saves ~580 MB RAM on Raspberry Pi.**

### 17.1 Module Classification

| Type | Execution | Port | Communication with core | Container |
|------|-----------|------|-------------------------|-----------|
| **SYSTEM** | importlib in core process | ❌ none | Direct Python calls | smarthome-core (single) |
| **UI/INTEGRATION/DRIVER/AUTOMATION** | Docker sandbox | ✅ 8100-8200 | HTTP API + webhooks | smarthome-modules |

### 17.2 SystemModule Base Class

```python
# core/module_loader/system_module.py — ALL system modules inherit from this

class SystemModule(ABC):
    name: str  # must match manifest.json "name"

    async def setup(bus, session_factory):  # called by loader
    async def start():                       # abstract — startup
    async def stop():                        # abstract — shutdown
    def get_router() -> APIRouter | None:    # REST endpoints

    # Instead of httpx → direct access:
    self.publish(event_type, payload)        # → EventBus directly
    self.subscribe(event_types, callback)    # → DirectSubscription (without webhook)
    self.fetch_devices()                     # → SQLAlchemy session
    self.patch_device_state(device_id, state)
    self.register_device(...)
```

### 17.3 System Module File Structure

```
system_modules/weather_service/
    __init__.py            # from .module import WeatherServiceModule as module_class
    module.py              # WeatherServiceModule(SystemModule) — entry point
    weather.py             # WeatherService — business logic (existing code)
    manifest.json          # type: SYSTEM, WITHOUT "port" field
    widget.html            # widget (iframe, BASE from pathname)
    settings.html          # settings (iframe, BASE from pathname)
```

### 17.4 manifest.json — rules for SYSTEM modules

```json
{
    "name": "weather-service",
    "type": "SYSTEM",
    "runtime_mode": "always_on",
    // ⛔ NO "port" field — SYSTEM modules do not listen on a port
    "permissions": ["events.publish"]
}
```

### 17.5 EventBus — two delivery methods

```python
# 1. DirectSubscription (for SYSTEM modules — in-process)
self.subscribe(["device.state_changed"], self._on_event)
# → EventBus calls callback directly via asyncio.create_task()

# 2. Webhook (for user modules — via HTTP)
POST /api/v1/events/subscribe { webhook_url: "http://localhost:8100/webhook" }
# → EventBus makes HTTP POST to webhook_url
```

### 17.6 System Module API Routing

System module routers are mounted in the core FastAPI app:

```
GET /api/ui/modules/weather-service/weather/current
GET /api/ui/modules/automation-engine/rules
POST /api/ui/modules/scheduler/jobs
```

Widgets are loaded via iframe:
```html
<iframe src="/api/ui/modules/weather-service/widget.html" />
```

### 17.7 BASE URL in HTML widgets

```javascript
// ✅ Correct — computed from iframe URL
const BASE = window.location.pathname.replace(/\/(widget|settings)(\.html)?$/, '');
fetch(BASE + '/weather/current')

// ❌ Wrong
const BASE = "http://localhost:8115";    // hardcoded port
const base = window.location.origin;     // doesn't account for prefix
fetch('/status');                         // without prefix
```

### 17.8 Module Loading (sandbox.py)

```python
# sandbox.py → _start_in_process()
import importlib
mod = importlib.import_module(f"system_modules.{dir_name}")
cls = mod.module_class           # exported from __init__.py
instance = cls()
await instance.setup(bus, session_factory)
await instance.start()
router = instance.get_router()   # mounted in app
```

---

## 18. PRESENCE DETECTION — DETECTION ALGORITHM

> Proper presence = Layer 2 ARP, not ping!
> Router keeps DHCP leases long after departure. ARP table has STALE status even after phone is turned off. ICMP/TCP ping is blocked by firewalls and sleep mode.

### 18.1 How It Works

**Why ARP (Layer 2):**
- Any device on Wi-Fi **must** respond to ARP requests — otherwise it drops off the network
- Works even when the screen is locked, phone is in pocket
- Instant result: ~1.9 sec for the entire /24 segment

**Why NOT to use:**
- `ping` — phones block ICMP, firewalls block TCP
- passive `/proc/net/arp` — contains STALE entries (devices left, but entry remains)
- DHCP leases — stored for hours after device departure

### 18.2 Detector Strategy (priority order)

```
1. arp-scan --localnet (L2, active)  ← PREFERRED
   → Sends Ethernet ARP broadcast, listens for responses
   → Runs ONCE per scan cycle (not per-device)
   → Returns set of active MACs in 1.9 sec

2. ip neigh + ping (L3 fallback)       ← if arp-scan unavailable
   → ping -c 1 -W 1 <ip>  (triggers kernel ARP resolution)
   → ip neigh show <ip>    (check status)
   → Accept:   REACHABLE | DELAY | PROBE
   → Reject:   STALE | FAILED | empty string

3. Bluetooth BLE                       ← for BT devices
```

### 18.3 "Away" Timeout (Consider Away — 5 minutes)

Modern iPhone/Android devices enter **Deep Sleep** and can disable Wi-Fi for 2-4 minutes to save battery. If scanning every 60 seconds — the system will falsely consider the owner "away".

**Rule:** do not transition to `away` instantly — wait `away_threshold_sec` (default **300 s**).

```
Device appeared  → immediately "home"   (no delay)
Device vanished  → wait 5 minutes → if not returned → "away"
```

```python
# PresenceDetector defaults
scan_interval_sec  = 60    # scan every 60 seconds
away_threshold_sec = 300   # 5 minutes wait before "away"
# Override via env:
# PRESENCE_SCAN_INTERVAL=60
# PRESENCE_AWAY_THRESHOLD=300
```

### 18.4 Installing arp-scan (Dockerfile.core)

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    arp-scan \
    arping \
    ...
```

Verify in container:
```bash
docker exec selena-core arp-scan --localnet -q
# → list of live devices in ~2 sec
```

### 18.5 Status API

```http
GET /api/ui/modules/presence-detection/status

Response 200:
{
  "detection_method": "arp-scan (L2)",   // or "ip-neigh (L3)"
  "arp_scan_available": true,
  "scan_interval_sec": 60,
  "away_threshold_sec": 300,
  ...
}
```

### 18.6 Critical Rules

```
✅ arp-scan runs ONCE per cycle (_scan_all), result is cached in _arp_scan_cache
✅ Each MAC is checked via O(1) lookup in set — not N×arp-scan calls
✅ away_threshold_sec = 300 (5 min) — protection against phone Deep Sleep phase
⛔ Do not use ping_ip() for presence detection — phones block it
⛔ Do not rely solely on /proc/net/arp — it contains STALE entries of dead devices
⛔ STALE status in ip neigh is NOT confirmation of presence — only REACHABLE/DELAY/PROBE
```

---

## 19. BUILD AND RUN

### Docker Compose — containers

The project is launched via `docker compose` from the repository root.
Two containers: `selena-core` (core + UI) and `selena-agent` (Integrity Agent).

```bash
# Container status
docker compose ps

# Restart all containers (after changes in Python/config/system_modules)
docker compose restart

# Restart core only
docker compose restart core

# Stop
docker compose down

# Launch (with image rebuild if Dockerfile/requirements.txt changed)
docker compose up -d --build

# Launch without rebuild
docker compose up -d

# Logs (live)
docker compose logs -f core
docker compose logs -f agent

# Last 100 log lines
docker compose logs --tail=100 core
```

### Frontend (React/Vite)

UI is built by Vite and served from `system_modules/ui_core/static/`.
Source files: `src/`, config: `vite.config.ts`.

```bash
# Build frontend (mandatory after changes in src/)
npx vite build

# After build — restart container to pick up new bundle
docker compose restart core
```

⛔ **Important:** changes in `src/` (React components, i18n, store) **are not applied**
until rebuild with `npx vite build`. Restarting the container without rebuild
will show the old frontend.

✅ Changes in `system_modules/` (Python, HTML widgets/settings) are picked up
on container restart — volume-mount provides live-sync.

### Typical Scenarios

| What changed | What to do |
|---|---|
| Python code in `core/` or `system_modules/` | `docker compose restart core` |
| HTML widgets/settings of modules | `docker compose restart core` |
| React components (`src/`) | `npx vite build && docker compose restart core` |
| `requirements.txt` / `Dockerfile.core` | `docker compose up -d --build` |
| `docker-compose.yml` / `.env` | `docker compose up -d` |
| `config/core.yaml` | `docker compose restart core` |
| Integrity Agent (`agent/`) | `docker compose restart agent` |

---

## 20. INTENT SYSTEM — ARCHITECTURE AND PROTOCOL

> **Intent System** — the mechanism through which user voice and text commands
> are routed to the appropriate module. Any module (system or user)
> can register its intents and receive commands through EventBus.

### 20.1 Multi-tier Router (IntentRouter)

```
User → STT → text
    │
    ▼
Tier 1:   FastMatcher — keyword/regex rules from YAML (~0 ms)
    │ [no match]
    ▼
Tier 1.5: System Module Intents — in-process regex (microseconds)
    │ [no match]
    ▼
Tier 2:   User Module Intents — HTTP to container (milliseconds)
    │ [no match]
    ▼
Tier 3:   LLM fallback — Ollama/Cloud (seconds, disabled when RAM < 5 GB)
    │ [no match]
    ▼
Fallback: "Sorry, I didn't understand" (in STT model language)
```

**File:** `system_modules/llm_engine/intent_router.py`

### 20.2 IntentResult — Router Response Structure

```python
@dataclass
class IntentResult:
    intent: str                          # "media.play_genre", "turn_on_light", "llm.response"
    response: str                        # text for TTS (empty for system_module)
    action: dict[str, Any] | None        # structured action
    source: str                          # "fast_matcher" | "system_module" | "module_intent" | "llm" | "fallback"
    latency_ms: int                      # processing time
    user_id: str | None = None           # speaker ID (speaker_id)
    params: dict[str, Any] | None = None # extracted parameters from regex named groups
```

### 20.3 Tier 1 — FastMatcher (YAML rules)

**File:** `system_modules/llm_engine/fast_matcher.py`
**Config:** `/opt/selena-core/config/intent_rules.yaml`

Rules — simple keyword/regex matches. No LLM, no HTTP. Works in microseconds.

```yaml
# /opt/selena-core/config/intent_rules.yaml
intents:
  - name: "turn_on_light"
    keywords: ["turn on light", "switch on light", "увімкни світло"]
    regex: ["turn on .*(light|lamp)", "увімкни .*(світло|лампу)"]
    response: "fast_matcher.light_on"
    action:
      type: "device.update_state"
      state: { on: true }

  - name: "media.pause"
    keywords: ["pause", "пауза", "на паузу"]
    response: ""  # empty — module will respond via TTS itself
```

**Parameter extraction:** regex with named groups automatically populate `params`:

```yaml
  - name: "media.volume_set"
    regex: ["volume\\s+(?:to\\s+)?(?P<level>\\d+)"]
    response: ""
```

On match "volume 50" → `params = {"level": "50"}`

**When file is missing**, built-in rules are used: lights, temperature, privacy, basic media commands.

### 20.4 Tier 1.5 — System Module Intents (in-process)

System modules (type: SYSTEM) register intents **directly** in IntentRouter. No HTTP — regex matching happens in the same process.

**Registration:**

```python
@dataclass
class SystemIntentEntry:
    module: str                          # "media-player"
    intent: str                          # "media.play_genre"
    patterns: dict[str, list[str]]       # {"uk": [...], "en": [...]}
    description: str = ""
    priority: int = 0                    # higher = checked first
```

```python
# In start() of system module:
from system_modules.llm_engine.intent_router import get_intent_router, SystemIntentEntry

router = get_intent_router()
router.register_system_intent(SystemIntentEntry(
    module="media-player",
    intent="media.play_genre",
    priority=10,
    patterns={
        "uk": [r"(?:увімкни|включи)\s+(?P<genre>рок|джаз)\s*(?:музику)?"],
        "en": [r"play\s+(?P<genre>rock|jazz)\s*(?:music)?"],
    },
))
```

**Priority:** `priority=10` is checked before `priority=5`. Use:
- `priority=10` — intents with parameter extraction (genre, station_name, query)
- `priority=5` — simple commands (pause, stop, next)

**Cleanup on stop:**
```python
async def stop(self):
    get_intent_router().unregister_system_intents(self.name)
```

**Language:** Patterns for the current language are checked first, fallback to `en`.

### 20.5 Tier 2 — User Module Intents (HTTP)

**File:** `core/api/routes/intents.py`

User modules register patterns through Core API. On match, core forwards the request to the module's HTTP endpoint.

**Registration (SDK does it automatically on startup):**

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
        "en": ["weather", "forecast", "temperature outside"],
        "uk": ["погода", "прогноз", "температура надворі"]
      },
      "description": "Weather queries",
      "endpoint": "/api/intent"
    }
  ]
}
```

**Handling in module (endpoint `/api/intent`):**

```python
# main.py of the module
@app.post("/api/intent")
async def handle_intent(body: dict):
    text = body["text"]
    lang = body["lang"]
    context = body.get("context", {})

    # Business logic
    weather = await get_weather()

    return {
        "handled": True,
        "tts_text": f"Currently {weather['temp']}°C, {weather['desc']}",
        "data": weather
    }
```

**Module response contract:**

```json
{
  "handled": true,           // mandatory — whether request was handled
  "tts_text": "...",          // text for speech (TTS)
  "data": { ... }             // arbitrary data (optional)
}
```

**SDK `@intent` decorator (for user modules):**

```python
from sdk.base_module import SmartHomeModule, intent

class WeatherModule(SmartHomeModule):
    name = "weather-module"

    @intent(r"weather|forecast|погода|прогноз")
    async def handle_weather(self, text: str, context: dict) -> dict:
        weather = await self._fetch_weather()
        return {
            "tts_text": f"Temperature {weather['temp']}°C",
            "data": weather
        }
```

### 20.6 Tier 3 — LLM fallback

If no tier recognized the command — the text goes to LLM (Ollama/Cloud). The prompt is built automatically from `build_system_prompt(compact=True)` for local models.

**Auto-disable:** when RAM < 5 GB, LLM is not called → fallback message.

### 20.7 Delivery via EventBus

After determining the intent, IntentRouter publishes a `voice.intent` event:

```python
# Published automatically by IntentRouter
await bus.publish(
    type="voice.intent",
    source="core.intent_router",
    payload={
        "intent": "media.play_genre",       # intent name
        "response": "",                       # TTS text (empty for system_module)
        "action": null,                       # structured action
        "params": {"genre": "jazz"},           # extracted parameters
        "source": "system_module",            # where response came from
        "user_id": null,                      # speaker ID
        "latency_ms": 2                       # processing time
    }
)
```

**Module subscription to `voice.intent`:**

```python
# SYSTEM module
self.subscribe(["voice.intent"], self._on_event)

async def _on_event(self, event: Any) -> None:
    if event.type == "voice.intent":
        intent = event.payload.get("intent", "")
        params = event.payload.get("params", {})
        if intent.startswith("media."):
            await self._voice_handler.handle(intent, params)
```

### 20.8 Voice Pipeline — Full Cycle

```
Audio → parecord → Vosk STT → VoiceCoreModule._process_command(text)
  │
  ├─ publish("voice.recognized", {text})
  │
  ├─ IntentRouter.route(text, lang) → IntentResult
  │     ├─ Tier 1:   FastMatcher → media.pause
  │     ├─ Tier 1.5: System Intent → media.play_genre {genre: "jazz"}
  │     ├─ Tier 2:   Module Intent → module.weather-module
  │     ├─ Tier 3:   LLM → llm.response
  │     └─ Fallback  → unknown
  │
  ├─ IntentRouter → publish("voice.intent", {...})
  │
  ├─ if source == "system_module":
  │     # Module handles TTS itself via voice.speak
  │     MediaPlayer._on_event() → voice_handler.handle()
  │       → play radio → publish("voice.speak", {text: "Playing jazz"})
  │         → VoiceCore._on_voice_event() → TTS → audio
  │
  ├─ if source != "system_module" && response:
  │     # VoiceCore speaks the response itself
  │     publish("voice.response", {text, query})
  │     → TTS → audio → publish("voice.speak_done", {text})
  │
  └─ Save to voice_history (SQLite)
```

### 20.9 Intent Categories

Intents are grouped by namespace (prefix before the dot):

| Namespace | Example | Module | Description |
|-----------|---------|--------|-------------|
| `media.*` | `media.play_radio`, `media.pause`, `media.volume_up` | media-player | Playback control |
| `device.*` | `turn_on_light`, `turn_off_light`, `temperature_query` | FastMatcher / core | Device control |
| `privacy.*` | `privacy_on`, `privacy_off` | voice-core | Privacy mode |
| `module.*` | `module.weather-module` | User modules | Tier 2 HTTP modules |
| `llm.*` | `llm.response` | LLM Engine | Free conversation |
| `automation.*` | `automation.run_scene` | automation-engine | Scenes |
| `unknown` | `unknown` | fallback | Not recognized |

### 20.10 How to Add a Voice Command to Your Module

#### System module (type: SYSTEM)

1. Create file `intent_patterns.py` in the module directory
2. Define patterns with named groups for parameter extraction
3. Register in `start()`, remove in `stop()`
4. Subscribe to `voice.intent` via EventBus
5. Use `self.publish("voice.speak", {"text": "..."})` for TTS

```python
# system_modules/my_module/intent_patterns.py
from system_modules.llm_engine.intent_router import SystemIntentEntry

MY_INTENTS = [
    SystemIntentEntry(
        module="my-module",
        intent="mymodule.do_action",
        priority=5,
        patterns={
            "uk": [r"зроби\s+(?P<what>.+)"],
            "en": [r"do\s+(?P<what>.+)"],
        },
    ),
]
```

```python
# system_modules/my_module/module.py
class MyModule(SystemModule):
    name = "my-module"

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

    async def _on_event(self, event):
        if event.type == "voice.intent":
            intent = event.payload.get("intent", "")
            params = event.payload.get("params", {})
            if intent == "mymodule.do_action":
                what = params.get("what", "")
                # ... execute action ...
                await self.publish("voice.speak", {"text": f"Executing: {what}"})
```

#### User module (type: UI/INTEGRATION/DRIVER/AUTOMATION)

1. Add `intents` to `manifest.json`
2. Use `@intent` decorator or implement `POST /api/intent`
3. Return `{"handled": true, "tts_text": "..."}`

```json
// manifest.json
{
  "name": "my-module",
  "type": "UI",
  "port": 8100,
  "intents": [
    {
      "patterns": {
        "en": ["do something", "action"]
      },
      "description": "Execute custom action",
      "endpoint": "/api/intent"
    }
  ]
}
```

```python
# main.py
from sdk.base_module import SmartHomeModule, intent

class MyModule(SmartHomeModule):
    name = "my-module"

    @intent(r"do\s+(?P<what>.+)")
    async def handle_action(self, text: str, context: dict) -> dict:
        return {"tts_text": f"Executing action", "data": {"action": "done"}}
```

### 20.11 Dependencies Between Modules

Modules **do not depend** on each other directly. All communication — through EventBus:

```
┌──────────────┐     voice.intent      ┌──────────────┐
│  voice-core  │ ──── EventBus ────── │ media-player  │
│  (STT/TTS)   │                       │  (VLC/Radio)  │
└──────────────┘                       └──────────────┘
        │                                      │
        │  voice.speak                         │  voice.speak
        ▼                                      ▼
  ┌──────────┐                         ┌──────────────┐
  │  Piper   │                         │   EventBus   │
  │  TTS     │ ◄──────────────────────│              │
  └──────────┘                         └──────────────┘
```

**Dependency rules:**

| Rule | Description |
|------|-------------|
| No direct imports | Modules do NOT import each other |
| EventBus — the only channel | Communication only through `publish()` / `subscribe()` |
| Graceful degradation | If module is not running — commands are silently ignored |
| Startup order does not matter | Modules register intents on `start()` |
| No blocking dependencies | If media-player is not running → `media.*` intents → LLM fallback |

**Exceptions (only for system modules):**

```python
# IntentRouter — a utility, not a module. Allowed import:
from system_modules.llm_engine.intent_router import get_intent_router, SystemIntentEntry

# EventBus — core. Available through self.publish() / self.subscribe()
# NO NEED to import directly — use SystemModule methods
```

### 20.12 Supported Languages

IntentRouter determines the language from the STT model (`vosk-model-small-uk` → `uk`).

| Code | Language | STT model |
|------|----------|-----------|
| `uk` | Українська | `vosk-model-small-uk` |
| `en` | English | `vosk-model-small-en-us` |

Intent patterns must contain variants for all supported languages:

```python
patterns={
    "uk": [r"увімкни\s+радіо"],
    "en": [r"(?:play|turn on)\s+radio"],
}
```

When patterns for the current language are missing — fallback to `en`.

---

*SelenaCore · AGENTS.md · SmartHome LK · Open Source MIT*
*Repository: https://github.com/dotradepro/SelenaCore*
