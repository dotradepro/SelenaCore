<div align="center">

# SelenaCore

**Відкрите локальне ядро розумного дому для Raspberry Pi**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](../../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)](https://fastapi.tiangolo.com)

🇬🇧 [English version](../../README.md)

</div>

---

## Що це

SmartHome LK Core — відкритий (MIT) хаб розумного дому, який встановлюється на Raspberry Pi 4/5 або будь-який Linux SBC. Працює **повністю офлайн** — голосовий асистент, автоматизації, керування пристроями — без підписки та без хмари.

Три принципи:

- **Ядро незмінне** — SHA256-захист усіх файлів ядра, Integrity Agent перевіряє кожні 30 сек
- **Модулі ізольовані** — лише HTTP/localhost:7070, без прямого доступу до даних ядра
- **Агент спостерігає** — IntegrityAgent: стоп модулів → повідомлення → відкат → SAFE MODE

---

## Швидкий старт

### Вимоги

- Raspberry Pi 4/5 (4–8 GB RAM) або будь-який Linux SBC
- Docker + Docker Compose
- Python 3.11+

### Запуск

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

cp .env.example .env
# Відредагуйте .env за потребою

docker compose up -d
```

**Core API:** `http://localhost:7070`
**UI (PWA):** `http://localhost:80` або `http://smarthome.local:80`

### Перший запуск — Onboarding Wizard

При першому старті (або без Wi-Fi) ядро піднімає точку доступу:

```
SSID:     SmartHome-Setup
Password: smarthome
```

Підключіться з телефону → відкрийте браузер → `192.168.4.1` → пройдіть 9-кроковий wizard.

---

## Архітектура

```
smarthome-core     ~420 MB    FastAPI, Device Registry, Event Bus,
                               Module Loader, Cloud Sync, Voice Core,
                               LLM Engine, UI Core

smarthome-modules  180-350 MB  Усі користувацькі модулі (Plugin Manager)

smarthome-sandbox  96-256 MB   Тимчасовий контейнер для тестування (--rm)

smarthome-agent    systemd     Integrity Agent — незалежний процес
```

### Структура проєкту

```
selena-core/
  core/
    main.py                  # FastAPI + asyncio точка входу
    config.py                # Налаштування з .env + core.yaml
    registry/                # Device Registry (SQLAlchemy + SQLite)
    eventbus/                # Event Bus (asyncio.Queue + webhooks)
    module_loader/           # Plugin Manager + Docker sandbox
    api/routes/              # REST API endpoints
    cloud_sync/              # Синхронізація з платформою (HMAC)
  system_modules/
    voice_core/              # STT (Vosk), TTS (Piper), wake-word
    llm_engine/              # Ollama, Fast Matcher, Intent Router
    network_scanner/         # ARP, mDNS, SSDP, OUI lookup
    user_manager/            # Профілі, PIN, Face ID, аудит-лог
    secrets_vault/           # AES-256-GCM сховище токенів
    import_adapters/         # Home Assistant, Tuya, Philips Hue
    hw_monitor/              # CPU/RAM/диск моніторинг
    notify_push/             # Web Push VAPID
    backup_manager/          # Local/Cloud backup, QR-перенесення
    remote_access/           # Tailscale VPN
    ui_core/                 # FastAPI :80, PWA, wizard, TTY TUI
  agent/
    integrity_agent.py       # SHA256 періодична перевірка
    responder.py             # Ланцюг реагування + SAFE MODE
  sdk/
    base_module.py           # SmartHomeModule базовий клас
    cli.py                   # smarthome CLI
    mock_core.py             # Mock Core API для розробки
  tests/                     # pytest тести
  config/
    core.yaml.example
  docker-compose.yml
```

---

## Core API

Base URL: `http://localhost:7070/api/v1`
Auth: `Authorization: Bearer <module_token>`

| Метод | Шлях | Опис |
|-------|------|------|
| GET | `/health` | Статус ядра (без авторизації) |
| GET | `/devices` | Список пристроїв |
| POST | `/devices` | Реєстрація пристрою |
| GET | `/devices/{id}` | Конкретний пристрій |
| PATCH | `/devices/{id}/state` | Оновлення стану |
| DELETE | `/devices/{id}` | Видалення |
| POST | `/events/publish` | Публікація події |
| POST | `/events/subscribe` | Підписка на події (webhook) |
| GET | `/modules` | Список модулів |
| POST | `/modules/install` | Встановлення модуля (ZIP) |
| POST | `/modules/{name}/start` | Запуск модуля |
| POST | `/modules/{name}/stop` | Зупинка модуля |
| GET | `/integrity/status` | Статус Integrity Agent |
| GET | `/system/info` | Інформація про пристрій |

> Onboarding wizard обробляється через `POST /api/ui/setup/{step}` (UI-рівень, не потребує авторизації при першому старті).

Swagger UI: `http://localhost:7070/docs` — доступний лише при `DEBUG=true`.

---

## Голосовий асистент

Повністю офлайн — STT та TTS працюють без інтернету.

```
Wake-word (openWakeWord)
  → Запис аудіо
  → Whisper.cpp STT           ~0.8–2 сек
  → Speaker ID (resemblyzer)  ~200 мс
  → Fast Matcher (YAML)       ~50 мс
  → LLM Fallback (Ollama)     ~3–8 сек (лише Pi 5, 8GB)
  → Piper TTS                 ~300 мс
  → Історія (SQLite)
```

Підтримувані мови: `uk`, `en`.

---

## SDK для розробників модулів

```bash
smarthome new-module my-module    # створити структуру модуля
smarthome dev                     # запускити модуль на :8100 з hot-reload
smarthome test                    # запустити тести
smarthome publish                 # упакувати та завантажити в SelenaCore
```

Приклад модуля:

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
        pass  # запускається кожні 5 хвилин
```

---

## Змінні оточення

Скопіюйте `.env.example` → `.env`:

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

## Тести

```bash
pip install -r requirements-dev.txt

pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

---

## Безпека

- **Integrity Agent** — SHA256-перевірка файлів ядра кожні 30 сек
- **AES-256-GCM** — усі OAuth-токени зашифровані в `/secure/tokens/`
- **API proxy** — модулі ніколи не отримують токени напряму
- **Біометрія** — зберігається лише локально, синхронізація в хмару заблокована
- **Core API** — недоступний зовні localhost (iptables)
- **Rate limiting** — 120 зап/хв (зовнішні), 600 зап/хв (LAN); PIN: 5 спроб → блокування 10 хвилин

---

## Ліцензія

MIT — див. [LICENSE](../../LICENSE)

---

*SmartHome LK · SelenaCore v0.3.0-beta · 2026 · https://github.com/dotradepro/SelenaCore*
