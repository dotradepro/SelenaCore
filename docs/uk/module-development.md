# Розробка модулів для SelenaCore

🇬🇧 [English version](../module-development.md)

## Що таке модуль

> **Примітка:** Цей посібник описує **користувацькі модулі** (типи: UI, INTEGRATION, DRIVER, AUTOMATION), які працюють у Docker-контейнерах.
> **Системні модулі** (тип: SYSTEM) працюють in-process всередині ядра. Вони наслідують `SystemModule` (`core/module_loader/system_module.py`) та спілкуються з ядром через прямі Python-виклики, не HTTP. Див. `AGENTS.md` §17.

Користувацький модуль — ізольований мікросервіс, який запускається в Docker-контейнері та спілкується з ядром **лише** через Core API (`http://localhost:7070/api/v1`).

Модуль може:
- Реєструвати пристрої в Device Registry
- Підписуватися на події Event Bus через webhook
- Публікувати події (крім `core.*`)
- Зберігати OAuth-токени через Secrets Vault

Модуль **не може**:
- Читати `/secure/` напряму
- Звертатися до SQLite ядра
- Публікувати `core.*` події
- Отримати OAuth-токен напряму (лише через API proxy)
- Зупиняти інші модулі

---

## Структура модуля

Мінімальна структура ZIP-архіву:

```
my-module.zip
  manifest.json          ← обов'язково
  main.py                ← точка входу
  requirements.txt       ← залежності Python
  Dockerfile             ← як запускати
  icon.svg               ← іконка в UI (якщо type: UI)
```

---

## manifest.json

```json
{
  "name": "my-module",
  "version": "1.0.0",
  "description": "Короткий опис модуля",
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
  "resources": {
    "memory_mb": 128,
    "cpu": 0.25
  },
  "author": "Your Name",
  "license": "MIT"
}
```

### Обов'язкові поля

| Поле | Допустимі значення | Коментар |
|------|--------------------|----------|
| `name` | `[a-z0-9-]+` | RFC 1123 slug, унікальне ім'я |
| `version` | `1.2.3` | semver |
| `type` | `UI`, `INTEGRATION`, `DRIVER`, `AUTOMATION`, `IMPORT_SOURCE` | SYSTEM — лише ядро |
| `api_version` | `"1.0"` | Поточна версія API |
| `port` | `8100`–`8200` | Порт модуля |
| `permissions` | див. нижче | Список дозволів |

### Дозволи (permissions)

| Дозвіл | Доступно для типів | Опис |
|--------|-------------------|------|
| `device.read` | всі | GET /devices |
| `device.write` | всі | POST/PATCH/DELETE /devices |
| `events.subscribe` | всі | Підписка на події |
| `events.publish` | всі | Публікація подій |
| `secrets.oauth` | лише INTEGRATION | Запуск OAuth flow |
| `secrets.proxy` | лише INTEGRATION | API proxy через vault |

### runtime_mode

| Значення | Поведінка |
|----------|-----------|
| `always_on` | Запускається з ядром, перезапускається при збої |
| `on_demand` | Запускається за запитом, залишається поки активний |
| `scheduled` | Запускається за розкладом (cron-вираз) |

### ui_profile (лише для type: UI)

| Профіль | Що відображається |
|---------|------------------|
| `HEADLESS` | Немає UI |
| `SETTINGS_ONLY` | Лише сторінка налаштувань |
| `ICON_SETTINGS` | Іконка в меню + налаштування |
| `FULL` | Іконка + віджет на дашборді + налаштування |

---

## SDK — base_module.py

```python
from sdk.base_module import SmartHomeModule, on_event, scheduled

class MyModule(SmartHomeModule):
    name = "my-module"
    version = "1.0.0"

    # === Lifecycle ===

    async def on_start(self):
        """Викликається при запуску модуля."""
        self._log.info("Module started")

    async def on_stop(self):
        """Викликається при зупинці модуля."""
        pass

    # === Event handlers ===

    @on_event("device.state_changed")
    async def handle_state_changed(self, payload: dict):
        """Викликається при кожній зміні стану пристрою."""
        device_id = payload["device_id"]
        new_state = payload["new_state"]
        self._log.debug(f"Device {device_id} → {new_state}")

    @on_event("device.offline")
    async def handle_offline(self, payload: dict):
        self._log.warning(f"Device offline: {payload['device_id']}")

    # === Scheduled tasks ===

    @scheduled("every:5m")
    async def periodic_sync(self):
        """Запускається кожні 5 хвилин."""
        import httpx, os
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{os.environ['SELENA_CORE_URL']}/devices",
                headers={"Authorization": f"Bearer {os.environ['SELENA_MODULE_TOKEN']}"}
            )
        for device in resp.json().get("devices", []):
            await self._sync_device(device)

    @scheduled("cron:0 * * * *")
    async def hourly_report(self):
        """Запускається щогодини за cron."""
        pass

    # === Core API helpers ===

    async def _sync_device(self, device: dict):
        import httpx, os
        async with httpx.AsyncClient() as client:
            await client.patch(
                f"{os.environ['SELENA_CORE_URL']}/devices/{device['device_id']}/state",
                headers={"Authorization": f"Bearer {os.environ['SELENA_MODULE_TOKEN']}"},
                json={"state": {"temperature": 22.5}}
            )
        await self.publish_event("climate.updated", {
            "device_id": device["device_id"],
            "temperature": 22.5
        })
```

### Доступні методи SmartHomeModule

