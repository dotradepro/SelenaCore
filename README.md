<div align="center">

# SelenaCore

**Открытое локальное ядро умного дома для Raspberry Pi**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-teal.svg)](https://fastapi.tiangolo.com)

</div>

---

## Что это

SmartHome LK Core — открытый (MIT) хаб умного дома, который устанавливается на Raspberry Pi 4/5 или любой Linux SBC. Работает **полностью офлайн** — голосовой ассистент, автоматизации, управление устройствами — без подписки и без облака.

Три принципа:

- **Ядро неизменно** — SHA256-защита всех файлов ядра, Integrity Agent проверяет каждые 30 сек
- **Модули изолированы** — только HTTP/localhost:7070, без прямого доступа к данным ядра
- **Агент наблюдает** — IntegrityAgent: стоп модулей → уведомление → откат → SAFE MODE

---

## Быстрый старт

### Требования

- Raspberry Pi 4/5 (4–8 GB RAM) или любой Linux SBC
- Docker + Docker Compose
- Python 3.11+

### Запуск

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

cp .env.example .env
# Отредактируй .env при необходимости

docker compose up -d
```

**Core API:** `http://localhost:7070`
**UI (PWA):** `http://localhost:8080` или `http://smarthome.local:8080`

### Первый запуск — Onboarding Wizard

При первом старте (или без Wi-Fi) ядро поднимает точку доступа:

```
SSID:     SmartHome-Setup
Password: smarthome
```

Подключись с телефона → открой браузер → `192.168.4.1` → пройди 9-шаговый wizard.

---

## Архитектура

```
smarthome-core     ~420 MB    FastAPI, Device Registry, Event Bus,
                               Module Loader, Cloud Sync, Voice Core,
                               LLM Engine, UI Core

smarthome-modules  180-350 MB  Все пользовательские модули (Plugin Manager)

smarthome-sandbox  96-256 MB   Временный контейнер для тестирования (--rm)

smarthome-agent    systemd     Integrity Agent — независимый процесс
```

### Структура проекта

```
selena-core/
  core/
    main.py                  # FastAPI + asyncio точка входа
    config.py                # Настройки из .env + core.yaml
    registry/                # Device Registry (SQLAlchemy + SQLite)
    eventbus/                # Event Bus (asyncio.Queue + webhooks)
    module_loader/           # Plugin Manager + Docker sandbox
    api/routes/              # REST API endpoints
    cloud_sync/              # Синхронизация с платформой (HMAC)
  system_modules/
    voice_core/              # STT (Whisper), TTS (Piper), wake-word
    llm_engine/              # Ollama, Fast Matcher, Intent Router
    network_scanner/         # ARP, mDNS, SSDP, OUI lookup
    user_manager/            # Профили, PIN, Face ID, аудит-лог
    secrets_vault/           # AES-256-GCM хранилище токенов
    import_adapters/         # Home Assistant, Tuya, Philips Hue
    hw_monitor/              # CPU/RAM/диск мониторинг
    notify_push/             # Web Push VAPID
    backup_manager/          # Local/Cloud backup, QR-перенос
    remote_access/           # Tailscale VPN
    ui_core/                 # FastAPI :8080, PWA, wizard, TTY TUI
  agent/
    integrity_agent.py       # SHA256 периодическая проверка
    responder.py             # Цепочка реагирования + SAFE MODE
  sdk/
    base_module.py           # SmartHomeModule базовый класс
    cli.py                   # smarthome CLI
    mock_core.py             # Mock Core API для разработки
  tests/                     # pytest тесты
  config/
    core.yaml.example
  docker-compose.yml
```

---

## Core API

Base URL: `http://localhost:7070/api/v1`
Auth: `Authorization: Bearer <module_token>`

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Статус ядра (без авторизации) |
| GET | `/devices` | Список устройств |
| POST | `/devices` | Регистрация устройства |
| GET | `/devices/{id}` | Конкретное устройство |
| PATCH | `/devices/{id}/state` | Обновление состояния |
| DELETE | `/devices/{id}` | Удаление |
| POST | `/events/publish` | Публикация события |
| POST | `/events/subscribe` | Подписка на события (webhook) |
| GET | `/modules` | Список модулей |
| POST | `/modules/install` | Установка модуля (ZIP) |
| POST | `/modules/{name}/start` | Запуск модуля |
| POST | `/modules/{name}/stop` | Остановка модуля |
| GET | `/integrity/status` | Статус Integrity Agent |
| GET | `/system/info` | Информация об устройстве |
| POST | `/wizard/step` | Шаг onboarding wizard |

Полная документация: `http://localhost:7070/docs` (Swagger UI, автогенерация FastAPI).

---

## Голосовой ассистент

Полностью офлайн — STT и TTS работают без интернета.

```
Wake-word (openWakeWord)
  → Запись аудио
  → Whisper.cpp STT           ~0.8–2 сек
  → Speaker ID (resemblyzer)  ~200 ms
  → Fast Matcher (YAML)       ~50 ms
  → LLM Fallback (Ollama)     ~3–8 сек (только Pi 5, 8GB)
  → Piper TTS                 ~300 ms
  → История (SQLite)
```

Поддерживаемые языки: `ru`, `uk`, `en`.

---

## SDK для разработчиков модулей

```bash
smarthome new-module my-module    # создать структуру модуля
smarthome dev                     # mock Core API на :7070
smarthome test                    # запустить тесты
smarthome publish                 # упаковать и загрузить в SelenaCore
```

Пример модуля:

```python
from sdk.base_module import SmartHomeModule, on_event, scheduled

class ClimateModule(SmartHomeModule):
    name = "climate-module"
    version = "1.0.0"

    async def on_start(self):
        self.logger.info("Climate module started")

    @on_event("device.state_changed")
    async def handle_state(self, payload):
        device = await self.get_device(payload["device_id"])
        if device and device.get("state", {}).get("temperature", 0) > 25:
            await self.publish_event("climate.overheat", {"device_id": payload["device_id"]})

    @scheduled("every:5m")
    async def periodic_check(self):
        pass  # запускается каждые 5 минут
```

---

## Переменные окружения

Скопируй `.env.example` → `.env`:

```bash
CORE_PORT=7070
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
UI_PORT=8080
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
DEBUG=false
MOCK_PLATFORM=false
DEV_MODULE_TOKEN=test-module-token-xyz
```

---

## Тесты

```bash
pip install -r requirements-dev.txt

pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

---

## Безопасность

- **Integrity Agent** — SHA256-проверка файлов ядра каждые 30 сек
- **AES-256-GCM** — все OAuth-токены зашифрованы в `/secure/tokens/`
- **API proxy** — модули никогда не получают токены напрямую
- **Биометрия** — хранится только локально, синхронизация в облако заблокирована
- **Core API** — недоступен снаружи localhost (iptables)
- **Rate limiting** — 100 req/sec на токен; PIN: 5 попыток → блокировка 10 минут

---

## Лицензия

MIT — см. [LICENSE](LICENSE)

---

*SmartHome LK · SelenaCore v0.3.0-beta · 2026 · https://github.com/dotradepro/SelenaCore*
