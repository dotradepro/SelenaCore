# docs/module-core-protocol.md — Протокол взаимодействия модулей и ядра

**Версия:** 1.0  
**Статус:** Нормативный документ — реализовывать строго по нему  
**Область:** `core/`, `core/module_loader/`, `sdk/base_module.py`, `sdk/mock_core.py`

---

## Обзор

Модуль и ядро общаются **только через HTTP на localhost**. Никаких других каналов нет и быть не должно. Прямой доступ модуля к SQLite, файловой системе `/secure/`, или к другим модулям — запрещён и заблокирован на уровне Docker network.

```
MODULE (:810X)                        CORE (:7070)
   │                                      │
   │──── HTTP Bearer token ─────────────►│  запросы к Core API
   │                                      │
   │◄─── POST /webhook/events ────────────│  доставка событий (HMAC)
   │◄─── POST /webhook/commands ──────────│  команды платформы (HMAC)
   │                                      │
   UI Core (:80) ──iframe──► GET /widget.html от модуля
```

---

## 1. Жизненный цикл модуля и выдача токена

### 1.1 Полный цикл установки

```
Пользователь загружает ZIP
        │
        ▼
POST /api/v1/modules/install (multipart/form-data, file=module.zip)
        │
        ▼
ModuleLoader.install():
  1. Распаковать ZIP → /var/lib/selena/modules/<name>/
  2. Validator.validate(manifest.json)
     → имя, версия, порт, permissions — строгая проверка
     → при ошибке: 422 + описание
  3. SandboxRunner.test()
     → docker run --rm smarthome-sandbox ...
     → timeout 60s
     → при провале: 400 + sandbox_output
  4. Сгенерировать module_token и сохранить:
       token_file = /secure/module_tokens/<name>.token
       Path(token_file).write_text(token)  # plaintext, chmod 600
       webhook_secret = secrets.token_hex(32) # хранится в памяти модуля через env
  5. Сформировать env-файл для контейнера:
       /var/lib/selena/modules/<name>/.env.module
       (содержимое — см. раздел 1.3)
  6. Запустить контейнер через DockerSandbox (см. раздел 1.4)
  7. Дождаться GET /health → 200 (timeout 30s)
  8. SDK внутри модуля автоматически подписывается на события
     из manifest.json при on_start (см. раздел 3.3)
  9. Вернуть ответ:
       201 { "name": "...", "status": "RUNNING", "port": 8100 }
     ⚠️ token НЕ возвращается в ответе — он уже внутри контейнера
```

### 1.2 Хранение токена

Токен хранится как plaintext файл `/secure/module_tokens/<name>.token` (chmod 600).
При проверке запроса ядро считывает все файлы `*.token` из этой директории и сравнивает с предъявленным токеном напрямую.
`DEV_MODULE_TOKEN` из `.env` принимается как дополнительный валидный токен в dev-режиме (`DEBUG=true`).

> **Примечание по безопасности:** В production хранилище токенов `/secure/` должно быть смонтировано с правами `700` и доступно только пользователю ядра.

### 1.3 Файл `.env.module` — передача секретов в контейнер

Ядро создаёт этот файл **до** запуска контейнера. Файл монтируется как `--env-file`. После старта контейнера — файл **удаляется** с диска (секреты живут только в памяти процесса).

```bash
# /var/lib/selena/modules/<name>/.env.module
# Создаётся ядром при установке. Удаляется сразу после docker run.

SELENA_MODULE_TOKEN=<raw_token_64_chars>
SELENA_WEBHOOK_SECRET=<webhook_secret_64_hex_chars>
SELENA_CORE_URL=http://localhost:7070/api/v1
SELENA_MODULE_NAME=<name>
SELENA_MODULE_PORT=<port>
```

**Почему удаляется:** файл на диске — потенциальная утечка. После передачи через `--env-file` переменные живут только в `/proc/<pid>/environ` контейнера, недоступном снаружи.

**Реализация удаления:**
```python
# core/module_loader/sandbox.py
import os, subprocess, tempfile

env_path = f"{install_path}/.env.module"
try:
    _write_env_file(env_path, token, webhook_secret, ...)
    proc = subprocess.run([
        "docker", "run", "--env-file", env_path, ...
    ])
finally:
    os.unlink(env_path)   # удалить сразу после вызова docker run
```

### 1.4 Docker run — параметры запуска

