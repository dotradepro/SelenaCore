# AGENTS.md — Контракт агента SelenaCore
## SmartHome LK · Локальное ядро устройства · Читать ОБЯЗАТЕЛЬНО перед каждой сессией

---

## 0. ПЕРЕД НАЧАЛОМ — ОБЯЗАТЕЛЬНЫЙ ЧЕКЛИСТ

```
AGENTS.md          ← этот файл (прочитать полностью)
docs/TZ.md         ← техническое задание v0.3 (источник правды)
README.md          ← структура проекта, команды запуска
```

**Порядок работы — строго по шагам:**

```
1. Прочитать AGENTS.md (этот файл)
2. Прочитать docs/TZ.md — понять всю картину
3. Разбить ТЗ на задачи → создать Issues на GitHub
4. Взять одну задачу → реализовать → коммит → пуш
5. Закрыть Issue → взять следующую
```

> ⛔ Нельзя начинать писать код до создания Issue.
> ⛔ Нельзя брать вторую задачу пока первая не закрыта.
> ⛔ Нельзя пушить в main со сломанными тестами.

---

## 1. СТРУКТУРА ПРОЕКТА

```
selena-core/
  core/
    main.py                    # точка входа FastAPI + asyncio
    config.py                  # загрузка core.yaml + .env
    registry/
      service.py               # DeviceRegistry
      models.py                # SQLAlchemy ORM
    eventbus/
      bus.py                   # EventBus (asyncio.Queue)
      types.py                 # константы типов событий
    module_loader/
      loader.py                # Plugin Manager + lifecycle
      sandbox.py               # Docker-изоляция тестового контейнера
      validator.py             # валидация manifest.json
    api/
      routes/
        devices.py             # GET/POST /api/v1/devices
        events.py              # /api/v1/events/*
        modules.py             # /api/v1/modules/*
        integrity.py           # /api/v1/integrity/status
        system.py              # /api/v1/health, /api/v1/system/*
      auth.py                  # проверка module_token
      middleware.py            # CORS, X-Request-Id, rate limiting
    cloud_sync/
      sync.py                  # CloudSync (asyncio background task)
      commands.py              # обработчики команд платформы
  system_modules/
    voice_core/
      stt.py                   # Whisper.cpp wrapper
      tts.py                   # Piper wrapper
      wake_word.py             # openWakeWord
      speaker_id.py            # resemblyzer
      privacy.py               # режим приватности (GPIO + команда)
    llm_engine/
      ollama_client.py         # Ollama REST client
      intent_router.py         # Fast Matcher + LLM уровни
      fast_matcher.py          # keyword/regex правила
      model_manager.py         # загрузка и выбор моделей
    network_scanner/
      arp_scanner.py           # ARP sweep
      mdns_listener.py         # mDNS/Bonjour
      ssdp_listener.py         # SSDP/UPnP
      zigbee_scanner.py        # Zigbee через USB донгл
      classifier.py            # OUI lookup + автоклассификация
    user_manager/
      profiles.py              # CRUD профилей пользователей
      voice_biometric.py       # голосовые слепки (resemblyzer)
      face_auth.py             # видеоавторизация (face_recognition)
      audit_log.py             # аудит-лог действий
    secrets_vault/
      vault.py                 # AES-256-GCM хранилище
      oauth_flow.py            # Device Authorization Grant (RFC 8628)
      proxy.py                 # API-прокси для модулей
    backup_manager/
      local_backup.py          # USB/SD бэкап
      cloud_backup.py          # E2E облачный бэкап
      qr_transfer.py           # QR-перенос секретов
    remote_access/
      tailscale.py             # Tailscale VPN клиент
    hw_monitor/
      monitor.py               # CPU температура, RAM, диск
      throttle.py              # автоснижение нагрузки
    notify_push/
      vapid.py                 # Web Push VAPID
    ui_core/
      server.py                # FastAPI сервер :8080
      pwa.py                   # PWA manifest + service worker
      wizard.py                # Onboarding wizard endpoints
      routes/                  # страницы ui-core
  agent/
    integrity_agent.py         # Integrity Agent (отдельный процесс)
    manifest.py                # core.manifest + SHA256
    responder.py               # цепочка реагирования + SAFE MODE
  sdk/
    smarthome_sdk/
      base.py                  # SmartHomeModule базовый класс
      decorators.py            # @on_event, @schedule
      client.py                # Core API клиент
      cli.py                   # smarthome CLI (new-module, dev, test)
  config/
    core.yaml                  # конфигурация ядра
    logging.yaml               # конфигурация логирования
  tests/
    test_registry.py
    test_eventbus.py
    test_module_loader.py
    test_integrity.py
    test_api.py
    test_cloud_sync.py
    test_voice.py
    test_wizard.py
  requirements.txt
  requirements-dev.txt
  Dockerfile.core              # образ smarthome-core
  Dockerfile.modules           # образ smarthome-modules
  Dockerfile.sandbox           # образ smarthome-sandbox
  docker-compose.yml
  smarthome-core.service       # systemd юнит ядра
  smarthome-agent.service      # systemd юнит агента
  smarthome-modules.service    # systemd юнит контейнера модулей
  .env.example
  core.yaml.example
```

---

## 2. СТЕК И ВЕРСИИ

| Компонент | Версия | Назначение |
|---|---|---|
| Python | 3.11+ | Язык ядра |
| FastAPI | 0.111+ | HTTP сервер (Core API + UI Core) |
| SQLAlchemy | 2.0+ | ORM для SQLite |
| SQLite | встроен | Хранилище Device Registry, аудит-лог |
| Docker SDK (docker-py) | 7.0+ | Управление контейнерами |
| Whisper.cpp (pywhispercpp) | latest | STT локально |
| Piper (piper-tts) | latest | TTS локально |
| openWakeWord | latest | Wake-word детектор |
| resemblyzer | latest | Speaker ID (голосовые слепки) |
| face_recognition (dlib) | latest | Face ID |
| Ollama | latest | LLM runner (phi-3-mini, gemma-2b) |
| cryptography (Fernet/AES) | latest | Secrets vault шифрование |
| qrcode | latest | QR-коды (wizard, перенос) |
| bleak / bluez | latest | Bluetooth управление |
| pyaudio + ALSA | latest | Аудио I/O |
| pytest + httpx | latest | Тесты |

