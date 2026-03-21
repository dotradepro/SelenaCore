# Разработка модулей для SelenaCore

## Что такое модуль

Модуль — изолированный микросервис, который запускается в Docker-контейнере и общается с ядром **только** через Core API (`http://localhost:7070/api/v1`).

Модуль может:
- Регистрировать устройства в Device Registry
- Подписываться на события Event Bus через webhook
- Публиковать события (кроме `core.*`)
- Хранить OAuth-токены через Secrets Vault

Модуль **не может**:
- Читать `/secure/` напрямую
- Обращаться к SQLite ядра
- Публиковать `core.*` события
- Получить OAuth-токен напрямую (только через API proxy)
- Останавливать другие модули

---

## Структура модуля

Минимальная структура ZIP-архива:

```
my-module.zip
  manifest.json          ← обязательно
  main.py                ← точка входа
  requirements.txt       ← зависимости Python
  Dockerfile             ← как запускать
  icon.svg               ← иконка в UI (если type: UI)
```

---

## manifest.json

```json
{
  "name": "my-module",
  "version": "1.0.0",
  "description": "Краткое описание модуля",
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

### Обязательные поля

| Поле | Допустимые значения | Комментарий |
|------|--------------------|-------------|
| `name` | `[a-z0-9-]+` | RFC 1123 slug, уникальное имя |
| `version` | `1.2.3` | semver |
| `type` | `UI`, `INTEGRATION`, `DRIVER`, `AUTOMATION`, `IMPORT_SOURCE` | SYSTEM — только ядро |
| `api_version` | `"1.0"` | Текущая версия API |
| `port` | `8100`–`8200` | Порт модуля |
| `permissions` | см. ниже | Список полномочий |

### Разрешения (permissions)

| Разрешение | Доступно для типов | Описание |
|------------|-------------------|----------|
| `device.read` | все | GET /devices |
| `device.write` | все | POST/PATCH/DELETE /devices |
| `events.subscribe` | все | Подписка на события |
| `events.publish` | все | Публикация событий |
| `secrets.oauth` | только INTEGRATION | Запуск OAuth flow |
| `secrets.proxy` | только INTEGRATION | API proxy через vault |

### runtime_mode

| Значение | Поведение |
|----------|-----------|
| `always_on` | Запускается с ядром, перезапускается при сбое |
| `on_demand` | Запускается по запросу, остаётся пока активен |
| `scheduled` | Запускается по расписанию (cron-выражение) |

### ui_profile (только для type: UI)

| Профиль | Что отображается |
|---------|-----------------|
| `HEADLESS` | Нет UI |
| `SETTINGS_ONLY` | Только страница настроек |
| `ICON_SETTINGS` | Иконка в меню + настройки |
| `FULL` | Иконка + виджет на дашборде + настройки |

---

## SDK — base_module.py

```python
from sdk.base_module import SmartHomeModule, on_event, scheduled

class MyModule(SmartHomeModule):
    name = "my-module"
    version = "1.0.0"

    # === Lifecycle ===

    async def on_start(self):
        """Вызывается при запуске модуля."""
        self.logger.info("Module started")

    async def on_stop(self):
        """Вызывается при остановке модуля."""
        pass

    # === Event handlers ===

    @on_event("device.state_changed")
    async def handle_state_changed(self, payload: dict):
        """Вызывается при каждом изменении состояния устройства."""
        device_id = payload["device_id"]
        new_state = payload["new_state"]
        self.logger.debug(f"Device {device_id} → {new_state}")

    @on_event("device.offline")
    async def handle_offline(self, payload: dict):
        self.logger.warning(f"Device offline: {payload['device_id']}")

    # === Scheduled tasks ===

    @scheduled("every:5m")
    async def periodic_sync(self):
        """Вызывается каждые 5 минут."""
        devices = await self.list_devices()
        for device in devices:
            await self._sync_device(device)

    @scheduled("cron:0 * * * *")
    async def hourly_report(self):
        """Вызывается каждый час по cron."""
        pass

    # === Core API helpers ===

    async def _sync_device(self, device: dict):
        # Обновить состояние в Registry
        await self.update_device_state(
            device["device_id"],
            {"temperature": 22.5}
        )

        # Опубликовать событие
        await self.publish_event("climate.updated", {
            "device_id": device["device_id"],
            "temperature": 22.5
        })