```python
# core/module_loader/sandbox.py

subprocess.run([
    "docker", "run",
    "--detach",
    "--name",        f"selena-module-{module.name}",
    "--network",     "selena_selena_internal",  # Docker Compose сеть selena_modules
    "--hostname",    module.name,
    "--publish",     f"127.0.0.1:{module.port}:{module.port}",  # только localhost
    "--env-file",    env_path,              # SELENA_* переменные
    "--memory",      f"{manifest.resources.memory_mb}m",
    "--cpus",        str(manifest.resources.cpu),
    "--pids-limit",  "100",
    "--read-only",                          # readonly rootfs
    "--tmpfs",       "/tmp:size=32m",       # только /tmp writable
    "--cap-drop",    "ALL",
    "--security-opt","no-new-privileges:true",
    "--restart",     restart_policy,        # always / no / on-failure
    "--label",       f"selena.module={module.name}",
    "--label",       f"selena.port={module.port}",
    f"selena-module-{module.name}:latest",  # image тег
])
```

`restart_policy`:
- `always_on` → `"always"`
- `on_demand` → `"no"`
- `scheduled` → `"no"` (ядро само управляет запуском)

### 1.5 Lifecycle при рестарте контейнера

Если контейнер рестартует (crash, OOM, `always` policy):

```
Контейнер рестартует
        │
        ▼
SDK.on_start() вызывается снова
        │
        ▼
SDK читает SELENA_MODULE_TOKEN из env (env живёт в памяти, не на диске)
        │
        ▼
SDK автоматически переподписывается на все события из manifest.json
(подписки хранятся в Event Bus в памяти → при рестарте ядра — тоже
 переподписка, см. раздел 3.4)
        │
        ▼
Модуль продолжает работу
```

**Токен при рестарте не меняется** — он хранится в `.env.module` до удаления, а затем живёт в `environ` контейнера. При рестарте Docker контейнера env переменные сохраняются (Docker хранит их в слое контейнера, не в файле). Токен остаётся валидным до деинсталляции модуля.

---

## 2. Аутентификация запросов: модуль → ядро

### 2.1 Схема Bearer token

Каждый HTTP запрос от модуля к Core API должен содержать заголовок:

```
Authorization: Bearer <module_token>
```

**Проверка на стороне ядра (`core/api/auth.py`):**

```python
# core/api/auth.py

import os
from pathlib import Path
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

bearer = HTTPBearer(auto_error=False)


def _load_valid_tokens() -> set[str]:
    """Load valid tokens from /secure/module_tokens/ directory."""
    tokens: set[str] = set()
    tokens_dir = Path(os.environ.get("CORE_SECURE_DIR", "/secure")) / "module_tokens"
    if tokens_dir.exists():
        for token_file in tokens_dir.glob("*.token"):
            token = token_file.read_text().strip()
            if token:
                tokens.add(token)
    dev_token = os.environ.get("DEV_MODULE_TOKEN", "")
    if dev_token:
        tokens.add(dev_token)
    return tokens


async def verify_module_token(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
) -> str:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = credentials.credentials
    valid_tokens = _load_valid_tokens()
    if token not in valid_tokens:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return token
```

> **Примечание:** В текущей реализации разрешения из manifest.json не проверяются
> на уровне отдельных эндпоинтов — любой валидный токен имеет полный доступ к API.
> Гранулярная проверка разрешений запланирована для v1.1.

**Использование в роутерах:**

```python
# core/api/routes/devices.py

@router.get("/devices")
async def list_devices(
    token: str = Depends(verify_module_token)
):
    ...

@router.post("/devices")
async def register_device(
    body: DeviceCreate,
    token: str = Depends(verify_module_token)
):
    ...
```

### 2.2 Таблица: эндпоинты по типу модуля

> **Примечание:** В текущей реализации (v1.0) тип модуля не проверяется на уровне эндпоинтов.
> Любой валидный токен имеет доступ ко всем эндпоинтам Public API.
> Приведённая таблица отражает **плановую** архитектуру для v1.1.

| Эндпоинт | INTEGRATION | DRIVER | AUTOMATION | SYSTEM |
|---|:---:|:---:|:---:|:---:|
| `GET /health` | ✅ | ✅ | ✅ | ✅ |
| `GET /devices` | ✅ | ✅ | ✅ | ✅ |
| `POST /devices` | ✅ | ✅ | — | ✅ |
| `PATCH /devices/{id}/state` | ✅ | ✅ | — | ✅ |
| `DELETE /devices/{id}` | — | ✅ | — | ✅ |
| `POST /events/publish` | ✅ | ✅ | ✅ | ✅ |
| `POST /events/subscribe` | ✅ | ✅ | ✅ | ✅ |
| `POST /secrets/oauth/start` | ✅ | — | — | ✅ |
| `POST /secrets/proxy` | ✅ | — | — | ✅ |
| `GET /modules` | — | — | — | ✅ |
| `POST /modules/install` | — | — | — | ✅ |
| `POST /modules/{name}/stop` | — | — | — | ✅ (не SYSTEM) |
| `GET /system/info` | — | — | — | ✅ |
| `GET /integrity/status` | — | — | — | ✅ |