---

## 3. ПРАВИЛА НАПИСАНИЯ КОДА

### Python — общие правила

```python
# ✅ Правильно
class DeviceRegistry:
    async def get(self, device_id: str) -> Device | None:
        ...

    async def update_state(self, device_id: str, state: dict) -> Device:
        ...

# ❌ Неправильно — нет типов, нет async
class DeviceRegistry:
    def get(self, id):
        ...
```

- Все публичные методы — async
- Типизация обязательна (Python type hints)
- Один файл = одна ответственность
- Логирование через `logging.getLogger(__name__)` — никакого `print()`
- Исключения — через кастомные классы, не голый `raise Exception("...")`
- `X-Request-Id` пробрасывать через все сервисы через `contextvars`

### FastAPI — правила

```python
# ✅ Правильно — роутер только парсит и вызывает сервис
@router.get("/devices/{device_id}")
async def get_device(
    device_id: str,
    registry: DeviceRegistry = Depends(get_registry),
    token: str = Depends(verify_module_token),
) -> DeviceResponse:
    device = await registry.get(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return DeviceResponse.from_orm(device)

# ❌ Неправильно — бизнес-логика в роутере
@router.get("/devices/{device_id}")
async def get_device(device_id: str):
    db = sqlite3.connect("data.db")  # ← нельзя
    ...
```

- Роутер = только HTTP (parse → service → response)
- Вся логика в сервисах
- Pydantic модели для всех request/response
- Dependency Injection через `Depends()`
- HTTPException для всех ошибок

### Запрещённые паттерны

```python
# ⛔ Нельзя
print("debug")                   # только logging
import os; os.system("rm -rf")   # shell injection риск
eval(user_input)                 # RCE риск
open("/secure/platform.key")     # только через SecretVault API
subprocess.run(shell=True)       # только с конкретным списком аргументов
except:                          # только except Exception as e:
    pass                         # никогда пустой catch
```

---

## 3.1. ЛОКАЛИЗАЦИЯ (i18n) — ПРАВИЛА

### Основные языки

| Код | Язык | Статус |
|-----|------|--------|
| `en` | English | Основной (fallback) |
| `uk` | Українська | Основной |

### Инфраструктура

```
src/i18n/
  i18n.ts              # конфигурация i18next + changeLanguage()
  locales/
    en.ts              # English translations
    uk.ts              # Ukrainian translations
```

- Библиотека: `i18next` + `react-i18next`
- Язык по умолчанию: `en`
- Fallback: `en`
- Хранение выбранного языка: `localStorage('selena-lang')`
- Переключение: через `changeLanguage()` из `src/i18n/i18n.ts`

### Правила для фронтенда

```tsx
// ✅ Правильно — все строки через t()
import { useTranslation } from 'react-i18next';

function MyComponent() {
  const { t } = useTranslation();
  return <h1>{t('dashboard.welcomeHome')}</h1>;
}

// ❌ Неправильно — захардкоженный текст
function MyComponent() {
  return <h1>Добро пожаловать домой</h1>;
}
```

**Обязательные правила:**

- ⛔ Нельзя хардкодить UI-текст на каком-либо языке — только через `t('key')`
- Все ключи переводов хранятся в `src/i18n/locales/en.ts` и `src/i18n/locales/uk.ts`
- Структура ключей: `section.key` (например `dashboard.welcomeHome`, `wizard.selectLanguage`)
- При добавлении нового текста — добавлять перевод в ОБА файла (`en.ts` и `uk.ts`)
- Интерполяция: `t('devices.registryInfo', { count: 5 })` → `"5 devices registered."`
- Не использовать `t` как имя переменной в `map()` и циклах (конфликт с `useTranslation`)

### Правила для документации

- Вся документация (`docs/`, `README.md`, `CONTRIBUTING.md`) хранится на **двух языках**:
  - Основной файл — на английском
  - Украинская версия — в `docs/uk/` с суффиксом или в подпапке
- Формат: `docs/architecture.md` (EN) + `docs/uk/architecture.md` (UK)
- При изменении документации — обновлять ОБА языка

### Добавление нового языка

1. Создать файл `src/i18n/locales/<code>.ts` (скопировать структуру из `en.ts`)
2. Зарегистрировать в `src/i18n/i18n.ts` в `resources`
3. Добавить опцию в Wizard (шаг 1 — выбор языка)
4. Перевести все ключи
5. Добавить документацию в `docs/<code>/`

---

## 4. CORE API — ПОЛНАЯ СПЕЦИФИКАЦИЯ

Base URL: `http://localhost:7070/api/v1`
Auth: `Authorization: Bearer <module_token>`

### 4.1 Health

```http
GET /api/v1/health
Authorization: (не требуется)

Response 200:
{
  "status": "ok",
  "version": "0.3.0-beta",
  "mode": "normal",        // "normal" | "safe_mode"
  "uptime": 86400,
  "integrity": "ok"        // "ok" | "violated" | "restoring"
}
```

### 4.2 Device Registry

```http
GET /api/v1/devices
Authorization: Bearer <token>

Response 200:
{
  "devices": [
    {
      "device_id": "uuid-...",
      "name": "Термостат кухня",
      "type": "actuator",           // sensor | actuator | controller | virtual
      "protocol": "zigbee",
      "state": { "temperature": 22.5, "mode": "heat" },
      "capabilities": ["set_temperature", "set_mode"],
      "last_seen": 1710936000.0,
      "module_id": "climate-module",
      "meta": {}
    }
  ]
}
```

```http
POST /api/v1/devices
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Термостат кухня",
  "type": "actuator",
  "protocol": "zigbee",
  "capabilities": ["set_temperature", "set_mode"],
  "meta": { "zigbee_addr": "0x1234" }
}

Response 201:
{
  "device_id": "uuid-generated",
  "name": "Термостат кухня",
  "type": "actuator",
  "protocol": "zigbee",
  "state": {},
  "capabilities": ["set_temperature", "set_mode"],
  "last_seen": null,
  "module_id": null,
  "meta": { "zigbee_addr": "0x1234" }
}
```

```http
GET /api/v1/devices/{device_id}
Authorization: Bearer <token>

Response 200: <Device object>
Response 404: { "detail": "Device not found" }
```

