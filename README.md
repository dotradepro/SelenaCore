<div align="center">

# SelenaCore

**Open-source local smart home core for Raspberry Pi**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)](https://fastapi.tiangolo.com)

[Українська версія](docs/uk/README.md)

</div>

---

## What is SelenaCore

SelenaCore is an open-source (MIT) smart home hub that runs on Raspberry Pi 4/5 or any Linux SBC. Works **fully offline** — voice assistant, automations, device management — no subscription, no cloud required.

Three principles:

- **Core is immutable** — SHA256 protection of all core files, Integrity Agent checks every 30 sec
- **Modules are isolated** — all user modules communicate exclusively through the WebSocket Module Bus
- **Agent watches** — IntegrityAgent: stop modules → notify → rollback → SAFE MODE

---

## Quick Start

### Requirements

- Raspberry Pi 4/5 (4-8 GB RAM), NVIDIA Jetson Orin Nano (8 GB), or any Linux SBC (ARM64/x86_64)
- Ubuntu 22.04+ (or Raspberry Pi OS)
- Docker + Docker Compose (auto-installed by setup script)

### Launch (automatic)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

cp .env.example .env
# Set GEMINI_API_KEY and other values in .env

sudo bash scripts/setup.sh
```

The setup script installs all dependencies, builds Docker images, configures the kiosk display service, and starts everything automatically.

### Launch (manual)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

cp .env.example .env
docker compose build
docker compose up -d
```

**Core API:** `http://localhost:7070`
**UI (PWA):** `http://localhost:80` or `http://smarthome.local:80`

### First Launch — Onboarding Wizard

On first start (or without Wi-Fi) the core creates an access point:

```
SSID:     SmartHome-Setup
Password: smarthome
```

Connect from your phone, open browser at `192.168.4.1`, follow the 9-step wizard.

---

## Architecture

SelenaCore runs as a single FastAPI application on port 7070 with two types of modules:

```
┌───────────────────────────────────────────────────────┐
│                  SelenaCore (FastAPI :7070)            │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │           Module Bus (WebSocket Hub)             │  │
│  │         ws://core:7070/api/v1/bus               │──┼──── User Modules
│  └──────────────────────┬──────────────────────────┘  │     (Docker containers)
│                         │                             │
│  EventBus (asyncio.Queue, in-process pub/sub)         │
│  ├── voice_core       ├── llm_engine                  │
│  ├── ui_core          ├── automation_engine            │
│  ├── user_manager     ├── scheduler                   │
│  ├── device_watchdog  ├── protocol_bridge             │
│  ├── hw_monitor       ├── media_player                │
│  └── 12 more system modules                          │
│                                                       │
│  Device Registry (SQLite)  │  Cloud Sync (HMAC)       │
│  Integrity Agent (SHA256)  │  i18n (uk, en)           │
└───────────────────────────────────────────────────────┘
```

**System modules** (22 built-in) run in-process via `importlib` — zero network overhead, direct EventBus and database access.

**User modules** run in Docker containers and connect to core through the **WebSocket Module Bus** at `ws://core:7070/api/v1/bus`. No individual ports per module — all communication goes through a single bus endpoint.

### Project Structure

```
selena-core/
  core/
    main.py                  # FastAPI + asyncio entry point
    config.py                # Settings from .env + core.yaml
    module_bus.py            # WebSocket Module Bus (CAN-bus inspired)
    registry/                # Device Registry (SQLAlchemy + SQLite)
    eventbus/                # Event Bus (asyncio.Queue)
    module_loader/           # Plugin Manager + Docker sandbox
    api/routes/              # REST API endpoints
    cloud_sync/              # Platform sync (HMAC)
    i18n.py                  # Internationalization
  system_modules/            # 22 built-in in-process modules
    voice_core/              # STT (Whisper), TTS (Piper), wake-word
    llm_engine/              # Ollama, Fast Matcher, Intent Router
    ui_core/                 # Web UI server (:80)
    user_manager/            # Profiles, PIN, Face ID, audit log
    secrets_vault/           # AES-256-GCM token storage
    ...                      # 17 more modules
  modules/                   # User-installed modules (Docker)
    weather-module/          # Example: weather via Open-Meteo
  agent/
    integrity_agent.py       # SHA256 periodic check
    responder.py             # Response chain + SAFE MODE
  sdk/
    base_module.py           # SmartHomeModule base class + decorators
    cli.py                   # smarthome CLI tool
  config/
    core.yaml.example        # Configuration template
    locales/                 # i18n translation files
  tests/                     # pytest test suite
  benchmarks/                # Performance benchmarks
  docker-compose.yml
```

---

## Core API

Base URL: `http://localhost:7070/api/v1`
Auth: `Authorization: Bearer <module_token>`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Core status (no auth) |
| GET | `/system/info` | System information |
| GET | `/devices` | Device list |
| POST | `/devices` | Register device |
| GET | `/devices/{id}` | Specific device |
| PATCH | `/devices/{id}/state` | Update state |
| DELETE | `/devices/{id}` | Delete device |
| POST | `/events/publish` | Publish event |
| GET | `/modules` | Module list |
| POST | `/modules/install` | Install module (ZIP) |
| POST | `/modules/{name}/start` | Start module |
| POST | `/modules/{name}/stop` | Stop module |
| GET | `/integrity/status` | Integrity Agent status |
| WS | `/bus?token=TOKEN` | Module Bus (WebSocket) |

Swagger UI: `http://localhost:7070/docs` — only available when `DEBUG=true`.

Full reference: [docs/api-reference.md](docs/api-reference.md)

---

## Voice Assistant

Fully offline — STT and TTS work without internet.

```
Wake-word (openWakeWord)
  → Audio recording
  → Whisper STT            ~0.8-2 sec
  → Speaker ID (resemblyzer)
  → Intent Router (4-tier):
      1. Fast Matcher (YAML)           ~0 ms
      2. System Module Intents         ~μs
      3. Module Bus Intents (WebSocket) ~ms
      4. Ollama LLM fallback           ~3-8 sec
  → Piper TTS              ~300 ms
```

Supported languages: `uk`, `en`.

Full guide: [docs/voice-settings.md](docs/voice-settings.md)

---

## Module Development

Modules communicate with core via the **WebSocket Module Bus** — no separate HTTP servers, no individual ports.

```python
import asyncio
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled

class ClimateModule(SmartHomeModule):
    name = "climate-module"
    version = "1.0.0"

    async def on_start(self):
        self._log.info("Climate module started")

    @intent(r"temperature|how hot|як.*тепло")
    async def handle_temp(self, text: str, context: dict) -> dict:
        return {"tts_text": "Current temperature is 22 degrees"}

    @on_event("device.state_changed")
    async def handle_state(self, data: dict):
        if data.get("new_state", {}).get("temperature", 0) > 25:
            await self.publish_event("climate.overheat", {
                "device_id": data["device_id"]
            })

    @scheduled("every:5m")
    async def periodic_check(self):
        devices = await self.api_request("GET", "/devices")
        # ... process devices ...

if __name__ == "__main__":
    module = ClimateModule()
    asyncio.run(module.start())
```

Full guide: [docs/module-development.md](docs/module-development.md)

---

## Environment Variables

Copy `.env.example` to `.env`:

```bash
CORE_PORT=7070
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
UI_PORT=80
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
DEBUG=false
DEV_MODULE_TOKEN=test-module-token-xyz
```

Full reference: [docs/configuration.md](docs/configuration.md)

---

## Tests

```bash
pip install -r requirements-dev.txt

pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design, module types, EventBus, boot sequence |
| [Module Bus Protocol](docs/module-bus-protocol.md) | WebSocket protocol reference |
| [Module Development](docs/module-development.md) | Building user modules with the SDK |
| [System Module Development](docs/system-module-development.md) | Building in-process system modules |
| [API Reference](docs/api-reference.md) | REST API endpoints |
| [Configuration](docs/configuration.md) | .env and core.yaml settings |
| [Widget Development](docs/widget-development.md) | UI widgets for modules |
| [Deployment](docs/deployment.md) | Installation and production setup |
| [Voice Settings](docs/voice-settings.md) | Voice pipeline configuration |
| [Kiosk Setup](docs/kiosk-setup.md) | Physical display configuration |
| [User Manager & Auth](docs/user-manager-auth.md) | Authentication and security |
| [Contributing](CONTRIBUTING.md) | Contribution guidelines |

---

## Support the Project

SmartHome LK is built by a solo developer. If you believe in what we're building — smart home infrastructure with autonomous AI development — consider sponsoring.

**Your support funds:**
- LLM API costs for the AI coding agents
- Docker sandbox hosting for secure module testing
- 6 months of production infrastructure
- Full-time development focus

| Platform | Link | Notes |
|---|---|---|
| Ko-fi | [ko-fi.com/dotradepro](https://ko-fi.com/dotradepro) | One-time / Goal tracker / All tiers |
| GitHub Sponsors | [github.com/sponsors/dotradepro](https://github.com/sponsors/dotradepro) | Monthly or one-time |

**Tiers:** Supporter $10 / Early Adopter $50 / Developer $100 (PRO 6mo) / Partner $500 (UNLIMITED forever) / Founding Sponsor $1000+

See [SPONSORS.md](SPONSORS.md) for the full list of supporters and tier benefits.

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/dotradepro)

---

## Security

- **Integrity Agent** — SHA256 check of core files every 30 sec
- **AES-256-GCM** — all OAuth tokens encrypted in `/secure/tokens/`
- **Module Bus ACL** — permission-based access control per module
- **Biometrics** — stored locally only, cloud sync blocked
- **Core API** — inaccessible outside localhost (iptables)
- **Rate limiting** — 120 req/min; PIN: 5 attempts → 10 min lock

---

## License

MIT — see [LICENSE](LICENSE)

---

*SmartHome LK / SelenaCore v0.3.0-beta / 2026 / https://github.com/dotradepro/SelenaCore*