### 2.3 Rate limiting

```python
# core/api/middleware.py
# Sliding window per-IP

LIMIT_LOCAL    = 600   # req/min для localhost и LAN (192.168.x, 10.x, 127.x)
LIMIT_EXTERNAL = 120   # req/min для внешних IP
WINDOW_SEC     = 60

# SSE-стрим и статические файлы — не учитываются
# При превышении: 429 Too Many Requests
# Header: Retry-After: <window_sec>
```

### 2.4 Ротация токена (деинсталляция)

Токен инвалидируется **только** при деинсталляции модуля:

```
DELETE /api/v1/modules/<name>   (только SYSTEM модуль или UI)
        │
        ▼
1. Docker stop selena-module-<name>
2. Docker rm selena-module-<name>
3. UPDATE modules SET status='REMOVED' WHERE name=<name>
   (token_hash остаётся в БД для аудита, но статус REMOVED → 401 при проверке)
4. Удалить /var/lib/selena/modules/<name>/
5. Event Bus: отписать все подписки этого модуля
```

Смена токена без деинсталляции не предусмотрена. Если токен скомпрометирован — только деинсталляция и повторная установка.

---

## 3. Доставка событий: ядро → модуль (Event Bus)

### 3.1 Схема Event Bus

```
Источник события                  Event Bus                  Подписчики
      │                          (asyncio.Queue)                   │
      │                                │                           │
PATCH /devices/{id}/state ──────► bus.publish(event) ────► delivery_worker
POST  /events/publish     ──────►       │                         │
                                        │              ┌──────────┘
                                        │              │
                                        ▼              ▼
                              фильтр по wildcard    POST http://localhost:810X/webhook/events
                                                    X-Selena-Signature: sha256=<hmac>
                                                    Content-Type: application/json
```

### 3.2 Формат события

```python
# Структура события (TypedDict)

class SelenaEvent(TypedDict):
    id:         str        # UUID, уникален для дедупликации
    type:       str        # "device.state_changed", "climate.updated", etc.
    source:     str        # имя модуля-издателя или "core"
    timestamp:  str        # ISO 8601, UTC
    payload:    dict       # произвольные данные


# Пример:
{
    "id":        "550e8400-e29b-41d4-a716-446655440000",
    "type":      "device.state_changed",
    "source":    "core",
    "timestamp": "2026-03-21T14:32:00.123Z",
    "payload": {
        "device_id":  "dev_abc123",
        "old_state":  {"temperature": 21.0},
        "new_state":  {"temperature": 22.4},
        "changed_by": "climate-control"
    }
}
```

### 3.3 Подписка на события

**Через API (модуль подписывается вручную):**

```python
# Модуль вызывает при старте:
POST /api/v1/events/subscribe
Authorization: Bearer <module_token>

{
    "event_types": ["device.state_changed", "device.offline"],
    "webhook_url": "http://localhost:8100/webhook/events"
    # wildcard: "device.*" — все события с префиксом device.
}
```

**Ответ:**
```json
{
    "subscription_id": "sub_xyz",
    "event_types": ["device.state_changed", "device.offline"],
    "webhook_url": "http://localhost:8100/webhook/events"
}
```

**Хранение подписок в памяти (Event Bus):**

```python
# core/eventbus/bus.py

class EventBus:
    # Подписки хранятся ТОЛЬКО в памяти.
    # При рестарте ядра — все модули переподписываются сами (см. 3.4)
    _subscriptions: dict[str, list[Subscription]] = {}
    # ключ: event_type или wildcard-паттерн
    # значение: список Subscription(module_name, webhook_url, webhook_secret)
```

**Запрет публикации `core.*` от модуля:**

```python
@router.post("/events/publish")
async def publish_event(
    body: EventPublish,
    module = Depends(require_permission("events.publish"))
):
    if body.event_type.startswith("core."):
        raise HTTPException(status_code=403,
            detail="Modules cannot publish core.* events")
    await bus.publish(SelenaEvent(
        id=str(uuid4()),
        type=body.event_type,
        source=module.name,
        timestamp=datetime.utcnow().isoformat() + "Z",
        payload=body.payload
    ))
    return {"published": True}
```

### 3.4 Переподписка при рестарте ядра