```http
PATCH /api/v1/devices/{device_id}/state
Authorization: Bearer <token>
Content-Type: application/json

{
  "state": { "temperature": 23.0, "mode": "cool" }
}

Response 200: <Device object с обновлённым state>

// Автоматически публикует событие device.state_changed в Event Bus
```

```http
DELETE /api/v1/devices/{device_id}
Authorization: Bearer <token>

Response 204: (no content)
```

### 4.3 Event Bus

```http
POST /api/v1/events/publish
Authorization: Bearer <token>
Content-Type: application/json

{
  "type": "device.state_changed",
  "source": "climate-module",
  "payload": {
    "device_id": "uuid-...",
    "old_state": { "temperature": 22.0 },
    "new_state": { "temperature": 23.0 }
  }
}

Response 201:
{
  "event_id": "uuid-...",
  "type": "device.state_changed",
  "timestamp": 1710936000.0
}

// ⛔ Ошибка если type начинается с "core." — 403 Forbidden
Response 403:
{
  "detail": "Publishing core.* events is forbidden for modules"
}
```

```http
POST /api/v1/events/subscribe
Authorization: Bearer <token>
Content-Type: application/json

{
  "event_types": ["device.state_changed", "device.offline"],
  "webhook_url": "http://localhost:8100/webhook/events"
}

Response 201:
{
  "subscription_id": "sub-uuid-...",
  "event_types": ["device.state_changed", "device.offline"],
  "webhook_url": "http://localhost:8100/webhook/events"
}
```

**Доставка события на webhook модуля:**

```http
POST http://localhost:8100/webhook/events
Content-Type: application/json
X-Selena-Event: device.state_changed
X-Selena-Signature: sha256=<hmac>

{
  "event_id": "uuid-...",
  "type": "device.state_changed",
  "source": "climate-module",
  "payload": { ... },
  "timestamp": 1710936000.0
}
```

### 4.4 Module Loader

```http
GET /api/v1/modules
Authorization: Bearer <token>

Response 200:
{
  "modules": [
    {
      "name": "climate-module",
      "version": "1.0.0",
      "type": "UI",
      "status": "RUNNING",    // UPLOADED|VALIDATING|READY|RUNNING|STOPPED|ERROR|REMOVED
      "runtime_mode": "always_on",
      "port": 8100,
      "installed_at": 1710936000.0
    }
  ]
}
```

```http
POST /api/v1/modules/install
Authorization: Bearer <token>
Content-Type: multipart/form-data

module: <zip-архив>

Response 201:
{
  "name": "climate-module",
  "status": "VALIDATING",
  "message": "Module uploaded, validation in progress"
}

// Статусы приходят через SSE: GET /api/v1/modules/{name}/status/stream
```

```http
GET /api/v1/modules/{name}/status/stream
Authorization: Bearer <token>

// Server-Sent Events
data: {"status": "VALIDATING", "message": "Checking manifest.json..."}
data: {"status": "READY", "message": "Validation passed, starting..."}
data: {"status": "RUNNING", "message": "Module started on port 8100"}
```

```http
POST /api/v1/modules/{name}/stop
POST /api/v1/modules/{name}/start
DELETE /api/v1/modules/{name}
Authorization: Bearer <token>

Response 200: { "name": "climate-module", "status": "STOPPED" }
Response 403: если модуль типа SYSTEM
```

### 4.5 Integrity Status

```http
GET /api/v1/integrity/status
Authorization: Bearer <token>

Response 200:
{
  "status": "ok",              // "ok" | "violated" | "restoring" | "safe_mode"
  "last_check": 1710936000.0,
  "check_interval_sec": 30,
  "changed_files": [],         // список если violation
  "restore_attempts": 0,
  "safe_mode_since": null
}
```

### 4.6 Secrets (для интеграций)

```http
POST /api/v1/secrets/oauth/start
Authorization: Bearer <token>
Content-Type: application/json

{
  "module": "gmail-integration",
  "provider": "google",
  "scopes": ["gmail.readonly", "gmail.send"]
}

Response 201:
{
  "session_id": "oauth-uuid-...",
  "qr_code_url": "/api/v1/secrets/oauth/qr/oauth-uuid-...",
  "verification_uri": "https://accounts.google.com/device?user_code=XXXX",
  "user_code": "XXXX-YYYY",
  "expires_in": 1800,
  "poll_interval": 5
}
```

```http
GET /api/v1/secrets/oauth/status/{session_id}
Authorization: Bearer <token>

Response 200:
{
  "status": "pending",    // "pending" | "authorized" | "expired" | "error"
  "module": "gmail-integration"
}

// При status == "authorized":
{
  "status": "authorized",
  "module": "gmail-integration",
  "connected": true
  // токен НЕ возвращается — хранится в vault
}
```

```http
POST /api/v1/secrets/proxy
Authorization: Bearer <token>
Content-Type: application/json

{
  "module": "gmail-integration",
  "url": "https://gmail.googleapis.com/gmail/v1/users/me/messages",
  "method": "GET",
  "headers": { "Content-Type": "application/json" },
  "body": null
}

Response 200:
{
  "status_code": 200,
  "headers": { ... },
  "body": { ... }     // ответ от внешнего API
}

// Ядро подставляет токен, выполняет запрос, возвращает результат
// Модуль НИКОГДА не видит токен
```

### 4.7 System / Onboarding

```http
GET /api/v1/system/info
Authorization: (не требуется при первом запуске)

Response 200:
{
  "initialized": false,
  "wizard_completed": false,
  "version": "0.3.0-beta",
  "hardware": {
    "model": "Raspberry Pi 5 Model B Rev 1.0",
    "ram_total_mb": 8192,
    "has_hdmi": true,
    "has_camera": false
  },
  "audio": {
    "inputs": [
      { "id": "hw:1,0", "name": "USB Audio", "type": "usb" }
    ],
    "outputs": [
      { "id": "hw:0,0", "name": "bcm2835 Headphones", "type": "jack" },
      { "id": "bluez_sink.AA:BB:CC", "name": "JBL Flip", "type": "bluetooth" }
    ]
  },
  "display_mode": "framebuffer"
}
```