```python
# Події (вбудовані)
await self.publish_event(event_type, payload)  # опублікувати подію

# Lifecycle
await self.on_start()   # перевизначте для пуску коду при старті модуля
await self.on_stop()    # перевизначте для пуску коду при зупинці модуля

# Core API — звертайтесь безпосередньо через httpx, використовуючи env-змінні:
import httpx, os

async with httpx.AsyncClient() as client:
    # Пристрої
    resp = await client.get(f"{os.environ['SELENA_CORE_URL']}/devices",
        headers={"Authorization": f"Bearer {os.environ['SELENA_MODULE_TOKEN']}"})
    devices = resp.json()["devices"]

    # Реєстрація пристрою
    resp = await client.post(f"{os.environ['SELENA_CORE_URL']}/devices",
        headers={"Authorization": f"Bearer {os.environ['SELENA_MODULE_TOKEN']}"},
        json={"name": "My Sensor", "type": "sensor",
              "protocol": "mqtt", "capabilities": []})

# Властивості self:
self._log          # logging.Logger з іменем модуля
self._core_token   # module_token для заголовка Authorization (= SELENA_MODULE_TOKEN)
# CORE_API_BASE    # http://localhost:7070/api/v1 (константа на рівні модуля)
```

---

## Локальна розробка

### Крок 1 — Створити модуль

```bash
cd /your/workspace
smarthome new-module my-climate-module
# Створює: my-climate-module/manifest.json, main.py, Dockerfile, requirements.txt
```

### Крок 2 — Запустити mock Core API

```bash
smarthome dev
# Запускає модуль на http://localhost:8100 з uvicorn hot-reload
# Задайте env-змінні вручну для dev-режиму:
export SELENA_MODULE_TOKEN=test-module-token-xyz
export SELENA_CORE_URL=http://localhost:7070/api/v1
export SELENA_MODULE_NAME=my-climate-module
export SELENA_MODULE_PORT=8100
```

### Крок 3 — Розробити модуль

```python
# main.py
from sdk.base_module import SmartHomeModule, on_event
from fastapi import FastAPI

app = FastAPI()
module = MyClimateModule()

@app.on_event("startup")
async def startup():
    await module.on_start()

@app.get("/health")
async def health():
    return {"status": "ok"}
```

### Крок 4 — Тести

```bash
smarthome test
# Запускає pytest в контексті mock Core API
```

Приклад тесту:

```python
import pytest
from httpx import AsyncClient
from sdk.mock_core import app as mock_app

@pytest.fixture
async def core_client():
    async with AsyncClient(app=mock_app, base_url="http://test") as c:
        yield c

async def test_device_registration(core_client):
    resp = await core_client.post(
        "/api/v1/devices",
        headers={"Authorization": "Bearer test-module-token-xyz"},
        json={"name": "Test Sensor", "type": "sensor",
              "protocol": "mqtt", "capabilities": []}
    )
    assert resp.status_code == 201
```

### Крок 5 — Встановити в SelenaCore

```bash
smarthome publish --core http://localhost:7070
# Збирає ZIP, відправляє на POST /api/v1/modules/install
# Відстежує статус через SSE
```

---

## Webhook від Event Bus

Якщо ваш модуль підписався на події, ядро буде відправляти POST-запити на ваш webhook URL.

```python
# Підписка
await core_client.post("/api/v1/events/subscribe",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "event_types": ["device.state_changed", "device.offline"],
        "webhook_url": "http://localhost:8100/webhook/events"
    }
)
```

```python
# Обробник webhook в модулі (FastAPI)
from fastapi import FastAPI, Request, HTTPException
import hmac
import hashlib

app = FastAPI()
WEBHOOK_SECRET = "..."  # отримано при реєстрації

@app.post("/webhook/events")
async def handle_event(request: Request):
    # Верифікувати HMAC-SHA256
    signature = request.headers.get("X-Selena-Signature", "")
    body = await request.body()
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401)

    event = await request.json()
    event_type = event["type"]
    payload = event["payload"]
    # ... обробка
    return {"ok": True}
```

---

## Структура manifest.json для OAuth інтеграції

```json
{
  "name": "gmail-integration",
  "type": "INTEGRATION",
  "permissions": [
    "secrets.oauth",
    "secrets.proxy"
  ],
  "oauth": {
    "provider": "google",
    "scopes": ["gmail.readonly", "gmail.send"]
  }
}
```

Використання в коді:

```python
# Почати OAuth flow (QR-код на екрані)
await core_client.post("/api/v1/secrets/oauth/start",
    json={"module": "gmail-integration", "provider": "google",
          "scopes": ["gmail.readonly"]})

# Виконати запит до API — ядро підставить токен
resp = await core_client.post("/api/v1/secrets/proxy",
    json={
        "module": "gmail-integration",
        "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        "method": "GET"
    })
# Токен НІКОЛИ не покидає ядро
```

---

## Типові помилки

| Помилка | Причина | Рішення |
|---------|---------|---------|
| `403 Forbidden` на `/events/publish` | Тип події починається з `core.` | Перейменуйте тип події |
| `403 Forbidden` на `/modules/{name}/stop` | Спроба зупинити SYSTEM модуль | Неможливо |
| `422 Unprocessable Entity` при встановленні | Помилка в manifest.json | Перевірте обов'язкові поля |
| `409 Conflict` при встановленні | Модуль з таким іменем вже існує | Спочатку DELETE |
| Webhook не доходить | Невірний `webhook_url` або модуль не слухає | Перевірте порт в manifest.json |
| `400 Bad Request` на proxy | URL не https:// або приватний IP | Лише публічні HTTPS endpoints |