Поскольку подписки хранятся только в памяти, при рестарте ядра все модули теряют подписки. Механизм восстановления:

**Ядро при старте:**
```python
# core/main.py → startup event

async def on_startup():
    # 1. Запустить все модули со статусом RUNNING в БД
    running_modules = await db.fetch(
        "SELECT * FROM modules WHERE status='RUNNING' AND runtime_mode='always_on'"
    )
    for mod in running_modules:
        await module_loader.restart_container(mod)
    # Контейнеры сами вызовут on_start → переподпишутся
```

**SDK при старте модуля:**
```python
# sdk/base_module.py → SmartHomeModule.start()

async def start(self):
    # Вызывается при запуске контейнера (FastAPI startup event)
    self._token = os.environ["SELENA_MODULE_TOKEN"]
    self._webhook_secret = os.environ["SELENA_WEBHOOK_SECRET"]
    self._core_url = os.environ["SELENA_CORE_URL"]

    # Переподписаться на все события из manifest.json
    # (декораторы @on_event собирают список при импорте класса)
    await self._resubscribe_all()

    # Вызвать пользовательский on_start
    await self.on_start()


async def _resubscribe_all(self):
    """Регистрирует webhook для всех @on_event обработчиков."""
    event_types = list(self._event_handlers.keys())
    if not event_types:
        return
    webhook_url = f"http://localhost:{self._port}/webhook/events"
    await self._post("/events/subscribe", {
        "event_types": event_types,
        "webhook_url": webhook_url
    })
```

### 3.5 Доставка webhook и верификация HMAC

**Ядро отправляет:**

```python
# core/eventbus/delivery.py

import hmac, hashlib, json, httpx

async def deliver(subscription: Subscription, event: SelenaEvent):
    body = json.dumps(event, ensure_ascii=False).encode()
    signature = "sha256=" + hmac.new(
        subscription.webhook_secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                subscription.webhook_url,
                content=body,
                headers={
                    "Content-Type":      "application/json",
                    "X-Selena-Signature": signature,
                    "X-Event-Id":        event["id"],
                    "X-Event-Type":      event["type"],
                }
            )
        if resp.status_code not in (200, 204):
            logger.warning(f"Webhook delivery failed: {resp.status_code}")
    except httpx.TimeoutException:
        logger.error(f"Webhook timeout for {subscription.webhook_url}")
    # Retry не предусмотрен — модуль должен быть идемпотентен
```

**Модуль проверяет (SDK делает автоматически):**

```python
# sdk/base_module.py — webhook endpoint регистрируется автоматически

@app.post("/webhook/events")
async def _handle_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Selena-Signature", "")

    expected = "sha256=" + hmac.new(
        self._webhook_secret.encode(),
        body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = json.loads(body)
    event_type = event["type"]

    # Диспетчеризация по обработчикам
    handler = self._event_handlers.get(event_type)
    if handler is None:
        # Попробовать wildcard
        for pattern, h in self._event_handlers.items():
            if pattern.endswith(".*") and event_type.startswith(pattern[:-2]):
                handler = h
                break

    if handler:
        await handler(self, event["payload"])

    return {"ok": True}
```

**Декоратор `@on_event` — регистрация обработчиков:**

```python
# sdk/base_module.py

def on_event(event_type: str):
    """Декоратор. Регистрирует метод как обработчик события."""
    def decorator(func):
        func._on_event = event_type   # метка на функции
        return func
    return decorator


class SmartHomeModuleMeta(type):
    """Метакласс собирает все @on_event обработчики при создании класса."""
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        cls._event_handlers: dict[str, Callable] = {}
        for attr_name, attr in namespace.items():
            if callable(attr) and hasattr(attr, "_on_event"):
                cls._event_handlers[attr._on_event] = attr
        return cls


class SmartHomeModule(metaclass=SmartHomeModuleMeta):
    ...
```

---

## 4. UI-виджеты и settings.html

### 4.1 Как UI Core загружает виджет

UI Core (:80) рендерит главный экран. Для каждого модуля с `ui_profile != HEADLESS`:

```
UI Core получает список модулей:
GET http://localhost:7070/api/v1/modules
→ [ { name, port, manifest.ui.widget.size, status, ... } ]

Для каждого модуля со статусом RUNNING:
  Создаёт <iframe src="http://localhost:{port}/widget.html"
                  sandbox="allow-scripts allow-same-origin"
                  scrolling="no">

  Размер iframe определяется manifest.ui.widget.size:
    "1x1" → 1 ячейка × 1 строка сетки
    "2x1" → 2 ячейки × 1 строка
    "2x2" → 2 ячейки × 2 строки
    "4x1" → вся ширина × 1 строка
    "1x2" → 1 ячейка × 2 строки
```