```http
POST /api/v1/wizard/step
Content-Type: application/json

{
  "step": "wifi",
  "data": {
    "ssid": "MyHomeNetwork",
    "password": "secret123"
  }
}

Response 200:
{
  "step": "wifi",
  "status": "ok",
  "next_step": "language",
  "message": "Connected to MyHomeNetwork. IP: 192.168.1.45"
}

// Доступные шаги: wifi | language | device_name | timezone |
//                 stt_model | tts_voice | admin_user | platform | import
```

---

## 5. MANIFEST.JSON — ПОЛНАЯ СХЕМА

```json
{
  "name": "climate-module",
  "version": "1.0.0",
  "description": "Управление климатом через Zigbee-термостаты",
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
  "oauth": null,
  "resources": {
    "memory_mb": 128,
    "cpu": 0.25
  },
  "author": "SmartHome LK",
  "license": "MIT",
  "homepage": "https://github.com/dotradepro/SelenaCore"
}
```

**Валидация manifest.json при установке:**

```python
REQUIRED_FIELDS = ["name", "version", "type", "api_version", "port", "permissions"]
VALID_TYPES = ["SYSTEM", "UI", "INTEGRATION", "DRIVER", "AUTOMATION", "IMPORT_SOURCE"]
VALID_PROFILES = ["HEADLESS", "SETTINGS_ONLY", "ICON_SETTINGS", "FULL"]
VALID_RUNTIME = ["always_on", "on_demand", "scheduled"]
ALLOWED_PERMISSIONS = [
    "device.read", "device.write",
    "events.subscribe", "events.publish",
    "secrets.oauth",     # только для INTEGRATION
    "secrets.proxy",     # только для INTEGRATION
]
VERSION_PATTERN = r"^\d+\.\d+\.\d+$"  # semver
```

---

## 6. СОБЫТИЯ EVENT BUS — ПОЛНЫЙ СПИСОК

```python
# Встроенные типы событий (публикует только ядро — core.*)
CORE_EVENTS = {
    "core.integrity_violation": "Агент обнаружил изменение файлов ядра",
    "core.integrity_restored":  "Агент успешно откатил изменения",
    "core.safe_mode_entered":   "Система перешла в SAFE MODE",
    "core.safe_mode_exited":    "SAFE MODE снят",
    "core.startup":             "Ядро запущено",
    "core.shutdown":            "Ядро останавливается",
}

# Устройства
DEVICE_EVENTS = {
    "device.state_changed":  "Изменилось состояние устройства в Registry",
    "device.registered":     "Новое устройство добавлено в Registry",
    "device.removed":        "Устройство удалено из Registry",
    "device.offline":        "Нет heartbeat > 90 сек",
    "device.online":         "Устройство снова доступно",
    "device.discovered":     "Сканер нашёл новое устройство в сети",
}

# Модули
MODULE_EVENTS = {
    "module.installed":  "Модуль установлен и запущен",
    "module.stopped":    "Модуль остановлен штатно",
    "module.started":    "Модуль запущен",
    "module.error":      "Модуль вернул ошибку или упал",
    "module.removed":    "Модуль удалён",
}

# Синхронизация с платформой
SYNC_EVENTS = {
    "sync.command_received":   "Получена команда от платформы",
    "sync.command_ack":        "Команда подтверждена",
    "sync.connection_lost":    "Потеряно соединение с платформой",
    "sync.connection_restored":"Соединение восстановлено",
}

# Голос
VOICE_EVENTS = {
    "voice.wake_word":      "Обнаружено wake-word",
    "voice.recognized":     "STT распознал запрос",
    "voice.intent":         "Intent Router определил намерение",
    "voice.response":       "TTS произносит ответ",
    "voice.privacy_on":     "Режим приватности включён",
    "voice.privacy_off":    "Режим приватности выключён",
}
```

---

## 7. INTEGRITY AGENT — АЛГОРИТМ

```python
# agent/integrity_agent.py — ОТДЕЛЬНЫЙ ПРОЦЕСС, не импортирует ядро

CORE_FILES_GLOB = "/opt/selena-core/core/**/*.py"
MANIFEST_PATH   = "/secure/core.manifest"
MASTER_HASH     = "/secure/master.hash"
BACKUP_DIR      = "/secure/core_backup/v0.3.0/"
LOG_PATH        = "/var/log/selena/integrity.log"

async def check_loop():
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SEC)  # 30 сек
        await run_check()

async def run_check():
    # 1. Верифицировать сам манифест
    manifest_hash = sha256_file(MANIFEST_PATH)
    stored_hash   = Path(MASTER_HASH).read_text().strip()
    if manifest_hash != stored_hash:
        await trigger_response("manifest_tampered", [MANIFEST_PATH])
        return

    # 2. Проверить каждый файл ядра
    manifest = json.loads(Path(MANIFEST_PATH).read_text())
    changed  = []
    for path, expected_hash in manifest.items():
        actual = sha256_file(path)
        if actual != expected_hash:
            changed.append({"path": path, "expected": expected_hash, "actual": actual})

    if changed:
        await trigger_response("files_changed", changed)

async def trigger_response(reason: str, changed: list):
    # Шаг 1: лог
    log_incident(reason, changed)

    # Шаг 2: стоп модулей
    await stop_all_modules()

    # Шаг 3: уведомить платформу
    await notify_platform(reason, changed)

    # Шаг 4: откат (3 попытки)
    for attempt in range(1, 4):
        success = await restore_from_backup(changed)
        if success:
            await restart_core()
            await notify_platform_restored()
            return
        await asyncio.sleep(5)

    # Шаг 5: SAFE MODE если откат не удался
    await enter_safe_mode()
    await notify_platform_safe_mode()
```

---

## 8. АУДИО-ПОДСИСТЕМА — РЕАЛИЗАЦИЯ

### Автодетект устройств