```

### Доступные методы SmartHomeModule

```python
# Устройства
await self.list_devices()                         # все устройства
await self.get_device(device_id)                  # конкретное устройство
await self.register_device(name, type, protocol,  # создать устройство
                           capabilities, meta)
await self.update_device_state(device_id, state)  # обновить состояние
await self.delete_device(device_id)               # удалить

# События
await self.publish_event(event_type, payload)     # опубликовать событие
await self.subscribe_events(event_types,          # подписаться (webhook)
                            webhook_url)

# Свойства
self.logger          # logging.Logger с именем модуля
self.token           # module_token для заголовка Authorization
self.core_url        # http://localhost:7070/api/v1
```

---

## Локальная разработка

### Шаг 1 — Создать модуль

```bash
cd /your/workspace
smarthome new-module my-climate-module
# Создаёт: my-climate-module/manifest.json, main.py, Dockerfile, requirements.txt
```

### Шаг 2 — Запустить mock Core API

```bash
smarthome dev
# Запускает mock API на http://localhost:7070
# Все endpoints работают с in-memory хранилищем
# Токен разработки: DEV_MODULE_TOKEN из .env (по умолчанию "test-module-token-xyz")
```

### Шаг 3 — Разработать модуль

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

### Шаг 4 — Тесты

```bash
smarthome test
# Запускает pytest в контексте mock Core API
```

Пример теста:

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

### Шаг 5 — Установить в SelenaCore

```bash
smarthome publish --core http://localhost:7070
# Собирает ZIP, отправляет на POST /api/v1/modules/install
# Отслеживает статус через SSE
```

---

## Webhook от Event Bus

Если твой модуль подписался на события, ядро будет отправлять POST-запросы на твой webhook URL.

```python
# Подписка
await core_client.post("/api/v1/events/subscribe",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "event_types": ["device.state_changed", "device.offline"],
        "webhook_url": "http://localhost:8100/webhook/events"
    }
)
```

```python
# Обработчик webhook в модуле (FastAPI)
from fastapi import FastAPI, Request, HTTPException
import hmac
import hashlib

app = FastAPI()
WEBHOOK_SECRET = "..."  # получен при регистрации

@app.post("/webhook/events")
async def handle_event(request: Request):
    # Верифицировать HMAC-SHA256
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
    # ... обработка
    return {"ok": True}
```

---

## Структура manifest.json для OAuth интеграции

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

Использование в коде:

```python
# Начать OAuth flow (QR-код на экране)
await core_client.post("/api/v1/secrets/oauth/start",
    json={"module": "gmail-integration", "provider": "google",
          "scopes": ["gmail.readonly"]})

# Выполнить запрос к API — ядро подставит токен
resp = await core_client.post("/api/v1/secrets/proxy",
    json={
        "module": "gmail-integration",
        "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        "method": "GET"
    })
# Токен НИКОГДА не покидает ядро
```

---

## Типичные ошибки

| Ошибка | Причина | Решение |
|--------|---------|---------|
| `403 Forbidden` на `/events/publish` | Тип события начинается с `core.` | Переименуй тип события |
| `403 Forbidden` на `/modules/{name}/stop` | Пытаешься остановить SYSTEM модуль | Нельзя |
| `422 Unprocessable Entity` при установке | Ошибка в manifest.json | Проверь обязательные поля |
| `409 Conflict` при установке | Модуль с таким именем уже существует | Сначала DELETE |
| Webhook не доходит | Неверный `webhook_url` или модуль не слушает | Проверь порт в manifest.json |
| `400 Bad Request` на proxy | URL не https:// или приватный IP | Только публичные HTTPS endpoints |
