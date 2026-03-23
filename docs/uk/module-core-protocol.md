# docs/uk/module-core-protocol.md — Протокол взаємодії модулів і ядра

**Версія:** 1.0
**Статус:** Нормативний документ — реалізовувати строго за ним
**Область:** `core/`, `core/module_loader/`, `sdk/base_module.py`, `sdk/mock_core.py`

---

## Огляд

Модуль і ядро спілкуються **виключно через HTTP на localhost**. Жодних інших каналів немає і бути не повинно. Прямий доступ модуля до SQLite, файлової системи `/secure/`, або до інших модулів — заборонено і заблоковано на рівні Docker network.

```
MODULE (:810X)                        CORE (:7070)
   │                                      │
   │──── HTTP Bearer token ─────────────►│  запити до Core API
   │                                      │
   │◄─── POST /webhook/events ────────────│  доставка подій (HMAC)
   │◄─── POST /webhook/commands ──────────│  команди платформи (HMAC)
   │                                      │
   UI Core (:80) ──iframe──► GET /widget.html від модуля
```

---

## 1. Життєвий цикл модуля і видача токена

### 1.1 Повний цикл встановлення

```
Користувач завантажує ZIP
        │
        ▼
POST /api/v1/modules/install (multipart/form-data, file=module.zip)
        │
        ▼
ModuleLoader.install():
  1. Розпакувати ZIP → /var/lib/selena/modules/<name>/
  2. Validator.validate(manifest.json)
     → ім'я, версія, порт, permissions — сувора перевірка
     → при помилці: 422 + опис
  3. SandboxRunner.test()
     → docker run --rm smarthome-sandbox ...
     → timeout 60s
     → при провалі: 400 + sandbox_output
  4. Згенерувати module_token:
       token = secrets.token_urlsafe(48)     # 64 символи base64url
       token_hash = sha256(token)            # зберігати тільки хеш
       webhook_secret = secrets.token_hex(32) # 64 символи hex
  5. Записати в SQLite (таблиця modules):
       INSERT INTO modules (name, version, port, token_hash,
                            webhook_secret, permissions, type,
                            status, installed_at)
  6. Сформувати env-файл для контейнера:
       /var/lib/selena/modules/<name>/.env.module
       (вміст — див. розділ 1.3)
  7. Запустити контейнер через DockerSandbox (див. розділ 1.4)
  8. Дочекатися GET /health → 200 (timeout 30s)
  9. SDK всередині модуля автоматично підписується на події
     з manifest.json при on_start (див. розділ 3.3)
 10. Повернути відповідь:
       201 { "name": "...", "status": "RUNNING", "port": 8100 }
     ⚠️ token НЕ повертається у відповіді — він вже всередині контейнера
```

### 1.2 Таблиця `modules` в SQLite

```sql
CREATE TABLE modules (
    id            TEXT PRIMARY KEY,        -- UUID
    name          TEXT UNIQUE NOT NULL,    -- з manifest.json
    version       TEXT NOT NULL,
    port          INTEGER UNIQUE NOT NULL, -- 8100–8200
    token_hash    TEXT NOT NULL,           -- sha256(raw_token), hex
    webhook_secret TEXT NOT NULL,          -- plaintext (тільки в БД)
    permissions   TEXT NOT NULL,           -- JSON array
    type          TEXT NOT NULL,           -- UI|INTEGRATION|DRIVER|AUTOMATION|SYSTEM
    runtime_mode  TEXT NOT NULL,           -- always_on|on_demand|scheduled
    status        TEXT NOT NULL DEFAULT 'STOPPED',
    install_path  TEXT NOT NULL,           -- /var/lib/selena/modules/<name>/
    installed_at  DATETIME NOT NULL,
    started_at    DATETIME,
    error_msg     TEXT
);
```

### 1.3 Файл `.env.module` — передача секретів у контейнер