```python
# system_modules/voice_core/audio_manager.py

PRIORITY_INPUT  = ["usb", "i2s_gpio", "bluetooth", "hdmi", "builtin"]
PRIORITY_OUTPUT = ["usb", "i2s_gpio", "bluetooth", "hdmi", "jack"]

def detect_audio_devices() -> AudioDevices:
    devices = AudioDevices(inputs=[], outputs=[])

    # ALSA — все карты из /proc/asound/cards
    for card in parse_alsa_cards():
        dtype = classify_card(card)  # usb | i2s_gpio | hdmi | jack | builtin
        if has_capture(card):
            devices.inputs.append(AudioDevice(id=card.alsa_id, name=card.name, type=dtype))
        if has_playback(card):
            devices.outputs.append(AudioDevice(id=card.alsa_id, name=card.name, type=dtype))

    # PulseAudio / PipeWire — BT устройства
    if is_pulse_running():
        for sink in pactl_list_sinks():
            if "bluez" in sink.name:
                devices.outputs.append(AudioDevice(
                    id=sink.name, name=sink.description, type="bluetooth"
                ))
        for source in pactl_list_sources():
            if "bluez" in source.name:
                devices.inputs.append(AudioDevice(
                    id=source.name, name=source.description, type="bluetooth"
                ))

    # Сортировать по приоритету
    devices.inputs.sort(key=lambda d: priority_score(d.type, PRIORITY_INPUT))
    devices.outputs.sort(key=lambda d: priority_score(d.type, PRIORITY_OUTPUT))

    return devices
```

### I2S GPIO микрофон (INMP441 / SPH0645)

```bash
# /boot/config.txt — добавить overlay
dtoverlay=googlevoicehat-soundcard   # для INMP441 на GPIO 18-21
# ИЛИ
dtoverlay=i2s-mmap

# После reboot — проверить:
arecord -l
# **** List of CAPTURE Hardware Devices ****
# card 1: sndrpisimplecar [snd_rpi_simple_card], device 0: ...
```

### Bluetooth pairing через API

```python
# POST /api/v1/system/bluetooth/pair
# Запускает bluetoothctl scan + pair + trust + connect

async def pair_bluetooth_device(mac: str) -> bool:
    proc = await asyncio.create_subprocess_exec(
        "bluetoothctl",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    commands = [
        f"pair {mac}\n",
        f"trust {mac}\n",
        f"connect {mac}\n",
        "quit\n",
    ]
    stdout, _ = await proc.communicate(
        input="".join(commands).encode()
    )
    return proc.returncode == 0
```

---

## 9. OAUTH ЧЕРЕЗ QR — РЕАЛИЗАЦИЯ

```python
# system_modules/secrets_vault/oauth_flow.py
# RFC 8628 — Device Authorization Grant

PROVIDERS = {
    "google": {
        "device_auth_url": "https://oauth2.googleapis.com/device/code",
        "token_url":       "https://oauth2.googleapis.com/token",
        "client_id":       env("GOOGLE_CLIENT_ID"),
        "client_secret":   env("GOOGLE_CLIENT_SECRET"),
    },
    "tuya": {
        "device_auth_url": "https://auth.tuya.com/oauth/device/code",
        "token_url":       "https://auth.tuya.com/oauth/token",
        "client_id":       env("TUYA_CLIENT_ID"),
        "client_secret":   env("TUYA_CLIENT_SECRET"),
    },
}

async def start_oauth_flow(module: str, provider: str, scopes: list[str]) -> OAuthSession:
    cfg = PROVIDERS[provider]

    # Шаг 1: запросить device_code
    resp = await http.post(cfg["device_auth_url"], data={
        "client_id": cfg["client_id"],
        "scope": " ".join(scopes),
    })
    data = resp.json()
    # data: { device_code, user_code, verification_uri, interval, expires_in }

    # Шаг 2: сгенерировать QR
    qr_url = f"{data['verification_uri']}?user_code={data['user_code']}"
    qr_img = generate_qr(qr_url)

    # Шаг 3: сохранить сессию + запустить polling
    session = OAuthSession(module=module, provider=provider,
                           device_code=data["device_code"],
                           interval=data["interval"])
    asyncio.create_task(poll_for_token(session, cfg))

    return session

async def poll_for_token(session: OAuthSession, cfg: dict):
    while not session.expired:
        await asyncio.sleep(session.interval)
        resp = await http.post(cfg["token_url"], data={
            "client_id":     cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "device_code":   session.device_code,
            "grant_type":    "urn:ietf:params:oauth:grant-type:device_code",
        })
        if resp.status_code == 200:
            tokens = resp.json()
            # Зашифровать и сохранить
            await vault.store(session.module, tokens)
            session.status = "authorized"
            return
        elif resp.json().get("error") == "authorization_pending":
            continue
        else:
            session.status = "error"
            return
```

---

## 10. РАЗБИВКА ТЗ НА ЗАДАЧИ И GITHUB ISSUES

> Перед началом работы агент ОБЯЗАН создать все Issues на GitHub по этому плану.
> Репозиторий: **https://github.com/dotradepro/SelenaCore**

### Фаза 1 — Инициализация проекта

| Issue | Заголовок | Labels |
|---|---|---|
| #1 | `chore: init project structure, Dockerfile, docker-compose` | `phase-1`, `chore`, `infra` |
| #2 | `chore: setup SQLite + SQLAlchemy models (Device, AuditLog)` | `phase-1`, `chore`, `backend` |
| #3 | `chore: setup FastAPI skeleton, health endpoint, middleware` | `phase-1`, `chore`, `backend` |
| #4 | `chore: systemd units + watchdog configuration` | `phase-1`, `chore`, `infra` |

### Фаза 2 — Ядро (Core API)

| Issue | Заголовок | Labels |
|---|---|---|
| #5 | `feat(registry): Device Registry CRUD + state history` | `phase-2`, `feat`, `backend` |
| #6 | `feat(eventbus): Event Bus asyncio.Queue + webhook delivery` | `phase-2`, `feat`, `backend` |
| #7 | `feat(api): Core API /devices endpoints + module_token auth` | `phase-2`, `feat`, `backend` |
| #8 | `feat(api): Core API /events endpoints + core.* protection` | `phase-2`, `feat`, `backend` |
| #9 | `feat(loader): Plugin Manager + manifest validation` | `phase-2`, `feat`, `backend` |
| #10 | `feat(loader): Module install/start/stop via Docker sandbox` | `phase-2`, `feat`, `backend` |
| #11 | `feat(api): Module Loader API /modules endpoints + SSE status` | `phase-2`, `feat`, `backend` |

### Фаза 3 — Integrity Agent