### 4.2 Эндпоинты которые обязан отдавать каждый модуль

```
GET  /health          → {"status": "ok", "name": "<name>", "version": "..."}
GET  /widget.html     → HTML-файл виджета (manifest.ui.widget.file)
GET  /settings.html   → HTML-файл настроек (manifest.ui.settings)
GET  /icon.svg        → SVG иконка (manifest.ui.icon)
```

SDK регистрирует эти маршруты автоматически при старте:

```python
# sdk/base_module.py → register_static_routes()

def register_static_routes(self, app: FastAPI):
    install_path = Path(os.environ.get("SELENA_INSTALL_PATH", "."))

    @app.get("/health")
    async def health():
        return {"status": "ok", "name": self.name, "version": self.version}

    @app.get("/widget.html", response_class=HTMLResponse)
    async def widget():
        path = install_path / self._manifest["ui"]["widget"]["file"]
        return path.read_text()

    @app.get("/settings.html", response_class=HTMLResponse)
    async def settings():
        path = install_path / self._manifest["ui"]["settings"]
        return path.read_text()

    @app.get("/icon.svg", response_class=Response)
    async def icon():
        path = install_path / self._manifest["ui"]["icon"]
        return Response(content=path.read_bytes(), media_type="image/svg+xml")
```

### 4.3 Аутентификация запросов из widget.html

Виджет работает в iframe браузера. Для запросов к Core API из виджета:

```javascript
// Ядро передаёт read-only UI token в widget.html через query parameter при загрузке:
// GET /widget.html?ui_token=<ui_token>

// UI token — отдельный токен с ограниченными правами:
//   только: device.read, events.subscribe (read-only)
//   выдаётся UI Core при загрузке страницы, TTL = 1 час
//   НЕ является module_token

// widget.html получает его:
const params = new URLSearchParams(window.location.search)
const uiToken = params.get('ui_token')

// Запросы к Core API из виджета:
const resp = await fetch('http://localhost:7070/api/v1/devices', {
    headers: { 'Authorization': `Bearer ${uiToken}` }
})
```

**Выдача UI token — UI Core:**

```python
# core/system_modules/ui_core/routes.py

@router.get("/widget-frame/{module_name}")
async def widget_frame(module_name: str, user = Depends(require_user)):
    module = await module_loader.get(module_name)
    if not module or module.status != "RUNNING":
        raise HTTPException(404)

    # Сгенерировать краткосрочный UI token
    ui_token = await token_service.create_ui_token(
        scope=["device.read"],
        ttl_seconds=3600,
        issued_for=f"widget:{module_name}"
    )

    widget_url = f"http://localhost:{module.port}/widget.html?ui_token={ui_token}"
    # Вернуть iframe src
    return {"iframe_src": widget_url}
```

### 4.4 settings.html — механизм сохранения настроек

```
Пользователь открывает настройки модуля в UI:
  → GET http://localhost:{port}/settings.html?ui_token=<ui_token>

Настройки сохраняются через Core API (не напрямую в файл!):
  POST /api/v1/modules/{name}/config
  Authorization: Bearer <ui_token>
  { "key": "temperature_unit", "value": "celsius" }

Модуль читает свои настройки:
  GET /api/v1/modules/{name}/config
  Authorization: Bearer <module_token>
```

**Хранение настроек модуля в SQLite:**

```sql
CREATE TABLE module_config (
    module_name  TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,   -- JSON-сериализованное значение
    updated_at   DATETIME NOT NULL,
    PRIMARY KEY (module_name, key)
);
```

---

## 5. Secrets Vault и OAuth proxy

### 5.1 Запрос OAuth (только INTEGRATION + permission `secrets.oauth`)

```
Шаг 1: Модуль инициирует OAuth flow

POST /api/v1/secrets/oauth/start
Authorization: Bearer <module_token>
{
    "provider": "google",
    "scopes": ["gmail.readonly", "gmail.send"]
}

Ответ:
{
    "device_code":  "AH-1Bx...",
    "user_code":    "ABCD-EFGH",
    "verification_url": "https://accounts.google.com/device",
    "expires_in":   1800,
    "qr_data_url":  "data:image/png;base64,..."   # QR-код для UI
}

Шаг 2: UI Core показывает QR-код пользователю

Шаг 3: Ядро polling OAuth провайдера (background task)
        При получении токена:
          → шифровать AES-256-GCM
          → сохранить в /secure/tokens/<module_name>/google.enc
          → Event Bus: publish "core.oauth.completed" { module, provider }

Шаг 4: Модуль получает событие "core.oauth.completed"
        (SYSTEM модули могут подписаться на core.* события)
        Обычные INTEGRATION модули — получают через:
          GET /api/v1/secrets/oauth/status?provider=google
          → { "status": "completed" | "pending" | "expired" }
```

