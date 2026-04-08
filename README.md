<div align="center">

<!-- TODO: add logo to docs/assets/logo.png -->

# SelenaCore

**Local-first smart home hub. No cloud required. No subscription. Just your hardware.**

[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3b82f6.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-06b6d4.svg)](https://fastapi.tiangolo.com)
[![Docker](https://img.shields.io/badge/Docker-Compose-2563eb.svg)](https://docker.com)
[![GitHub Stars](https://img.shields.io/github/stars/dotradepro/SelenaCore?style=flat&color=f59e0b)](https://github.com/dotradepro/SelenaCore/stargazers)
[![GitHub Issues](https://img.shields.io/github/issues/dotradepro/SelenaCore?color=ef4444)](https://github.com/dotradepro/SelenaCore/issues)

[🇺🇦 Українська](docs/uk/README.md) · [📖 Docs](https://docs.selenehome.tech) · [🐛 Report Bug](https://github.com/dotradepro/SelenaCore/issues) · [💡 Discuss](https://github.com/dotradepro/SelenaCore/discussions)

</div>

---

## Why SelenaCore?

- **100% offline** — voice assistant, automations, device control all run on your hardware. The internet is optional.
- **No subscription** — set it up once, free forever. No accounts, no usage tiers.
- **Your data stays home** — nothing leaves the LAN unless you explicitly enable cloud sync.
- **Runs on a Raspberry Pi 4** — no server, no rack, no fans.

---

## Features

### 🎙️ Offline Voice Assistant

Full offline STT (Vosk / Whisper) and TTS (Piper) pipeline. Local LLM inference via Ollama for natural language understanding. Cloud LLMs (OpenAI, Anthropic, Groq) are an optional fallback. Models are picked and downloaded from the in-browser setup wizard.

### 🧩 Modular Architecture

24 built-in system modules (voice, device management, climate, lights, energy, automations, media and more) run in-process with zero overhead. User modules run in isolated Docker containers and only communicate through the WebSocket Module Bus. Modules cannot import each other.

### 🔌 Runtime Provider System

`device-control` is a pluggable provider system. Built-in providers: Tuya LAN, Tuya Cloud, Gree / Pular Wi-Fi A/C, MQTT. Optional providers (Philips Hue Bridge, ESPHome and others) install in one click from the UI — no rebuild, no container restart.

### 🏠 Auto-routing & Smart Import

When you import a device — manually, from Tuya, or via Gree scan — it is automatically routed to the right module (`climate` / `lights-switches`) by `entity_type` and registered as a source in `energy-monitor`. No manual wiring.

### 🛡️ Integrity Agent

A separate process verifies SHA256 hashes of every core file every 30 seconds. On change: stop all modules → notify → roll back → enter SAFE MODE. The core cannot be silently modified.

### 📱 Setup in 10 Minutes

One `install.sh` script, then a 9-step browser wizard: Wi-Fi, STT/TTS models, LLM, admin user. No SSH, no editing config files. Works from a phone.

### ⚡ Energy Monitoring

Auto-tracking of power consumption for every registered device. One filterable, sortable table with search, room and type filters. Click the dashboard widget to open the full-screen detail table.

### 🔄 OTA Updates

The built-in `update-manager` checks GitHub Releases daily at 03:00, downloads with SHA256 verification, snapshots a backup, and applies the update atomically through systemd. One click to update.

---

## Quick Start

### Hardware

| Platform                    | RAM   | Notes                              |
|-----------------------------|-------|------------------------------------|
| Raspberry Pi 4 / 5          | 4 GB+ | Recommended for most users         |
| NVIDIA Jetson Orin Nano     | 8 GB  | GPU-accelerated STT / TTS          |
| Any Linux SBC (ARM64/x86_64)| 2 GB+ | Core features, no local LLM        |

OS: Ubuntu 22.04+ or Raspberry Pi OS Bookworm. Docker 24+ and Docker Compose v2 are installed automatically.

### One-shot install (recommended)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
sudo ./install.sh
```

`install.sh` installs Docker, creates the `selena` system user and directories, builds the containers, and prints a URL like `http://<lan-ip>/`. Everything else — model downloads, voice selection, LLM choice, admin user, native systemd services — happens in the **first-run wizard** in your browser, with a live progress bar.

### Manual setup

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore
cp .env.example .env
cp config/core.yaml.example config/core.yaml
docker compose up -d --build
```

### URLs

- `http://localhost` or `http://smarthome.local` — Web UI + API
- `https://localhost` — HTTPS via self-signed TLS proxy (~5 MB overhead)
- `http://localhost/docs` — Swagger UI (only when `DEBUG=true`)

---

## Architecture

```
+--------------------------------------------------------------+
|                  SelenaCore (FastAPI :80)                    |
|                                                              |
|  +--------------------------------------------------------+  |
|  |          24 System Modules (in-process)                |  |
|  |  voice_core · llm_engine · climate · lights_switches   |  |
|  |  device_control (provider system) · energy_monitor     |  |
|  |  automation_engine · update_manager · scheduler        |  |
|  |  user_manager · secrets_vault · hw_monitor · ...       |  |
|  +-----------------------+--------------------------------+  |
|                          | EventBus (asyncio.Queue)          |
|  +-----------------------+--------------------------------+  |
|  |  Module Bus (WebSocket ws://host/api/v1/bus)           |  |---> User Modules
|  +--------------------------------------------------------+  |     (Docker)
|                                                              |
|  Device Registry (SQLite) · Cloud Sync (HMAC-SHA256)         |
|  Secrets Vault (AES-256-GCM) · SyncManager · i18n (en, uk)   |
+--------------------------------------------------------------+

HTTPS :443 ---> TLS proxy (asyncio, ~5 MB RAM) ---> :80

+----------------------------------+
|  Integrity Agent (separate proc) |
|  SHA256 every 30s                |
|  Rollback + SAFE MODE            |
+----------------------------------+
```

Total runtime footprint: **~1.5 GB RAM** for the entire stack on a Pi 4. See [docs/architecture.md](docs/architecture.md) for the full design.

---

## Modules

| Module              | Purpose                                                                            |
|---------------------|------------------------------------------------------------------------------------|
| `voice_core`        | STT (Vosk / Whisper), TTS (Piper), wake-word, speaker ID, privacy mode             |
| `llm_engine`        | Ollama local LLM, Fast Matcher, 6-tier intent router, cloud LLM fallback           |
| `ui_core`           | React SPA + PWA (served directly by Core)                                          |
| `device_control`    | Device registry, pluggable provider system (Tuya / Gree / Hue / ESPHome / MQTT)    |
| `climate`           | A/C and thermostat control, grouped by room                                        |
| `lights_switches`   | Lights, switches, outlets — on/off, brightness, RGB                                |
| `energy_monitor`    | Power tracking, kWh statistics, auto-routing source                                |
| `automation_engine` | YAML rules: triggers (time / event / device / presence) → actions                  |
| `update_manager`    | OTA updates from GitHub Releases with SHA256 verification                          |
| `scheduler`         | Cron / interval / sunrise / sunset task scheduling                                 |
| `user_manager`      | User profiles, PIN, Face ID, audit log                                             |
| `secrets_vault`     | AES-256-GCM token and credential storage                                           |
| `hw_monitor`        | CPU temp, RAM, disk, uptime — 30s polling                                          |
| `media_player`      | Internet radio, USB, SMB, Internet Archive — voice control, cover art              |
| `protocol_bridge`   | MQTT / Zigbee / Z-Wave / HTTP gateway to the device registry                       |
| `weather_service`   | Local weather and forecast via Open-Meteo (no API key)                             |
| `presence_detection`| Home / away detection via ARP, Bluetooth, Wi-Fi MAC                                |
| `device_watchdog`   | Device availability monitoring                                                     |
| `notification_router`| Routes notifications to TTS, Telegram, Web Push, HTTP webhooks                    |
| `notify_push`       | Web Push (VAPID) implementation                                                    |
| `network_scanner`   | ARP / mDNS / SSDP / Zigbee discovery                                               |
| `clock`             | Alarms, timers, reminders, world clock, stopwatch                                  |
| `backup_manager`    | Local USB / SD and E2E cloud backup                                                |
| `remote_access`     | Tailscale-based remote access                                                      |

Full reference: [docs/modules.md](docs/modules.md).

---

## Provider System

`device-control` ships with a runtime-pluggable provider system. New device families can be added without rebuilding the container.

| Provider      | Protocol            | Built-in | Notes                                  |
|---------------|---------------------|----------|----------------------------------------|
| `tuya_local`  | Tuya LAN (tinytuya) | ✅       | No developer account required          |
| `tuya_cloud`  | Tuya Sharing SDK    | ✅       | No cloud account required              |
| `gree`        | Gree UDP / AES      | ✅       | Pular, Cooper&Hunter, EWT A/C          |
| `mqtt`        | MQTT bridge         | ✅       | Via `protocol_bridge`                  |
| `philips_hue` | Hue Bridge LAN      | Install  | `phue` library                         |
| `esphome`     | Native asyncio API  | Install  | Push-based                             |

Install from the UI: **Settings → device-control → Providers → Install**. See [docs/providers.md](docs/providers.md).

---

## Core API

| Method | Path                  | Description                                  |
|--------|-----------------------|----------------------------------------------|
| GET    | `/api/v1/health`      | Core status (no auth)                        |
| GET    | `/api/v1/system/info` | Hardware and version info                    |
| GET    | `/api/v1/devices`     | List all devices                             |
| POST   | `/api/v1/devices`     | Register a device                            |
| PATCH  | `/api/v1/devices/{id}/state` | Update device state                   |
| POST   | `/api/v1/events/publish` | Publish an event                          |
| GET    | `/api/v1/modules`     | List modules                                 |
| POST   | `/api/v1/modules/install` | Install a module (ZIP)                   |
| WS     | `/api/v1/bus?token=TOKEN` | Module Bus (WebSocket)                   |
| WS     | `/api/ui/sync?v=N`    | UI state sync (WebSocket, versioned)         |

Auth: `Authorization: Bearer <module_token>`. Full reference: [docs/api-reference.md](docs/api-reference.md).

---

## Building a Module

```bash
smarthome new-module my-module
cd my-module
smarthome dev
smarthome publish . --core http://smarthome.local
```

```python
from sdk.base_module import SmartHomeModule, intent, on_event, scheduled

class MyModule(SmartHomeModule):
    name = "my-module"

    @intent(r"turn on (?P<what>.+)")
    async def handle_turn_on(self, text: str, context: dict) -> dict:
        return {"tts_text": f"Turning on {context['what']}"}

    @on_event("device.state_changed")
    async def on_device_change(self, event: dict) -> None:
        ...

    @scheduled(cron="0 7 * * *")
    async def morning_routine(self) -> None:
        ...
```

Full guide: [docs/module-development.md](docs/module-development.md).

---

## Configuration

| Variable           | Default                              | Description                          |
|--------------------|--------------------------------------|--------------------------------------|
| `CORE_PORT`        | `80`                                 | Unified API + UI port                |
| `CORE_DATA_DIR`    | `/var/lib/selena`                    | Database, models, backups            |
| `CORE_SECURE_DIR`  | `/secure`                            | Encrypted tokens, TLS certs          |
| `CORE_LOG_LEVEL`   | `INFO`                               | DEBUG / INFO / WARNING               |
| `DEBUG`            | `false`                              | Enables Swagger UI at `/docs`        |
| `PLATFORM_API_URL` | `https://selenehome.tech/api/v1`     | Cloud platform (optional)            |
| `OLLAMA_URL`       | `http://localhost:11434`             | Local LLM inference endpoint         |
| `UI_HTTPS`         | `true`                               | Enable TLS proxy on `:443`           |

Full reference: [docs/configuration.md](docs/configuration.md).

---

## Project Structure

```
selena-core/
├── core/
│   ├── main.py              # FastAPI lifespan — single :80 process
│   ├── config.py            # Pydantic settings
│   ├── module_bus.py        # WebSocket Module Bus
│   ├── api/
│   │   ├── sync_manager.py  # UI state sync (versioned WebSocket)
│   │   └── routes/          # REST + WebSocket + SPA serving
│   ├── registry/            # Device Registry + DriverProvider ORM
│   ├── eventbus/            # Async event bus
│   ├── module_loader/       # Plugin manager + Docker sandbox
│   └── cloud_sync/          # HMAC-signed cloud sync
├── system_modules/          # 24 built-in modules
│   ├── voice_core/
│   ├── llm_engine/
│   ├── device_control/      # providers/, drivers/ (gree, tuya_*, …)
│   ├── climate/
│   ├── lights_switches/
│   ├── energy_monitor/
│   ├── update_manager/
│   └── …                    # 17 more
├── agent/                   # Integrity Agent (separate process)
├── sdk/                     # SmartHomeModule base class + CLI
├── modules/                 # User-installed modules (Docker)
├── config/                  # core.yaml.example, locales, intents
├── scripts/                 # start.sh, install.sh, systemd units
├── tests/                   # pytest suite (72+ tests)
└── docker-compose.yml
```

---

## Roadmap

- [ ] Matter / Thread protocol support
- [ ] Module marketplace UI
- [ ] Mobile app (React Native)
- [ ] Multi-hub mesh networking
- [ ] Home Assistant full migration tool
- [ ] Voice print (speaker ID) improvements

---

## Contributing

Pull requests, issue reports, translations, hardware tests and module ideas are all welcome. Before opening a PR:

```bash
pytest tests/ -x -q
python -m mypy core/ --ignore-missing
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

---

## Security

Please **do not** open a public issue for security reports. Use [GitHub Security Advisories](https://github.com/dotradepro/SelenaCore/security/advisories/new) instead. See [SECURITY.md](SECURITY.md) for the full policy.

---

<div align="center">

## Sponsoring

[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-%E2%9D%A4-ea4aaa?style=for-the-badge&logo=github)](https://github.com/sponsors/dotradepro)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-FF5E5B?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/dotradepro)

Sponsorship pays for new test hardware (Pi 5, Jetson, Zigbee dongles), development time, video tutorials, and the marketplace infrastructure.

---

Made with ❤️ for people who believe your home should work for you — not the other way around.

[⭐ Star this repo](https://github.com/dotradepro/SelenaCore/stargazers) · [🐛 Report Issue](https://github.com/dotradepro/SelenaCore/issues) · [💬 Discuss](https://github.com/dotradepro/SelenaCore/discussions)

</div>