| Issue | Заголовок | Labels |
|---|---|---|
| #12 | `feat(agent): SHA256 manifest creation on first init` | `phase-3`, `feat`, `security` |
| #13 | `feat(agent): periodic file check loop (30s interval)` | `phase-3`, `feat`, `security` |
| #14 | `feat(agent): response chain: stop modules → notify → restore` | `phase-3`, `feat`, `security` |
| #15 | `feat(agent): SAFE MODE — read-only Core API, no module start` | `phase-3`, `feat`, `security` |

### Фаза 4 — Cloud Sync

| Issue | Заголовок | Labels |
|---|---|---|
| #16 | `feat(sync): heartbeat ping to SmartHome LK platform` | `phase-4`, `feat`, `backend` |
| #17 | `feat(sync): long-poll command receiver + ACK` | `phase-4`, `feat`, `backend` |
| #18 | `feat(sync): handle INSTALL_MODULE, STOP_MODULE, REBOOT commands` | `phase-4`, `feat`, `backend` |
| #19 | `feat(sync): integrity event reporting to platform` | `phase-4`, `feat`, `security` |

### Фаза 5 — UI Core + Onboarding

| Issue | Заголовок | Labels |
|---|---|---|
| #20 | `feat(ui): FastAPI server :8080 + static files + PWA manifest` | `phase-5`, `feat`, `frontend` |
| #21 | `feat(ui): AP mode + QR code generation on first boot` | `phase-5`, `feat`, `frontend` |
| #22 | `feat(ui): wizard endpoints (9 steps: wifi→import)` | `phase-5`, `feat`, `frontend` |
| #23 | `feat(ui): dashboard page + device list + module list` | `phase-5`, `feat`, `frontend` |
| #24 | `feat(ui): display mode autodetect (headless/kiosk/framebuffer/tty)` | `phase-5`, `feat`, `infra` |
| #25 | `feat(ui): TTY1 Textual TUI status display` | `phase-5`, `feat`, `frontend` |
| #26 | `feat(ui): Service Worker + offline page (PWA)` | `phase-5`, `feat`, `frontend` |

### Фаза 6 — Системные модули: Аудио и Голос

| Issue | Заголовок | Labels |
|---|---|---|
| #27 | `feat(voice): audio device autodetect (USB/I2S/BT/HDMI/jack)` | `phase-6`, `feat`, `voice` |
| #28 | `feat(voice): Whisper.cpp STT wrapper + streaming` | `phase-6`, `feat`, `voice` |
| #29 | `feat(voice): Piper TTS wrapper + voice selection` | `phase-6`, `feat`, `voice` |
| #30 | `feat(voice): openWakeWord integration + background loop` | `phase-6`, `feat`, `voice` |
| #31 | `feat(voice): resemblyzer Speaker ID + enrollment flow` | `phase-6`, `feat`, `voice` |
| #32 | `feat(voice): privacy mode (GPIO button + voice command)` | `phase-6`, `feat`, `voice` |
| #33 | `feat(voice): WebRTC audio stream from browser → Whisper` | `phase-6`, `feat`, `voice` |
| #34 | `feat(voice): voice history storage in SQLite` | `phase-6`, `feat`, `voice` |

### Фаза 7 — LLM и Intent Router

| Issue | Заголовок | Labels |
|---|---|---|
| #35 | `feat(llm): Fast Matcher (keyword/regex rules YAML config)` | `phase-7`, `feat`, `llm` |
| #36 | `feat(llm): Ollama client + phi-3-mini/gemma-2b support` | `phase-7`, `feat`, `llm` |
| #37 | `feat(llm): dynamic system prompt with module registry` | `phase-7`, `feat`, `llm` |
| #38 | `feat(llm): Intent Router orchestration (Fast → LLM fallback)` | `phase-7`, `feat`, `llm` |
| #39 | `feat(llm): model manager (download/select/switch)` | `phase-7`, `feat`, `llm` |
| #40 | `feat(llm): auto-disable LLM when RAM < 5GB` | `phase-7`, `feat`, `llm` |

### Фаза 8 — Пользователи и безопасность

| Issue | Заголовок | Labels |
|---|---|---|
| #41 | `feat(users): user profiles CRUD (admin/resident/guest roles)` | `phase-8`, `feat`, `security` |
| #42 | `feat(users): PIN auth + rate limiting (5 attempts → 10 min lock)` | `phase-8`, `feat`, `security` |
| #43 | `feat(users): Face ID enrollment + browser webcam auth flow` | `phase-8`, `feat`, `security` |
| #44 | `feat(users): audit log (SQLite, 10k records rotation)` | `phase-8`, `feat`, `security` |
| #45 | `feat(security): self-signed HTTPS certificate generation` | `phase-8`, `feat`, `security` |
| #46 | `feat(security): iptables rules setup script` | `phase-8`, `feat`, `security` |
| #47 | `feat(security): Tailscale integration (remote-access module)` | `phase-8`, `feat`, `security` |

### Фаза 9 — Secrets Vault и OAuth

| Issue | Заголовок | Labels |
|---|---|---|
| #48 | `feat(vault): AES-256-GCM secrets storage in /secure/tokens/` | `phase-9`, `feat`, `security` |
| #49 | `feat(vault): OAuth Device Authorization Grant flow (RFC 8628)` | `phase-9`, `feat`, `backend` |
| #50 | `feat(vault): API proxy endpoint (no token exposure to modules)` | `phase-9`, `feat`, `security` |
| #51 | `feat(vault): token auto-refresh (5 min before expiry)` | `phase-9`, `feat`, `backend` |

### Фаза 10 — Сканер сети и импорт

| Issue | Заголовок | Labels |
|---|---|---|
| #52 | `feat(scanner): ARP sweep (passive + on-demand)` | `phase-10`, `feat`, `backend` |
| #53 | `feat(scanner): mDNS/Bonjour listener` | `phase-10`, `feat`, `backend` |
| #54 | `feat(scanner): SSDP/UPnP listener` | `phase-10`, `feat`, `backend` |
| #55 | `feat(scanner): OUI database lookup (manufacturer detection)` | `phase-10`, `feat`, `backend` |
| #56 | `feat(import): Home Assistant import adapter + ha-bridge module` | `phase-10`, `feat`, `backend` |
| #57 | `feat(import): Tuya import adapter + tuya-bridge module` | `phase-10`, `feat`, `backend` |
| #58 | `feat(import): Philips Hue local API adapter` | `phase-10`, `feat`, `backend` |