### 5.2 API proxy (только INTEGRATION + permission `secrets.proxy`)

```python
# Модуль делает запрос через ядро — токен НИКОГДА не покидает ядро

POST /api/v1/secrets/proxy
Authorization: Bearer <module_token>
{
    "provider": "google",
    "url":      "https://gmail.googleapis.com/gmail/v1/users/me/messages",
    "method":   "GET",
    "headers":  { "Accept": "application/json" },  # опционально
    "body":     null                                # опционально
}

# Ядро:
# 1. Проверяет url: только https://, блокирует private IP
# 2. Расшифровывает токен из /secure/tokens/<module>/google.enc
# 3. Добавляет Authorization: Bearer <decrypted_token> к запросу
# 4. Выполняет запрос с follow_redirects=False
# 5. Возвращает ответ провайдера:

{
    "status_code": 200,
    "headers": { "Content-Type": "application/json" },
    "body": { "messages": [...] }
}
```

**SSRF защита:**

```python
# core/system_modules/secrets_vault/proxy.py

import ipaddress, re
from urllib.parse import urlparse

BLOCKED_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
]

def validate_proxy_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Only https:// URLs allowed")
    host = parsed.hostname
    try:
        addr = ipaddress.ip_address(host)
        for net in BLOCKED_RANGES:
            if addr in net:
                raise ValueError(f"Private IP blocked: {host}")
    except ValueError:
        pass  # hostname — резолвится позже, дополнительная проверка при запросе
```

---

## 6. Cloud Sync — взаимодействие с платформой SmartHome LK

### 6.1 Heartbeat

```
Каждые 60 секунд:

POST https://smarthome-lk.com/api/v1/device/heartbeat
Headers:
  X-Device-Hash:  <PLATFORM_DEVICE_HASH из .env>
  X-Signature:    sha256=<hmac>
  Content-Type:   application/json

Body:
{
    "timestamp":  "2026-03-21T14:00:00Z",
    "status":     "online",
    "uptime":     86400,
    "modules": [
        { "name": "climate-control", "status": "RUNNING", "version": "1.2.1" }
    ],
    "integrity": {
        "status": "ok",
        "last_check": "2026-03-21T13:59:30Z",
        "files_checked": 847
    },
    "hardware": {
        "cpu_percent":  23.0,
        "ram_mb_used":  2100,
        "cpu_temp_c":   48.0
    }
}

HMAC вычисляется:
  key     = содержимое /secure/platform.key (AES-256-GCM ключ, читается и расшифровывается)
  message = json_body + "." + timestamp   (timestamp из заголовка запроса)
  sig     = hmac-sha256(key, message)
```

### 6.2 Long-poll команд

```
GET https://smarthome-lk.com/api/v1/device/commands
    ?device_hash=<hash>
    &wait=30
Headers:
  X-Signature: sha256=<hmac>

# Платформа держит соединение до 30 сек или пока нет команд

Ответ при наличии команды:
{
    "command_id": "cmd_abc123",
    "type":       "INSTALL_MODULE",   # или STOP_MODULE, REBOOT, SYNC_STATE, FACTORY_RESET
    "payload":    { ... }
}

После выполнения команды:
POST https://smarthome-lk.com/api/v1/device/commands/{command_id}/ack
{
    "success":   true,
    "error_msg": null
}
```

**Обработка команд:**

```python
# core/cloud_sync/command_handler.py

COMMAND_HANDLERS = {
    "INSTALL_MODULE": handle_install_module,    # payload: { zip_url, name }
    "STOP_MODULE":    handle_stop_module,       # payload: { name }
    "REBOOT":         handle_reboot,            # payload: {}
    "SYNC_STATE":     handle_sync_state,        # payload: {} → отправить полный статус
    "FACTORY_RESET":  handle_factory_reset,     # payload: { confirm_token }
}
```

### 6.3 Retry политика

```python
# Экспоненциальный backoff при недоступности платформы

delay = min(2 ** attempt, 300)  # максимум 5 минут
# attempt: 0→1s, 1→2s, 2→4s, ..., 8→256s, 9+→300s

# При OFFLINE: ядро продолжает работать полностью локально
# Платформа недоступна — не критично для локального функционала
```

---

## 7. Integrity Agent — взаимодействие с ядром

