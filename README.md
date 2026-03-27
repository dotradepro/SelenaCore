<div align="center">

# SelenaCore

**Open-source local smart home core for Raspberry Pi**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)](https://fastapi.tiangolo.com)

🇺🇦 [Українська версія](docs/uk/README.md)

</div>

---

## What is it

SmartHome LK Core is an open-source (MIT) smart home hub that runs on Raspberry Pi 4/5 or any Linux SBC. Works **fully offline** — voice assistant, automations, device management — no subscription, no cloud required.

Three principles:

- **Core is immutable** — SHA256 protection of all core files, Integrity Agent checks every 30 sec
- **Modules are isolated** — HTTP/localhost:7070 only, no direct access to core data
- **Agent watches** — IntegrityAgent: stop modules → notify → rollback → SAFE MODE

---

## Quick Start

### Requirements

- Raspberry Pi 4/5 (4–8 GB RAM), Jetson Orin, or any Linux SBC (ARM64/x86_64)
- Ubuntu 22.04+ (or Raspberry Pi OS)
- Docker + Docker Compose (auto-installed by setup script)

### Launch (automatic — recommended)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

cp .env.example .env
# Set GEMINI_API_KEY and other values in .env

sudo bash scripts/setup.sh
```

The setup script installs all dependencies, builds Docker images, configures the kiosk display service, and starts everything automatically.

> After setup completes, **log out and log back in** once so group changes take effect. The kiosk will then launch automatically on every boot.

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

### Kiosk / Physical Display

SelenaCore auto-detects the display environment and launches the UI accordingly:

| Environment | Mode | Description |
|-------------|------|-------------|
| Desktop (GNOME/KDE) | `desktop` | Chromium kiosk via existing DE session |
| Headless + screen | `kiosk` | cage + Chromium (Wayland, no DE needed) |
| No display | `tty` | Python TUI with QR code on TTY1 |

Full setup guide: [docs/kiosk-setup.md](docs/kiosk-setup.md)

### First Launch — Onboarding Wizard

On first start (or without Wi-Fi) the core creates an access point:

```
SSID:     SmartHome-Setup
Password: smarthome
```

Connect from your phone → open browser → `192.168.4.1` → follow the 9-step wizard.

---

## Architecture

```
smarthome-core     ~420 MB    FastAPI, Device Registry, Event Bus,
                               Module Loader, Cloud Sync, Voice Core,
                               LLM Engine, UI Core

smarthome-modules  180-350 MB  All user modules (Plugin Manager)

smarthome-sandbox  96-256 MB   Temporary container for testing (--rm)

smarthome-agent    systemd     Integrity Agent — independent process
```

### Project Structure

```
selena-core/
  core/
    main.py                  # FastAPI + asyncio entry point
    config.py                # Settings from .env + core.yaml
    registry/                # Device Registry (SQLAlchemy + SQLite)
    eventbus/                # Event Bus (asyncio.Queue + webhooks)
    module_loader/           # Plugin Manager + Docker sandbox
    api/routes/              # REST API endpoints
    cloud_sync/              # Platform sync (HMAC)
  system_modules/
    voice_core/              # STT (Vosk), TTS (Piper), wake-word
    llm_engine/              # Ollama, Fast Matcher, Intent Router
    network_scanner/         # ARP, mDNS, SSDP, OUI lookup
    user_manager/            # Profiles, PIN, Face ID, audit log
    secrets_vault/           # AES-256-GCM token storage
    import_adapters/         # Home Assistant, Tuya, Philips Hue
    hw_monitor/              # CPU/RAM/disk monitoring
    notify_push/             # Web Push VAPID
    backup_manager/          # Local/Cloud backup, QR transfer
    remote_access/           # Tailscale VPN
    ui_core/                 # FastAPI :80, PWA, wizard, TTY TUI
  agent/
    integrity_agent.py       # SHA256 periodic check
    responder.py             # Response chain + SAFE MODE
  sdk/
    base_module.py           # SmartHomeModule base class
    cli.py                   # smarthome CLI
    mock_core.py             # Mock Core API for development
  tests/                     # pytest tests
  config/
    core.yaml.example
  docker-compose.yml
```

---

## Core API

Base URL: `http://localhost:7070/api/v1`
Auth: `Authorization: Bearer <module_token>`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Core status (no auth required) |
| GET | `/devices` | Device list |
| POST | `/devices` | Register device |
| GET | `/devices/{id}` | Specific device |
| PATCH | `/devices/{id}/state` | Update state |
| DELETE | `/devices/{id}` | Delete |
| POST | `/events/publish` | Publish event |
| POST | `/events/subscribe` | Subscribe to events (webhook) |
| GET | `/modules` | Module list |
| POST | `/modules/install` | Install module (ZIP) |
| POST | `/modules/{name}/start` | Start module |
| POST | `/modules/{name}/stop` | Stop module |
| GET | `/integrity/status` | Integrity Agent status |
| GET | `/system/info` | Device info |

> Onboarding wizard steps are handled at `POST /api/ui/setup/{step}` (UI-tier endpoint, no auth required on first boot).

Swagger UI: `http://localhost:7070/docs` — only available when `DEBUG=true`.

---

## Voice Assistant

Fully offline — STT and TTS work without internet.

```
Wake-word (openWakeWord)
  → Audio recording
  → Whisper.cpp STT           ~0.8–2 sec
  → Speaker ID (resemblyzer)  ~200 ms
  → Fast Matcher (YAML)       ~50 ms
  → LLM Fallback (Ollama)     ~3–8 sec (Pi 5, 8GB only)
  → Piper TTS                 ~300 ms
  → History (SQLite)
```

Supported languages: `uk`, `en`.

---

## SDK for Module Developers

```bash
smarthome new-module my-module    # create module structure
smarthome dev                     # mock Core API on :7070
smarthome test                    # run tests
smarthome publish                 # package and upload to SelenaCore
```

Module example:

```python
from sdk.base_module import SmartHomeModule, on_event, scheduled

class ClimateModule(SmartHomeModule):
    name = "climate-module"
    version = "1.0.0"

    async def on_start(self):
        self._log.info("Climate module started")

    @on_event("device.state_changed")
    async def handle_state(self, payload):
        if payload.get("new_state", {}).get("temperature", 0) > 25:
            await self.publish_event("climate.overheat", {"device_id": payload["device_id"]})

    @scheduled("every:5m")
    async def periodic_check(self):
        pass  # runs every 5 minutes
```

---

## Environment Variables

Copy `.env.example` → `.env`:

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
MOCK_PLATFORM=false
DEV_MODULE_TOKEN=test-module-token-xyz
```

---

## Tests

```bash
pip install -r requirements-dev.txt

pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

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
| Ko-fi | [ko-fi.com/dotradepro](https://ko-fi.com/dotradepro) | One-time · Goal tracker · All tiers |
| GitHub Sponsors | [github.com/sponsors/dotradepro](https://github.com/sponsors/dotradepro) | Monthly or one-time |

**Tiers:** Supporter $10 · Early Adopter $50 · Developer $100 (PRO 6mo) · Partner $500 (UNLIMITED forever) · Founding Sponsor $1000+

See [SPONSORS.md](SPONSORS.md) for the full list of supporters and tier benefits.

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/dotradepro)

---

## Security

- **Integrity Agent** — SHA256 check of core files every 30 sec
- **AES-256-GCM** — all OAuth tokens encrypted in `/secure/tokens/`
- **API proxy** — modules never receive tokens directly
- **Biometrics** — stored locally only, cloud sync blocked
- **Core API** — inaccessible outside localhost (iptables)
- **Rate limiting** — 120 req/min (external), 600 req/min (LAN); PIN: 5 attempts → 10 min lock

---

## License

MIT — see [LICENSE](LICENSE)

---

*SmartHome LK · SelenaCore v0.3.0-beta · 2026 · https://github.com/dotradepro/SelenaCore*