### Фаза 11 — Мониторинг, уведомления, бэкап

| Issue | Заголовок | Labels |
|---|---|---|
| #59 | `feat(monitor): CPU temp + RAM + disk monitoring + alerts` | `phase-11`, `feat`, `infra` |
| #60 | `feat(monitor): RAM degradation strategy (auto-stop by priority)` | `phase-11`, `feat`, `infra` |
| #61 | `feat(notify): Web Push VAPID implementation` | `phase-11`, `feat`, `backend` |
| #62 | `feat(backup): local USB/SD backup + restore` | `phase-11`, `feat`, `backend` |
| #63 | `feat(backup): E2E cloud backup (PBKDF2 + AES-256-GCM)` | `phase-11`, `feat`, `security` |
| #64 | `feat(backup): QR secrets transfer between devices` | `phase-11`, `feat`, `security` |

### Фаза 12 — SDK и тесты

| Issue | Заголовок | Labels |
|---|---|---|
| #65 | `feat(sdk): SmartHomeModule base class + decorators` | `phase-12`, `feat`, `sdk` |
| #66 | `feat(sdk): smarthome CLI (new-module / dev / test / publish)` | `phase-12`, `feat`, `sdk` |
| #67 | `feat(sdk): mock Core API for local development` | `phase-12`, `feat`, `sdk` |
| #68 | `test: registry, eventbus, module_loader, integrity` | `phase-12`, `test`, `backend` |
| #69 | `test: Core API endpoints + auth + rate limiting` | `phase-12`, `test`, `backend` |
| #70 | `test: wizard flow + onboarding` | `phase-12`, `test`, `frontend` |
| #71 | `docs: README, CONTRIBUTING, module development guide` | `phase-12`, `docs` |

---

## 11. GIT WORKFLOW

### Ветки

- Работа в **`main`** для задач до 200 строк
- Задача > 200 строк: ветка `feat/<issue-number>-<slug>`

```bash
git checkout -b feat/5-device-registry   # для Issue #5
git checkout main                        # вернуться
git merge feat/5-device-registry         # влить
git push origin main
```

### Commit messages (Conventional Commits)

Формат: `<type>(<scope>): <описание> [#<N>]`

| Type | Когда |
|---|---|
| `feat` | новый функционал |
| `fix` | исправление бага |
| `chore` | настройка, зависимости, конфиги |
| `refactor` | рефакторинг без изменения поведения |
| `test` | тесты |
| `docs` | документация |
| `security` | исправление уязвимости |
| `perf` | оптимизация производительности |

```bash
# ✅ Правильно
git commit -m "feat(registry): add Device Registry CRUD with state history [#5]"
git commit -m "fix(agent): handle missing manifest file on first init [#12]"
git commit -m "test(registry): add pytest for state_changed event emission [#68]"
git commit -m "chore: add docker-compose.yml with core+modules+sandbox [#1]"

# ❌ Неправильно
git commit -m "fix"
git commit -m "update code"
git commit -m "wip"
git commit -m "."
```

### Когда коммитить

Атомарные шаги — каждый коммит должен оставлять проект в рабочем состоянии:

```
Создана модель данных          → коммит
Написан сервис                 → коммит
Добавлен роутер                → коммит
Написан тест                   → коммит
Тест прошёл                    → коммит + пуш
```

### Push в main

```bash
# Перед каждым push — проверить:
pytest tests/ -x -q                    # все тесты зелёные
python -m mypy core/ --ignore-missing  # типизация

git push origin main
```

### Деплой в контейнер (ОБЯЗАТЕЛЬНО после каждого push)

> Исходный код Python (core/, system_modules/, agent/, tests/) монтируется в контейнер через volume mounts.
> После изменения Python-файлов достаточно перезапустить контейнер.
> `docker cp` НЕ нужен для этих директорий.

```bash
# 1. Пересобрать фронтенд
npx vite build

# 2. Скопировать собранные статические файлы в контейнер
docker cp system_modules/ui_core/static/. selena-core:/opt/selena-core/system_modules/ui_core/static/

# 3. Перезапустить контейнер (Python-код подхватится через volume mounts)
docker restart selena-core

# 4. Проверить что всё работает
sleep 3
curl -s http://localhost:7070/api/v1/health | python3 -m json.tool
curl -s -o /dev/null -w "UI :8080 → HTTP %{http_code}\n" http://localhost:8080/

# 5. Обновить экран устройства (kiosk Chromium)
sudo XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0 wtype -k F5
```

> **Почему шаг 5 обязателен:** Экран устройства — это Chromium в kiosk-режиме
> внутри Wayland-композитора `cage` (systemd: `smarthome-display.service`).
> `docker restart` перезапускает бэкенд и UI-сервер, но НЕ перезагружает браузер.
> Chromium с флагами `--disable-background-networking` кеширует старую страницу.
> Команда `wtype -k F5` отправляет нажатие F5 через Wayland-протокол в Chromium.
>
> Если `wtype` недоступен или не работает — альтернатива:
> `sudo systemctl restart smarthome-display.service`
> (перезапускает cage + chromium полностью, медленнее но надёжнее)

**Что обновляется:**

| Шаг | Что обновляется | Где видно |
|-----|-----------------|-----------|
| `npx vite build` | Фронтенд (React SPA) | — |
| `docker cp static/` | UI в контейнере | Браузер `:8080` |
| `docker restart` | Перезагрузка FastAPI + UI | Сервер |
| `wtype -k F5` | Обновление страницы в kiosk | Экран устройства |

**Правила:**

- Если изменения только в `src/` (фронтенд) — шаги 1, 2, 3, 4, 5
- Если изменения только в `core/` (бэкенд) — шаги 3, 4, 5 (volume mount — автоматически)
- Если изменения в обоих — все 5 шагов
- ⛔ Нельзя считать задачу завершённой без проверки `curl` на шаге 4
- ⛔ Нельзя считать задачу завершённой без обновления экрана устройства (шаг 5)

---

## 12. РАБОТА С GITHUB ISSUES

### Алгоритм работы с задачей

