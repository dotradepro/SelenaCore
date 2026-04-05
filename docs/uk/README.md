<div align="center">

# SelenaCore

**Відкрите локальне ядро розумного дому для Raspberry Pi**

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal.svg)](https://fastapi.tiangolo.com)

[English version](../../README.md)

</div>

---

## Що таке SelenaCore

SelenaCore — це відкрите (MIT) ядро розумного дому, яке працює на Raspberry Pi 4/5 або будь-якому Linux SBC. Працює **повністю офлайн** — голосовий асистент, автоматизації, керування пристроями — без підписок, без хмари.

Три принципи:

- **Ядро незмінне** — SHA256-захист усіх файлів ядра, Integrity Agent перевіряє кожні 30 сек
- **Модулі ізольовані** — усі користувацькі модулі спілкуються виключно через WebSocket Module Bus
- **Агент стежить** — IntegrityAgent: зупинка модулів → сповіщення → відкат → SAFE MODE

---

## Швидкий старт

### Вимоги

- Raspberry Pi 4/5 (4-8 ГБ RAM), Jetson Orin або будь-який Linux SBC (ARM64/x86_64)
- Ubuntu 22.04+ (або Raspberry Pi OS)
- Docker + Docker Compose (автоматично встановлюється скриптом налаштування)

### Запуск (автоматичний)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

cp .env.example .env
# Вкажіть GEMINI_API_KEY та інші значення у .env

sudo bash scripts/setup.sh
```

Скрипт налаштування встановлює всі залежності, збирає Docker-образи, налаштовує сервіс кіоск-дисплея та запускає все автоматично.

### Запуск (ручний)

```bash
git clone https://github.com/dotradepro/SelenaCore.git
cd SelenaCore

cp .env.example .env
docker compose build
docker compose up -d
```

**Core API:** `http://localhost:7070`
**UI (PWA):** `http://localhost:80` або `http://smarthome.local:80`

### Перший запуск — Майстер налаштування

При першому запуску (або без Wi-Fi) ядро створює точку доступу:

```
SSID:     SmartHome-Setup
Password: smarthome
```

Підключіться з телефону, відкрийте браузер за адресою `192.168.4.1`, пройдіть 9-кроковий майстер.

---

## Архітектура

SelenaCore працює як єдиний FastAPI-додаток на порті 7070 з двома типами модулів:

```
┌───────────────────────────────────────────────────────┐
│                  SelenaCore (FastAPI :7070)            │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │           Module Bus (WebSocket Hub)             │  │
│  │         ws://core:7070/api/v1/bus               │──┼──── Користувацькі модулі
│  └──────────────────────┬──────────────────────────┘  │     (Docker-контейнери)
│                         │                             │
│  EventBus (asyncio.Queue, внутрішній pub/sub)         │
│  ├── voice_core       ├── llm_engine                  │
│  ├── ui_core          ├── automation_engine            │
│  ├── user_manager     ├── scheduler                   │
│  ├── device_watchdog  ├── protocol_bridge             │
│  ├── hw_monitor       ├── media_player                │
│  └── ще 12 системних модулів                         │
│                                                       │
│  Device Registry (SQLite)  │  Cloud Sync (HMAC)       │
│  Integrity Agent (SHA256)  │  i18n (uk, en)           │
└───────────────────────────────────────────────────────┘
```

**Системні модулі** (22 вбудовані) працюють у процесі через `importlib` — нуль мережевих витрат, прямий доступ до EventBus та бази даних.

**Користувацькі модулі** працюють у Docker-контейнерах і підключаються до ядра через **WebSocket Module Bus** за адресою `ws://core:7070/api/v1/bus`. Жодних окремих портів для модулів — уся комунікація проходить через єдину точку входу bus.

### Структура проєкту

```
selena-core/
  core/
    main.py                  # FastAPI + asyncio точка входу
    config.py                # Налаштування з .env + core.yaml
    module_bus.py            # WebSocket Module Bus (натхненний CAN-bus)
    registry/                # Device Registry (SQLAlchemy + SQLite)
    eventbus/                # Event Bus (asyncio.Queue)
    module_loader/           # Plugin Manager + Docker sandbox
    api/routes/              # REST API ендпоінти
    cloud_sync/              # Синхронізація з платформою (HMAC)
    i18n.py                  # Інтернаціоналізація
  system_modules/            # 22 вбудовані модулі в процесі
    voice_core/              # STT (Vosk), TTS (Piper), wake-word
    llm_engine/              # Ollama, Fast Matcher, Intent Router
    ui_core/                 # Веб UI сервер (:80)
    user_manager/            # Профілі, PIN, Face ID, журнал аудиту
    secrets_vault/           # AES-256-GCM сховище токенів
    ...                      # ще 17 модулів
  modules/                   # Встановлені користувачем модулі (Docker)
    weather-module/          # Приклад: погода через Open-Meteo
  agent/
    integrity_agent.py       # SHA256 періодична перевірка
    responder.py             # Ланцюг відповідей + SAFE MODE
  sdk/
    base_module.py           # Базовий клас SmartHomeModule + декоратори
    cli.py                   # smarthome CLI інструмент
  config/
    core.yaml.example        # Шаблон конфігурації
    locales/                 # Файли перекладу i18n
  tests/                     # Набір тестів pytest
  benchmarks/                # Бенчмарки продуктивності
  docker-compose.yml
```

---

## Core API

Базова URL-адреса: `http://localhost:7070/api/v1`
Автентифікація: `Authorization: Bearer <module_token>`

| Метод | Шлях | Опис |
|-------|------|------|
| GET | `/health` | Статус ядра (без автентифікації) |
| GET | `/system/info` | Інформація про систему |
| GET | `/devices` | Список пристроїв |
| POST | `/devices` | Реєстрація пристрою |
| GET | `/devices/{id}` | Конкретний пристрій |
| PATCH | `/devices/{id}/state` | Оновлення стану |
| DELETE | `/devices/{id}` | Видалення пристрою |
| POST | `/events/publish` | Публікація події |
| GET | `/modules` | Список модулів |
| POST | `/modules/install` | Встановлення модуля (ZIP) |
| POST | `/modules/{name}/start` | Запуск модуля |
| POST | `/modules/{name}/stop` | Зупинка модуля |
| GET | `/integrity/status` | Статус Integrity Agent |
| WS | `/bus?token=TOKEN` | Module Bus (WebSocket) |

Swagger UI: `http://localhost:7070/docs` — доступний лише коли `DEBUG=true`.

Повна довідка: [api-reference.md](../api-reference.md)

---

## Голосовий асистент

Повністю офлайн — STT та TTS працюють без інтернету.

```
Wake-word (openWakeWord)
  → Запис аудіо
  → Vosk STT (streaming)   ~0.3-0.5 сек
  → Speaker ID (resemblyzer)
  → Intent Router (4 рівні):
      1. Fast Matcher (YAML)           ~0 мс
      2. System Module Intents         ~мкс
      3. Module Bus Intents (WebSocket) ~мс
      4. Ollama LLM fallback           ~3-8 сек
  → Piper TTS              ~300 мс
```

Підтримувані мови: `uk`, `en`.

Повний посібник: [voice-settings.md](../voice-settings.md)

---

## Розробка модулів

Модулі спілкуються з ядром через **WebSocket Module Bus** — без окремих HTTP-серверів, без індивідуальних портів.

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

Повний посібник: [module-development.md](../module-development.md)

---

## Змінні середовища

Скопіюйте `.env.example` у `.env`:

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

Повна довідка: [configuration.md](../configuration.md)

---

## Тести

```bash
pip install -r requirements-dev.txt

pytest tests/ -v
pytest tests/ --cov=core --cov-report=term-missing
```

---

## Документація

| Документ | Опис |
|----------|------|
| [Архітектура](../architecture.md) | Дизайн системи, типи модулів, EventBus, послідовність завантаження |
| [Протокол Module Bus](../module-bus-protocol.md) | Довідка з протоколу WebSocket |
| [Розробка модулів](../module-development.md) | ��творення користувацьких модулів за допомогою SDK |
| [Розробка системних модулів](../system-module-development.md) | Створення вбудованих системних модулів |
| [Довідка з API](../api-reference.md) | REST API ендпоінти |
| [Конфігурація](../configuration.md) | Налаштування .env та core.yaml |
| [Розробка віджетів](../widget-development.md) | UI-віджети для модулів |
| [Розгортання](../deployment.md) | Встанов��ення та налаштування для продакшену |
| [Налаштування голосу](../voice-settings.md) | Конфігурація голосового конвеєра |
| [Налаштування кіоску](../kiosk-setup.md) | Конфігурація фізичного дисплея |
| [Менеджер користувачів та автентифікація](../user-manager-auth.md) | Автентифікація та безпека |
| [Внесок у проєкт](CONTRIBUTING.md) | Правила участі в розробці |

---

## Підтримати проєкт

SmartHome LK створюється одним розробником. Якщо ви вірите в те, що ми будуємо — інфраструктуру розумного дому з автономною AI-розробкою — розгляньте можливість спонсорства.

**Ваша підтримка фінансує:**
- Витрати на LLM API для AI-агентів кодування
- Docker sandbox хостинг для безпечного тестування модулів
- 6 місяців продакшен-інфраструктури
- Повну зосередженість на розробці

| Платформа | Посилання | Примітки |
|---|---|---|
| Ko-fi | [ko-fi.com/dotradepro](https://ko-fi.com/dotradepro) | Разова оплата / Трекер цілей / Усі рівні |
| GitHub Sponsors | [github.com/sponsors/dotradepro](https://github.com/sponsors/dotradepro) | Щомісячно або разово |

**Рівні:** Supporter $10 / Early Adopter $50 / Developer $100 (PRO 6 міс.) / Partner $500 (UNLIMITED назавжди) / Founding Sponsor $1000+

Дивіться [SPONSORS.md](SPONSORS.md) для повного списку підтримувачів та переваг рівнів.

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/dotradepro)

---

## Безпека

- **Integrity Agent** — SHA256-перевірка файлів ядра кожні 30 сек
- **AES-256-GCM** — усі OAuth-токени зашифровані у `/secure/tokens/`
- **Module Bus ACL** — контроль доступу на основі дозволів для кожного модуля
- **Біометрія** — зберігається лише локально, синхронізація з хмарою заблокована
- **Core API** — недоступний за межами localhost (iptables)
- **Обмеження запитів** — 120 запитів/хв; PIN: 5 спроб → блокування на 10 хв

---

## Ліцензія

MIT — дивіться [LICENSE](LICENSE)

---

*SmartHome LK / SelenaCore v0.3.0-beta / 2026 / https://github.com/dotradepro/SelenaCore*