Ядро створює цей файл **до** запуску контейнера. Файл монтується як `--env-file`. Після старту контейнера — файл **видаляється** з диска (секрети живуть тільки в пам'яті процесу).

```bash
# /var/lib/selena/modules/<name>/.env.module
# Створюється ядром при встановленні. Видаляється одразу після docker run.

SELENA_MODULE_TOKEN=<raw_token_64_chars>
SELENA_WEBHOOK_SECRET=<webhook_secret_64_hex_chars>
SELENA_CORE_URL=http://localhost:7070/api/v1
SELENA_MODULE_NAME=<name>
SELENA_MODULE_PORT=<port>
```

**Чому видаляється:** файл на диску — потенційний витік. Після передачі через `--env-file` змінні живуть тільки в `/proc/<pid>/environ` контейнера, недоступному ззовні.

**Реалізація видалення:**
```python
# core/module_loader/docker_runner.py
import os, subprocess, tempfile

env_path = f"{install_path}/.env.module"
try:
    _write_env_file(env_path, token, webhook_secret, ...)
    proc = subprocess.run([
        "docker", "run", "--env-file", env_path, ...
    ])
finally:
    os.unlink(env_path)   # видалити одразу після виклику docker run
```

### 1.4 Docker run — параметри запуску

```python
# core/module_loader/docker_runner.py

subprocess.run([
    "docker", "run",
    "--detach",
    "--name",        f"selena-module-{module.name}",
    "--network",     "selena-net",          # ізольована мережа
    "--hostname",    module.name,
    "--publish",     f"127.0.0.1:{module.port}:{module.port}",  # тільки localhost
    "--env-file",    env_path,              # SELENA_* змінні
    "--memory",      f"{manifest.resources.memory_mb}m",
    "--cpus",        str(manifest.resources.cpu),
    "--pids-limit",  "100",
    "--read-only",                          # readonly rootfs
    "--tmpfs",       "/tmp:size=32m",       # тільки /tmp writable
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
- `scheduled` → `"no"` (ядро саме керує запуском)

### 1.5 Lifecycle при рестарті контейнера

Якщо контейнер рестартує (crash, OOM, `always` policy):

```
Контейнер рестартує
        │
        ▼
SDK.on_start() викликається знову
        │
        ▼
SDK читає SELENA_MODULE_TOKEN з env (env живе в пам'яті, не на диску)
        │
        ▼
SDK автоматично переписується на всі події з manifest.json
(підписки зберігаються в Event Bus в пам'яті → при рестарті ядра — теж
 переписка, див. розділ 3.4)
        │
        ▼
Модуль продовжує роботу
```

**Токен при рестарті не змінюється** — він зберігається в `.env.module` до видалення, а потім живе в `environ` контейнера. При рестарті Docker контейнера env змінні зберігаються (Docker зберігає їх у шарі контейнера, не у файлі). Токен залишається валідним до деінсталяції модуля.

---

## 2. Автентифікація запитів: модуль → ядро

### 2.1 Схема Bearer token

Кожен HTTP запит від модуля до Core API повинен містити заголовок:

```
Authorization: Bearer <module_token>
```

**Перевірка на стороні ядра (`core/api/auth.py`):**

```python
# core/api/auth.py

import hashlib, hmac
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from core.db import get_db

bearer = HTTPBearer()

async def verify_module_token(
    credentials: HTTPAuthorizationCredentials = Security(bearer),
    db = Depends(get_db)
) -> ModuleRecord:
    raw_token = credentials.credentials
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    module = await db.fetchone(
        "SELECT * FROM modules WHERE token_hash = ? AND status != 'REMOVED'",
        (token_hash,)
    )
    if not module:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return module   # прикріплюється до request.state.module


def require_permission(permission: str):
    """Декоратор перевірки permission."""
    def dependency(module: ModuleRecord = Depends(verify_module_token)):
        if permission not in json.loads(module.permissions):
            raise HTTPException(status_code=403,
                detail=f"Permission '{permission}' not granted in manifest.json")
        return module
    return dependency
```

**Використання в роутерах:**

```python
# core/api/routes/devices.py

@router.get("/devices")
async def list_devices(
    module = Depends(require_permission("device.read"))
):
    ...

@router.post("/devices")
async def register_device(
    body: DeviceCreate,
    module = Depends(require_permission("device.write"))
):
    ...
```

### 2.2 Таблиця: ендпоінти за типом модуля

| Ендпоінт | USER | INTEGRATION | DRIVER | AUTOMATION | SYSTEM |
|---|:---:|:---:|:---:|:---:|:---:|
| `GET /health` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `GET /devices` | — | `device.read` | `device.read` | `device.read` | ✅ |
| `POST /devices` | — | `device.write` | `device.write` | — | ✅ |
| `PATCH /devices/{id}/state` | — | `device.write` | `device.write` | — | ✅ |
| `DELETE /devices/{id}` | — | — | `device.write` | — | ✅ |
| `POST /events/publish` | — | `events.publish` | `events.publish` | `events.publish` | ✅ |
| `POST /events/subscribe` | — | `events.subscribe` | `events.subscribe` | `events.subscribe` | ✅ |
| `POST /secrets/oauth/start` | — | `secrets.oauth` | — | — | ✅ |
| `POST /secrets/proxy` | — | `secrets.proxy` | — | — | ✅ |
| `GET /modules` | — | — | — | — | ✅ |
| `POST /modules/install` | — | — | — | — | ✅ |
| `POST /modules/{name}/stop` | — | — | — | — | ✅ (не SYSTEM) |
| `GET /system/info` | — | — | — | — | ✅ |
| `GET /integrity/status` | — | — | — | — | ✅ |

`USER` — зарезервовано, не використовується модулями в поточній версії.

### 2.3 Rate limiting

```python
# core/api/middleware.py
# Реалізувати через sliding window в пам'яті (dict token_hash → deque timestamps)

RATE_LIMIT = 100   # запитів
RATE_WINDOW = 1.0  # за секунду

# При перевищенні: 429 Too Many Requests
# Header: Retry-After: 1
```

### 2.4 Ротація токена (деінсталяція)

Токен інвалідується **тільки** при деінсталяції модуля:

```
DELETE /api/v1/modules/<name>   (тільки SYSTEM модуль або UI)
        │
        ▼
1. Docker stop selena-module-<name>
2. Docker rm selena-module-<name>
3. UPDATE modules SET status='REMOVED' WHERE name=<name>
   (token_hash залишається в БД для аудиту, але статус REMOVED → 401 при перевірці)
4. Видалити /var/lib/selena/modules/<name>/
5. Event Bus: відписати всі підписки цього модуля
```

Зміна токена без деінсталяції не передбачена. Якщо токен скомпрометовано — тільки деінсталяція і повторне встановлення.

---

## 3. Доставка подій: ядро → модуль (Event Bus)

### 3.1 Схема Event Bus

```
Джерело події                    Event Bus                  Підписники
      │                          (asyncio.Queue)                   │
      │                                │                           │
PATCH /devices/{id}/state ──────► bus.publish(event) ────► delivery_worker
POST  /events/publish     ──────►       │                         │
                                        │              ┌──────────┘
                                        │              │
                                        ▼              ▼
                              фільтр по wildcard    POST http://localhost:810X/webhook/events
                                                    X-Selena-Signature: sha256=<hmac>
                                                    Content-Type: application/json
```

### 3.2 Формат події

```python
# Структура події (TypedDict)

class SelenaEvent(TypedDict):
    id:         str        # UUID, унікальний для дедуплікації
    type:       str        # "device.state_changed", "climate.updated", тощо
    source:     str        # ім'я модуля-видавця або "core"
    timestamp:  str        # ISO 8601, UTC
    payload:    dict       # довільні дані


# Приклад:
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

### 3.3 Підписка на події

**Через API (модуль підписується вручну):**

```python
# Модуль викликає при старті:
POST /api/v1/events/subscribe
Authorization: Bearer <module_token>

{
    "event_types": ["device.state_changed", "device.offline"],
    "webhook_url": "http://localhost:8100/webhook/events"
    # wildcard: "device.*" — всі події з префіксом device.
}
```

**Відповідь:**
```json
{
    "subscription_id": "sub_xyz",
    "event_types": ["device.state_changed", "device.offline"],
    "webhook_url": "http://localhost:8100/webhook/events"
}
```

**Зберігання підписок у пам'яті (Event Bus):**

```python
# core/eventbus/bus.py

class EventBus:
    # Підписки зберігаються ТІЛЬКИ в пам'яті.
    # При рестарті ядра — всі модулі переписуються самі (див. 3.4)
    _subscriptions: dict[str, list[Subscription]] = {}
    # ключ: event_type або wildcard-патерн
    # значення: список Subscription(module_name, webhook_url, webhook_secret)
```

**Заборона публікації `core.*` від модуля:**

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

### 3.4 Переписка при рестарті ядра

Оскільки підписки зберігаються тільки в пам'яті, при рестарті ядра всі модулі втрачають підписки. Механізм відновлення:

**Ядро при старті:**
```python
# core/main.py → startup event

async def on_startup():
    # 1. Запустити всі модулі зі статусом RUNNING в БД
    running_modules = await db.fetch(
        "SELECT * FROM modules WHERE status='RUNNING' AND runtime_mode='always_on'"
    )
    for mod in running_modules:
        await module_loader.restart_container(mod)
    # Контейнери самі викличуть on_start → переписуються
```

**SDK при старті модуля:**
```python
# sdk/base_module.py → SmartHomeModule.start()

async def start(self):
    # Викликається при запуску контейнера (FastAPI startup event)
    self._token = os.environ["SELENA_MODULE_TOKEN"]
    self._webhook_secret = os.environ["SELENA_WEBHOOK_SECRET"]
    self._core_url = os.environ["SELENA_CORE_URL"]

    # Переписатися на всі події з manifest.json
    # (декоратори @on_event збирають список при імпорті класу)
    await self._resubscribe_all()

    # Викликати користувацький on_start
    await self.on_start()


async def _resubscribe_all(self):
    """Реєструє webhook для всіх @on_event обробників."""
    event_types = list(self._event_handlers.keys())
    if not event_types:
        return
    webhook_url = f"http://localhost:{self._port}/webhook/events"
    await self._post("/events/subscribe", {
        "event_types": event_types,
        "webhook_url": webhook_url
    })
```

### 3.5 Доставка webhook та верифікація HMAC

**Ядро відправляє:**

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
    # Retry не передбачено — модуль повинен бути ідемпотентним
```

**Модуль перевіряє (SDK робить автоматично):**

```python
# sdk/base_module.py — webhook endpoint реєструється автоматично

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

    # Диспетчеризація за обробниками
    handler = self._event_handlers.get(event_type)
    if handler is None:
        # Спробувати wildcard
        for pattern, h in self._event_handlers.items():
            if pattern.endswith(".*") and event_type.startswith(pattern[:-2]):
                handler = h
                break

    if handler:
        await handler(self, event["payload"])

    return {"ok": True}
```

**Декоратор `@on_event` — реєстрація обробників:**

```python
# sdk/base_module.py

def on_event(event_type: str):
    """Декоратор. Реєструє метод як обробник події."""
    def decorator(func):
        func._on_event = event_type   # мітка на функції
        return func
    return decorator


class SmartHomeModuleMeta(type):
    """Метаклас збирає всі @on_event обробники при створенні класу."""
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

## 4. UI-віджети та settings.html

### 4.1 Як UI Core завантажує віджет

UI Core (:80) рендерить головний екран. Для кожного модуля з `ui_profile != HEADLESS`:

```
UI Core отримує список модулів:
GET http://localhost:7070/api/v1/modules
→ [ { name, port, manifest.ui.widget.size, status, ... } ]

Для кожного модуля зі статусом RUNNING:
  Створює <iframe src="http://localhost:{port}/widget.html"
                  sandbox="allow-scripts allow-same-origin"
                  scrolling="no">

  Розмір iframe визначається manifest.ui.widget.size:
    "1x1" → 1 комірка × 1 рядок сітки
    "2x1" → 2 комірки × 1 рядок
    "2x2" → 2 комірки × 2 рядки
    "4x1" → вся ширина × 1 рядок
    "1x2" → 1 комірка × 2 рядки
```

### 4.2 Ендпоінти які зобов'язаний віддавати кожен модуль

```
GET  /health          → {"status": "ok", "name": "<name>", "version": "..."}
GET  /widget.html     → HTML-файл віджета (manifest.ui.widget.file)
GET  /settings.html   → HTML-файл налаштувань (manifest.ui.settings)
GET  /icon.svg        → SVG іконка (manifest.ui.icon)
```

SDK реєструє ці маршрути автоматично при старті:

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

### 4.3 Автентифікація запитів з widget.html

Віджет працює у iframe браузера. Для запитів до Core API з віджета:

```javascript
// Ядро передає read-only UI token у widget.html через query parameter при завантаженні:
// GET /widget.html?ui_token=<ui_token>

// UI token — окремий токен з обмеженими правами:
//   тільки: device.read, events.subscribe (read-only)
//   видається UI Core при завантаженні сторінки, TTL = 1 година
//   НЕ є module_token

// widget.html отримує його:
const params = new URLSearchParams(window.location.search)
const uiToken = params.get('ui_token')

// Запити до Core API з віджета:
const resp = await fetch('http://localhost:7070/api/v1/devices', {
    headers: { 'Authorization': `Bearer ${uiToken}` }
})
```

**Видача UI token — UI Core:**

```python
# core/system_modules/ui_core/routes.py

@router.get("/widget-frame/{module_name}")
async def widget_frame(module_name: str, user = Depends(require_user)):
    module = await module_loader.get(module_name)
    if not module or module.status != "RUNNING":
        raise HTTPException(404)

    # Згенерувати короткостроковий UI token
    ui_token = await token_service.create_ui_token(
        scope=["device.read"],
        ttl_seconds=3600,
        issued_for=f"widget:{module_name}"
    )

    widget_url = f"http://localhost:{module.port}/widget.html?ui_token={ui_token}"
    # Повернути iframe src
    return {"iframe_src": widget_url}
```

### 4.4 settings.html — механізм збереження налаштувань

```
Користувач відкриває налаштування модуля в UI:
  → GET http://localhost:{port}/settings.html?ui_token=<ui_token>

Налаштування зберігаються через Core API (не напряму у файл!):
  POST /api/v1/modules/{name}/config
  Authorization: Bearer <ui_token>
  { "key": "temperature_unit", "value": "celsius" }

Модуль читає свої налаштування:
  GET /api/v1/modules/{name}/config
  Authorization: Bearer <module_token>
```

**Зберігання налаштувань модуля в SQLite:**

```sql
CREATE TABLE module_config (
    module_name  TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,   -- JSON-серіалізоване значення
    updated_at   DATETIME NOT NULL,
    PRIMARY KEY (module_name, key)
);
```

---

## 5. Secrets Vault та OAuth proxy

### 5.1 Запит OAuth (тільки INTEGRATION + permission `secrets.oauth`)

```
Крок 1: Модуль ініціює OAuth flow

POST /api/v1/secrets/oauth/start
Authorization: Bearer <module_token>
{
    "provider": "google",
    "scopes": ["gmail.readonly", "gmail.send"]
}

Відповідь:
{
    "device_code":  "AH-1Bx...",
    "user_code":    "ABCD-EFGH",
    "verification_url": "https://accounts.google.com/device",
    "expires_in":   1800,
    "qr_data_url":  "data:image/png;base64,..."   # QR-код для UI
}

Крок 2: UI Core показує QR-код користувачу

Крок 3: Ядро polling OAuth провайдера (background task)
        При отриманні токена:
          → зашифрувати AES-256-GCM
          → зберегти в /secure/tokens/<module_name>/google.enc
          → Event Bus: publish "core.oauth.completed" { module, provider }

Крок 4: Модуль отримує подію "core.oauth.completed"
        (SYSTEM модулі можуть підписатися на core.* події)
        Звичайні INTEGRATION модулі — отримують через:
          GET /api/v1/secrets/oauth/status?provider=google
          → { "status": "completed" | "pending" | "expired" }
```

### 5.2 API proxy (тільки INTEGRATION + permission `secrets.proxy`)

```python
# Модуль робить запит через ядро — токен НІКОЛИ не покидає ядро

POST /api/v1/secrets/proxy
Authorization: Bearer <module_token>
{
    "provider": "google",
    "url":      "https://gmail.googleapis.com/gmail/v1/users/me/messages",
    "method":   "GET",
    "headers":  { "Accept": "application/json" },  # опціонально
    "body":     null                                # опціонально
}

# Ядро:
# 1. Перевіряє url: тільки https://, блокує private IP
# 2. Розшифровує токен з /secure/tokens/<module>/google.enc
# 3. Додає Authorization: Bearer <decrypted_token> до запиту
# 4. Виконує запит з follow_redirects=False
# 5. Повертає відповідь провайдера:

{
    "status_code": 200,
    "headers": { "Content-Type": "application/json" },
    "body": { "messages": [...] }
}
```

**Захист від SSRF:**

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
        pass  # hostname — резолвиться пізніше, додаткова перевірка при запиті
```

---

## 6. Cloud Sync — взаємодія з платформою SmartHome LK

### 6.1 Heartbeat

```
Кожні 60 секунд:

POST https://selenehome.tech/api/v1/device/heartbeat
Headers:
  X-Device-Hash:  <PLATFORM_DEVICE_HASH з .env>
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

HMAC обчислюється:
  key     = вміст /secure/platform.key (AES-256-GCM ключ, читається і розшифровується)
  message = json_body + "." + timestamp   (timestamp із заголовка запиту)
  sig     = hmac-sha256(key, message)
```

### 6.2 Long-poll команд

```
GET https://selenehome.tech/api/v1/device/commands
    ?device_hash=<hash>
    &wait=30
Headers:
  X-Signature: sha256=<hmac>

# Платформа тримає з'єднання до 30 сек або поки немає команд

Відповідь при наявності команди:
{
    "command_id": "cmd_abc123",
    "type":       "INSTALL_MODULE",   # або STOP_MODULE, REBOOT, SYNC_STATE, FACTORY_RESET
    "payload":    { ... }
}

Після виконання команди:
POST https://selenehome.tech/api/v1/device/commands/{command_id}/ack
{
    "success":   true,
    "error_msg": null
}
```

**Обробка команд:**

```python
# core/cloud_sync/command_handler.py

COMMAND_HANDLERS = {
    "INSTALL_MODULE": handle_install_module,    # payload: { zip_url, name }
    "STOP_MODULE":    handle_stop_module,       # payload: { name }
    "REBOOT":         handle_reboot,            # payload: {}
    "SYNC_STATE":     handle_sync_state,        # payload: {} → відправити повний статус
    "FACTORY_RESET":  handle_factory_reset,     # payload: { confirm_token }
}
```

### 6.3 Retry політика

```python
# Експоненційний backoff при недоступності платформи

delay = min(2 ** attempt, 300)  # максимум 5 хвилин
# attempt: 0→1s, 1→2s, 2→4s, ..., 8→256s, 9+→300s

# При OFFLINE: ядро продовжує працювати повністю локально
# Платформа недоступна — не критично для локального функціоналу
```

---

## 7. Integrity Agent — взаємодія з ядром

### 7.1 Незалежність процесу

```
smarthome-agent.service (systemd)
  ↓
agent/integrity_agent.py
  ↓
НІКОЛИ не робить: import core.*
НІКОЛИ не робить: from core import ...

Взаємодія ТІЛЬКИ через:
  1. Файлову систему (/secure/, /var/lib/selena/)
  2. Docker CLI (subprocess)
  3. HTTP запит до :7070 (для notify та status)
```

### 7.2 Алгоритм перевірки

```python
# agent/integrity_agent.py

async def check_once() -> IntegrityResult:
    # 1. Прочитати master.hash
    master_hash = Path("/secure/master.hash").read_text().strip()

    # 2. Обчислити SHA256 від core.manifest
    manifest_bytes = Path("/secure/core.manifest").read_bytes()
    manifest_hash = sha256(manifest_bytes).hexdigest()

    if manifest_hash != master_hash:
        return IntegrityResult(status="MANIFEST_TAMPERED",
                               detail="core.manifest hash mismatch")

    # 3. Розібрати manifest (JSON: { "file_path": "expected_hash", ... })
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

### 7.3 Ланцюг реакції при порушенні

```python
# agent/responder.py

async def respond_to_violation(result: IntegrityResult):
    log.critical(f"INTEGRITY VIOLATION: {result}")

    # Крок 1: Зупинити всі модулі через Docker CLI
    proc = subprocess.run(
        ["docker", "ps", "--filter", "label=selena.module", "-q"],
        capture_output=True, text=True
    )
    container_ids = proc.stdout.strip().split()
    for cid in container_ids:
        subprocess.run(["docker", "stop", "--time", "5", cid])

    # Крок 2: Повідомити платформу (не через імпорт core!)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "http://localhost:7070/api/v1/integrity/violation",
                json={"violations": result.violations},
                headers={"X-Agent-Secret": _read_agent_secret()}
            )
    except Exception:
        pass  # ядро недоступне — продовжуємо rollback

    # Крок 3: Спроба rollback (3 спроби з паузою 5 сек)
    for attempt in range(3):
        success = await attempt_rollback()
        if success:
            log.info("Rollback successful, restarting core")
            subprocess.run(["systemctl", "restart", "smarthome-core"])
            return
        await asyncio.sleep(5)

    # Крок 4: SAFE MODE — якщо rollback не вдався
    await enter_safe_mode()


async def attempt_rollback() -> bool:
    backup_dir = Path("/secure/core_backup")
    versions = sorted(backup_dir.iterdir(), reverse=True)
    if not versions:
        return False
    latest_backup = versions[0]
    # Копіювати файли з backup поверх поточних
    # Перерахувати core.manifest та master.hash
    ...


async def enter_safe_mode():
    # Записати прапор у файл
    Path("/var/lib/selena/SAFE_MODE").write_text("1")
    # Ядро при старті перевіряє цей файл → обмежує API
    subprocess.run(["systemctl", "restart", "smarthome-core"])
```

### 7.4 `/api/v1/integrity/violation` — endpoint ядра для агента

```python
# core/api/routes/integrity.py
# Захищений окремим секретом агента (не module_token)

AGENT_SECRET = os.environ["INTEGRITY_AGENT_SECRET"]  # з .env

@router.post("/integrity/violation")
async def report_violation(
    body: ViolationReport,
    request: Request
):
    agent_secret = request.headers.get("X-Agent-Secret", "")
    if not hmac.compare_digest(agent_secret, AGENT_SECRET):
        raise HTTPException(status_code=403)

    # Активувати SAFE MODE в ядрі негайно
    core_state.safe_mode = True
    logger.critical(f"SAFE MODE activated by Integrity Agent: {body.violations}")
    return {"acknowledged": True}
```

**SAFE MODE в ядрі:**

```python
# core/api/middleware.py

async def safe_mode_middleware(request: Request, call_next):
    if core_state.safe_mode:
        # Дозволити тільки GET запити та /health
        if request.method != "GET" and request.url.path != "/api/v1/health":
            return JSONResponse(
                status_code=503,
                content={"error": "SAFE_MODE",
                         "detail": "Core is in safe mode. Only read operations allowed."}
            )
    return await call_next(request)
```

---

## 8. Середовище розробки — mock Core API

### 8.1 DEV_MODULE_TOKEN

У режимі розробки (`smarthome dev`):

```bash
# .env
DEV_MODULE_TOKEN=test-module-token-xyz
MOCK_PLATFORM=true
```

Mock Core API приймає `DEV_MODULE_TOKEN` як валідний токен з правами SYSTEM. Модуль не потрібно встановлювати — токен передається вручну.

### 8.2 Змінні оточення в dev-режимі

```bash
# Розробник задає вручну при запуску модуля локально:
export SELENA_MODULE_TOKEN=test-module-token-xyz
export SELENA_WEBHOOK_SECRET=dev-webhook-secret-hex
export SELENA_CORE_URL=http://localhost:7070/api/v1
export SELENA_MODULE_NAME=my-module
export SELENA_MODULE_PORT=8100
export SELENA_INSTALL_PATH=.

python main.py
```

### 8.3 mock_core.py — що імітує

```python
# sdk/mock_core.py — мінімальна реалізація для тестів

# Приймає будь-який Bearer токен як валідний
# Зберігає пристрої in-memory (dict)
# Event Bus: синхронна доставка в той самий процес
# Secrets: токени не шифруються, зберігаються in-memory
# HMAC підписи: обчислюються з SELENA_WEBHOOK_SECRET з env
```

---

## 9. Повна схема змінних оточення

### .env ядра

```bash
# Основні
CORE_PORT=7070
UI_PORT=80
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO
DEBUG=false

# Платформа
PLATFORM_API_URL=https://selenehome.tech/api/v1
PLATFORM_DEVICE_HASH=                    # заповнюється при реєстрації
MOCK_PLATFORM=false                      # true = не підключатися до платформи

# Секрети (генерувати при встановленні)
INTEGRITY_AGENT_SECRET=<32 random bytes hex>  # для X-Agent-Secret заголовка

# Dev
DEV_MODULE_TOKEN=test-module-token-xyz   # тільки DEBUG=true
```

### .env.module (створюється ядром, не редагується вручну)

```bash
SELENA_MODULE_TOKEN=<64 chars base64url>
SELENA_WEBHOOK_SECRET=<64 chars hex>
SELENA_CORE_URL=http://localhost:7070/api/v1
SELENA_MODULE_NAME=<name>
SELENA_MODULE_PORT=<8100-8200>
SELENA_INSTALL_PATH=/var/lib/selena/modules/<name>
```

---

## 10. Зведена таблиця — хто що читає і пише

| Компонент | Читає | Пише | Заборонено |
|---|---|---|---|
| Модуль | `SELENA_*` env vars | — | `/secure/`, SQLite ядра, інші модулі |
| Core API | SQLite modules | SQLite modules | `/secure/` (тільки через Secrets Vault) |
| Secrets Vault | `/secure/tokens/<name>/` | `/secure/tokens/<name>/` | — |
| Integrity Agent | `/secure/core.manifest`, `/secure/master.hash` | `/var/lib/selena/SAFE_MODE` | `import core.*` |
| Cloud Sync | `/secure/platform.key` | — | — |
| Module Loader | `/var/lib/selena/modules/` | `/var/lib/selena/modules/`, `.env.module` (потім видаляє) | — |
| SDK (widget.html) | `ui_token` з URL query | — | `module_token`, `/secure/` |

---

## 11. Критерії готовності реалізації

- [ ] `module_token` генерується при встановленні, зберігається як `sha256` хеш, plaintext тільки в `.env.module`
- [ ] `.env.module` видаляється з диска одразу після `docker run`
- [ ] `webhook_secret` зберігається в SQLite в plaintext, ніколи не повертається через API
- [ ] HMAC-SHA256 перевіряється на кожний вхідний webhook в SDK
- [ ] `core.*` події блокуються з 403 при спробі публікації від модуля
- [ ] При рестарті ядра всі `always_on` модулі перезапускаються і переписуються
- [ ] UI token видається UI Core, має TTL 1 година, права тільки `device.read`
- [ ] `GET /widget.html`, `/settings.html`, `/icon.svg`, `/health` реєструються SDK автоматично
- [ ] SSRF protection: тільки `https://`, блокування private IP ranges
- [ ] Integrity Agent не імпортує `core.*`, використовує тільки subprocess та HTTP
- [ ] SAFE MODE: тільки GET запити проходять при `core_state.safe_mode = True`
- [ ] `/api/v1/integrity/violation` захищений `INTEGRITY_AGENT_SECRET`, не `module_token`

---

*SelenaCore · Протокол взаємодії модулів і ядра · UK переклад · MIT*