### 7.1 Независимость процесса

```
smarthome-agent.service (systemd)
  ↓
agent/integrity_agent.py
  ↓
НИКОГДА не делает: import core.*
НИКОГДА не делает: from core import ...

Взаимодействие ТОЛЬКО через:
  1. Файловую систему (/secure/, /var/lib/selena/)
  2. Docker CLI (subprocess)
  3. HTTP запрос к :7070 (для notify и status)
```

### 7.2 Алгоритм проверки

```python
# agent/integrity_agent.py

async def check_once() -> IntegrityResult:
    # 1. Прочитать master.hash
    master_hash = Path("/secure/master.hash").read_text().strip()

    # 2. Вычислить SHA256 от core.manifest
    manifest_bytes = Path("/secure/core.manifest").read_bytes()
    manifest_hash = sha256(manifest_bytes).hexdigest()

    if manifest_hash != master_hash:
        return IntegrityResult(status="MANIFEST_TAMPERED",
                               detail="core.manifest hash mismatch")

    # 3. Разобрать manifest (JSON: { "file_path": "expected_hash", ... })
    manifest = json.loads(manifest_bytes)
    violations = []

    for file_path, expected_hash in manifest.items():
        try:
            actual_hash = sha256(Path(file_path).read_bytes()).hexdigest()
            if actual_hash != expected_hash:
                violations.append({"file": file_path,
                                    "expected": expected_hash,
                                    "actual": actual_hash})
        except FileNotFoundError:
            violations.append({"file": file_path, "error": "missing"})

    if violations:
        return IntegrityResult(status="VIOLATED", violations=violations)

    return IntegrityResult(status="OK", files_checked=len(manifest))
```

### 7.3 Цепочка реакции при нарушении

```python
# agent/responder.py

async def respond_to_violation(result: IntegrityResult):
    log.critical(f"INTEGRITY VIOLATION: {result}")

    # Шаг 1: Остановить все модули через Docker CLI
    proc = subprocess.run(
        ["docker", "ps", "--filter", "label=selena.module", "-q"],
        capture_output=True, text=True
    )
    container_ids = proc.stdout.strip().split()
    for cid in container_ids:
        subprocess.run(["docker", "stop", "--time", "5", cid])

    # Шаг 2: Уведомить платформу (не через импорт core!)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "http://localhost:7070/api/v1/integrity/violation",
                json={"violations": result.violations},
                headers={"X-Agent-Secret": _read_agent_secret()}
            )
    except Exception:
        pass  # ядро недоступно — продолжаем rollback

    # Шаг 3: Попытка rollback (3 попытки с паузой 5 сек)
    for attempt in range(3):
        success = await attempt_rollback()
        if success:
            log.info("Rollback successful, restarting core")
            subprocess.run(["systemctl", "restart", "smarthome-core"])
            return
        await asyncio.sleep(5)

    # Шаг 4: SAFE MODE — если rollback не удался
    await enter_safe_mode()


async def attempt_rollback() -> bool:
    backup_dir = Path("/secure/core_backup")
    versions = sorted(backup_dir.iterdir(), reverse=True)
    if not versions:
        return False
    latest_backup = versions[0]
    # Копировать файлы из backup поверх текущих
    # Пересчитать core.manifest и master.hash
    ...


async def enter_safe_mode():
    # Записать флаг в файл
    Path("/var/lib/selena/SAFE_MODE").write_text("1")
    # Ядро при старте проверяет этот файл → ограничивает API
    subprocess.run(["systemctl", "restart", "smarthome-core"])
```

### 7.4 `/api/v1/integrity/violation` — endpoint ядра для агента

```python
# core/api/routes/integrity.py
# Защищён отдельным секретом агента (не module_token)

AGENT_SECRET = os.environ["INTEGRITY_AGENT_SECRET"]  # из .env

@router.post("/integrity/violation")
async def report_violation(
    body: ViolationReport,
    request: Request
):
    agent_secret = request.headers.get("X-Agent-Secret", "")
    if not hmac.compare_digest(agent_secret, AGENT_SECRET):
        raise HTTPException(status_code=403)

    # Активировать SAFE MODE в ядре немедленно
    core_state.safe_mode = True
    logger.critical(f"SAFE MODE activated by Integrity Agent: {body.violations}")
    return {"acknowledged": True}
```

**SAFE MODE в ядре:**

```python
# core/api/middleware.py

async def safe_mode_middleware(request: Request, call_next):
    if core_state.safe_mode:
        # Разрешить только GET запросы и /health
        if request.method != "GET" and request.url.path != "/api/v1/health":
            return JSONResponse(
                status_code=503,
                content={"error": "SAFE_MODE",
                         "detail": "Core is in safe mode. Only read operations allowed."}
            )
    return await call_next(request)
```