```
1. Проверить что Issue существует на GitHub
   → Если нет — СОЗДАТЬ по плану из раздела 10
   → gh issue create --title "feat(registry): ..." --label "phase-2,feat,backend"

2. Поставить label "in-progress"
   → gh issue edit <N> --add-label "in-progress"

3. Прочитать Issue полностью + связанные разделы TZ.md

4. Запланировать шаги выполнения (написать список в Issue комментарии)

5. Выполнять шаги, коммитя каждый атомарный шаг с [#N]

6. Написать итоговый комментарий:
   → "✅ Done. Commits: abc1234, def5678, ghi9012"

7. Закрыть Issue:
   → gh issue close <N>

8. Снять label "in-progress"
```

### Создание Issue через gh CLI

```bash
gh issue create \
  --repo dotradepro/SelenaCore \
  --title "feat(registry): Device Registry CRUD + state history" \
  --body "## Задача
Реализовать Device Registry с полным CRUD и хранением истории состояний.

## Читать перед началом
- docs/TZ.md раздел 2 (Device Registry)
- AGENTS.md раздел 4.2 (API спецификация)

## Критерии готовности
- [ ] POST /api/v1/devices — создание устройства
- [ ] GET /api/v1/devices — список всех устройств
- [ ] GET /api/v1/devices/{id} — конкретное устройство
- [ ] PATCH /api/v1/devices/{id}/state — обновление state
- [ ] DELETE /api/v1/devices/{id} — удаление
- [ ] История: последние 1000 состояний в SQLite
- [ ] Публикация device.state_changed в Event Bus
- [ ] pytest test_registry.py → 0 failed" \
  --label "phase-2,feat,backend"
```

### Labels для проекта

```
phase-1 … phase-12    фаза реализации
feat / fix / chore / refactor / test / docs / security / perf
backend / frontend / infra / voice / llm / sdk
in-progress / blocked / needs-review
```

---

## 13. КРИТИЧЕСКИЕ ЗАПРЕТЫ

```
⛔ Начинать код без создания Issue на GitHub
⛔ Брать вторую задачу пока первая не закрыта
⛔ Пушить в main с падающими тестами
⛔ Пустой except: pass — всегда логировать ошибку
⛔ print() — только logging.getLogger(__name__)
⛔ Хранить секреты в .env в открытом виде (только .env.example)
⛔ Читать /secure из модуля напрямую (только через secrets-vault API)
⛔ Публиковать события core.* из модуля (403 на уровне API)
⛔ Возвращать OAuth-токен модулю напрямую (только через proxy)
⛔ Биометрию в любые исходящие HTTP запросы
⛔ shell=True в subprocess без крайней необходимости
⛔ eval() / exec() в любом коде
⛔ Модифицировать файлы ядра без обновления core.manifest
⛔ Коммит с сообщением "fix", "update", "wip", "."
⛔ Создавать virtualenv / venv внутри Docker-контейнера (зависимости ставятся глобально через pip)
⛔ Использовать docker cp для обновления core/ или system_modules/ (используются volume mounts)
```

---

## 14. ТЕСТИРОВАНИЕ

### Структура тестов

```python
# tests/test_registry.py

import pytest
from httpx import AsyncClient
from core.main import app

@pytest.fixture
async def client():
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c

@pytest.fixture
def module_token(client):
    # Получить токен тестового модуля
    return "test-module-token-xyz"

async def test_create_device(client, module_token):
    resp = await client.post("/api/v1/devices",
        headers={"Authorization": f"Bearer {module_token}"},
        json={
            "name": "Test Sensor",
            "type": "sensor",
            "protocol": "mqtt",
            "capabilities": ["read_temperature"],
        }
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Sensor"
    assert data["device_id"] is not None

async def test_state_changed_event(client, module_token, event_bus):
    # Создать устройство
    resp = await client.post("/api/v1/devices", ...)
    device_id = resp.json()["device_id"]

    # Обновить состояние
    await client.patch(f"/api/v1/devices/{device_id}/state",
        headers={"Authorization": f"Bearer {module_token}"},
        json={"state": {"temperature": 22.5}}
    )

    # Проверить что событие опубликовано
    event = await event_bus.get_last_event("device.state_changed")
    assert event["payload"]["device_id"] == device_id
    assert event["payload"]["new_state"]["temperature"] == 22.5

async def test_core_event_forbidden(client, module_token):
    resp = await client.post("/api/v1/events/publish",
        headers={"Authorization": f"Bearer {module_token}"},
        json={
            "type": "core.integrity_violation",  # запрещено
            "source": "evil-module",
            "payload": {}
        }
    )
    assert resp.status_code == 403
```

### Запуск тестов

```bash
# Все тесты
pytest tests/ -v

# Конкретный файл
pytest tests/test_registry.py -v

# С покрытием
pytest tests/ --cov=core --cov-report=term-missing

# Остановиться на первой ошибке
pytest tests/ -x
```

---

## 15. ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (.env.example)

```bash
# Платформа SmartHome LK
PLATFORM_API_URL=https://smarthome-lk.com/api/v1
PLATFORM_DEVICE_HASH=                    # заполняется при регистрации
# API ключ хранится в /secure/platform.key — не в .env!

# Core API
CORE_PORT=7070
CORE_DATA_DIR=/var/lib/selena
CORE_SECURE_DIR=/secure
CORE_LOG_LEVEL=INFO                       # DEBUG | INFO | WARNING | ERROR

# UI
UI_PORT=8080
UI_HTTPS=true

# Integrity Agent
AGENT_CHECK_INTERVAL=30                   # секунд
AGENT_MAX_RESTORE_ATTEMPTS=3

# Docker
DOCKER_SOCKET=/var/run/docker.sock
MODULE_CONTAINER_IMAGE=smarthome-modules:latest
SANDBOX_IMAGE=smarthome-sandbox:latest

# Аудио (переопределение автодетекта)
AUDIO_FORCE_INPUT=                        # или "hw:2,0"
AUDIO_FORCE_OUTPUT=                       # или "bluez_sink.AA_BB_CC"

# OAuth провайдеры
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
TUYA_CLIENT_ID=
TUYA_CLIENT_SECRET=

# Tailscale
TAILSCALE_AUTH_KEY=                       # tskey-auth-...

# Режим разработки
DEBUG=false
MOCK_PLATFORM=false                       # для локальной разработки без платформы
```

---

*SelenaCore · AGENTS.md · SmartHome LK · Open Source MIT*
*Репозиторий: https://github.com/dotradepro/SelenaCore*