---

## 8. Среда разработки — mock Core API

### 8.1 DEV_MODULE_TOKEN

В режиме разработки (`smarthome dev`):

```bash
# .env
DEV_MODULE_TOKEN=test-module-token-xyz
MOCK_PLATFORM=true
```

Mock Core API принимает `DEV_MODULE_TOKEN` как валидный токен с правами SYSTEM. Модуль не нужно устанавливать — токен передаётся вручную.

### 8.2 Переменные окружения в dev-режиме

```bash
# Разработчик задаёт вручную при запуске модуля локально:
export SELENA_MODULE_TOKEN=test-module-token-xyz
export SELENA_WEBHOOK_SECRET=dev-webhook-secret-hex
export SELENA_CORE_URL=http://localhost:7070/api/v1
export SELENA_MODULE_NAME=my-module
export SELENA_MODULE_PORT=8100
export SELENA_INSTALL_PATH=.

python main.py
```

### 8.3 mock_core.py — что имитирует

```python
# sdk/mock_core.py — минимальная реализация для тестов

# Принимает любой Bearer токен как валидный
# Хранит устройства in-memory (dict)
# Event Bus: синхронная доставка в тот же процесс
# Secrets: токены не шифруются, хранятся in-memory
# HMAC подписи: вычисляются с SELENA_WEBHOOK_SECRET из env
```

---

## 9. Полная схема переменных окружения

### .env ядра

```bash
# Основные
CORE_PORT=7070
UI_PORT=80
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
DEBUG=false

# Платформа
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=                    # заполняется при регистрации
MOCK_PLATFORM=false                      # true = не подключаться к платформе

# Секреты (генерировать при установке)
INTEGRITY_AGENT_SECRET=<32 random bytes hex>  # для X-Agent-Secret заголовка

# Dev
DEV_MODULE_TOKEN=test-module-token-xyz   # только DEBUG=true
```

### .env.module (создаётся ядром, не редактируется вручную)

```bash
SELENA_MODULE_TOKEN=<64 chars base64url>
SELENA_WEBHOOK_SECRET=<64 chars hex>
SELENA_CORE_URL=http://localhost:7070/api/v1
SELENA_MODULE_NAME=<name>
SELENA_MODULE_PORT=<8100-8200>
SELENA_INSTALL_PATH=/var/lib/selena/modules/<name>
```

---

## 10. Сводная таблица — кто что читает и пишет

| Компонент | Читает | Пишет | Запрещено |
|---|---|---|---|
| Модуль | `SELENA_*` env vars | — | `/secure/`, SQLite ядра, другие модули |
| Core API | SQLite modules | SQLite modules | `/secure/` (только через Secrets Vault) |
| Secrets Vault | `/secure/tokens/<name>/` | `/secure/tokens/<name>/` | — |
| Integrity Agent | `/secure/core.manifest`, `/secure/master.hash` | `/var/lib/selena/SAFE_MODE` | `import core.*` |
| Cloud Sync | `/secure/platform.key` | — | — |
| Module Loader | `/var/lib/selena/modules/` | `/var/lib/selena/modules/`, `.env.module` (затем удаляет) | — |
| SDK (widget.html) | `ui_token` из URL query | — | `module_token`, `/secure/` |

---

## 11. Критерии готовности реализации

- [ ] `module_token` генерируется при установке, хранится как plaintext файл `/secure/module_tokens/<name>.token` (chmod 600)
- [ ] `.env.module` удаляется с диска сразу после `docker run`
- [ ] `webhook_secret` хранится в SQLite в plaintext, никогда не возвращается через API
- [ ] HMAC-SHA256 проверяется на каждый входящий webhook в SDK
- [ ] `core.*` события блокируются с 403 при попытке публикации от модуля
- [ ] При рестарте ядра все `always_on` модули перезапускаются и переподписываются
- [ ] UI token выдаётся UI Core, имеет TTL 1 час, права только `device.read`
- [ ] `GET /widget.html`, `/settings.html`, `/icon.svg`, `/health` регистрируются SDK автоматически
- [ ] SSRF protection: только `https://`, блокировка private IP ranges
- [ ] Integrity Agent не импортирует `core.*`, использует только subprocess и HTTP
- [ ] SAFE MODE: только GET запросы проходят при `core_state.safe_mode = True`
- [ ] `/api/v1/integrity/violation` защищён `INTEGRITY_AGENT_SECRET`, не `module_token`
