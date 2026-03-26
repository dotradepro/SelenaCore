# Архітектура SelenaCore

🇬🇧 [English version](../architecture.md)

## Огляд

SelenaCore складається з двох незалежних процесів:

```
┌─────────────────────────────────────────────────────────────────┐
│                      smarthome-core (Docker)                     │
│                                                                   │
│  FastAPI :7070 (Core API)          FastAPI :80 (UI Core)       │
│       │                                   │                      │
│  ┌────┴────────────────────────────────────┴───────────────┐     │
│  │             EventBus (asyncio.Queue)                     │     │
│  └────┬────────────────────────────────────┬───────────────┘     │
│       │                                   │                      │
│  Device Registry              Module Loader (Plugin Manager)      │
│  (SQLite)                     + DockerSandbox                    │
│                                                                   │
│  CloudSync ← HMAC             Voice Core (STT/TTS/wake-word)     │
│  IntegrityAgent (30s)         LLM Engine (Ollama)                │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│              smarthome-agent (systemd, окремий процес)            │
│                                                                   │
│  SHA256 перевірка файлів ядра кожні 30 сек                       │
│  При порушенні: стоп модулів → повідомлення → відкат → SAFE MODE │
└─────────────────────────────────────────────────────────────────┘
```

---

## Компоненти

### Модель виконання модулів

SelenaCore використовує **дворівневу модель виконання** для економії RAM (~580 МБ на Raspberry Pi):

| Тип | Виконання | Порт | Зв'язок з ядром | Контейнер |
|-----|-----------|------|-----------------|------------|
| **SYSTEM** | In-process через `importlib` | Немає | Прямі Python-виклики + `SystemModule` ABC | smarthome-core (спільний) |
| **Користувацькі (UI/INTEGRATION/DRIVER/AUTOMATION)** | Docker sandbox | 8100–8200 | HTTP API + webhook | smarthome-modules |

**Системні модулі** наслідують `SystemModule` (`core/module_loader/system_module.py`) та завантажуються через `sandbox.py → _start_in_process()`. Їх `APIRouter` монтується в core FastAPI app за адресою `/api/ui/modules/{name}/`. Вони працюють з EventBus та Device Registry через прямі Python-виклики — без HTTP.

**Користувацькі модулі** працюють в ізольованих Docker-контейнерах з власними портами.

---

## Додаткова документація

| Тема | Документ |
|------|----------|
| Автентифікація користувачів та QR-флоу | [docs/uk/user-manager-auth.md](user-manager-auth.md) |
| Протокол модулів (токени, HMAC, вебхуки) | [docs/uk/module-core-protocol.md](module-core-protocol.md) |
| Розробка модулів (SDK, manifest) | [docs/uk/module-development.md](module-development.md) |
| Розробка віджетів (widget.html, i18n) | [docs/uk/widget-development.md](widget-development.md) |
| Деплой та systemd | [docs/uk/deployment.md](deployment.md) |

---

### 1. Core API (`core/api/`)

REST-сервер на FastAPI, порт `7070`. Точка входу для всіх модулів.

**Middleware:**
- `X-Request-Id` генерується для кожного запиту, пробрасується через `contextvars`
- CORS — дозволено лише `localhost`
- Rate limiting — 120 зап/хв (зовнішні), 600 зап/хв (LAN/localhost); SSE та статика — виняток

**Авторизація (`core/api/auth.py`):**
- `Authorization: Bearer <module_token>` обов'язковий для всіх endpoints крім `/health`
- Токен зберігається як plaintext файл `/secure/module_tokens/<name>.token`; в dev-режимі підтримується `DEV_MODULE_TOKEN`
- Гранулярна перевірка дозволів не реалізована в v1 — будь-який валідний токен отримує повний доступ до API

---

### 2. Device Registry (`core/registry/`)

SQLite-сховище пристроїв через SQLAlchemy 2.0 async.

**Таблиці:**
- `devices` — пристрої (id, name, type, protocol, state JSON, capabilities, last_seen, module_id, meta)
- `state_history` — останні 1000 станів на пристрій (архів)
- `audit_log` — всі дії користувачів (10 000 записів, ротація)

**Автоматична подія:**
При `PATCH /devices/{id}/state` автоматично публікується `device.state_changed` в Event Bus.

---

### 3. Event Bus (`core/eventbus/`)

```python
# Публікація
await bus.publish(event)          # кладе в asyncio.Queue

# Підписка (користувацькі модулі — webhook)
await bus.subscribe("device.*", webhook_url)  # wildcard

# Підписка (системні модулі — in-process)
bus.subscribe_direct(sub_id, module_id, ["device.*"], callback)

# Доставка (background task)
# Webhook: POST http://module:810X/webhook/events
# Direct:  asyncio.create_task(callback(event))
X-Selena-Signature: sha256=<hmac>    # HMAC-SHA256 підпис (лише webhook)
```

**Захист:**
- `core.*` події не можна публікувати з модуля → 403 Forbidden
- HMAC-SHA256 підпис на кожну webhook-доставку

---

### 4. Module Loader (`core/module_loader/`)

#### Життєвий цикл модуля

```
UPLOADED → VALIDATING → READY → RUNNING → STOPPED → REMOVED
                                    ↓
                                  ERROR
```

#### Встановлення модуля

1. Upload ZIP → `/api/v1/modules/install`
2. **Validator** (`validator.py`) перевіряє `manifest.json`:
   - Обов'язкові поля: `name`, `version`, `type`, `api_version`, `port`, `permissions`
   - `name` — RFC 1123 slug (`[a-z0-9-]+`)
   - `version` — semver (`^\d+\.\d+\.\d+$`)
   - `port` — 8100–8200
   - `permissions` — лише дозволені значення
3. **Sandbox** — тестування в `smarthome-sandbox` контейнері (--rm)
4. **DockerSandbox** — запуск в `smarthome-modules` на виділеному порту

#### Захист SYSTEM-модулів

Модулі з `type: SYSTEM` не можна зупинити через API → 403 Forbidden.

---

### 5. Integrity Agent (`agent/`)

Незалежний процес (systemd юніт `smarthome-agent.service`), **не імпортує** ядро.

```
Кожні 30 сек:
  1. Читає /secure/master.hash
  2. Обчислює SHA256 файлу /secure/core.manifest
  3. Порівнює → якщо розбіжність: MANIFEST TAMPERED
  4. Для кожного файлу ядра: SHA256 з manifest vs SHA256 на диску
  5. Якщо знайдено зміни:
       а) Лог до /var/log/selena/integrity.log
       б) Стоп усіх модулів (Docker stop)
       в) Повідомлення платформі
       г) Відкат з /secure/core_backup/ (3 спроби, 5 сек пауза)
       ґ) Якщо відкат не вдався → SAFE MODE
```

**SAFE MODE:**
- Core API лише для читання (лише `GET` методи)
- Встановлення та запуск нових модулів заборонені
- `GET /health` повертає `"mode": "safe_mode"`

---

### 6. Cloud Sync (`core/cloud_sync/`)

Background task: heartbeat кожні 60 сек + long-poll команд.

```
Heartbeat:
  POST /api/v1/device/heartbeat
  Headers:
    X-Device-Hash: <hash>
    X-Signature: sha256=<hmac>    # HMAC-SHA256 тіло + timestamp + ключ з /secure/platform.key
  Body: { status, uptime, modules, integrity }

Long-poll:
  GET /api/v1/device/commands?device_hash=...&wait=30
  → Обробка: INSTALL_MODULE | STOP_MODULE | REBOOT | SYNC_STATE | FACTORY_RESET
  → Відповідь: POST /api/v1/device/commands/{id}/ack
```

**Retry:** експоненціальний backoff 2^n сек, max 300 сек.

---

### 7. Voice Core (`system_modules/voice_core/`)

| Компонент | Файл | Технологія |
|-----------|------|------------|
| Wake-word | `wake_word.py` | openWakeWord |
| STT | `stt.py` | Vosk |
| TTS | `tts.py` | Piper TTS |
| Speaker ID | `speaker_id.py` | resemblyzer |
| Аудіо I/O | `audio_manager.py` | ALSA + PipeWire |
| WebRTC | — | браузер → Whisper pipeline |

**Пріоритети аудіо входів:** `usb > i2s_gpio > bluetooth > hdmi > builtin`

---

### 8. LLM Engine (`system_modules/llm_engine/`)

Дворівневий маршрутизатор:

```
Рівень 1: Fast Matcher
  Завантажує YAML з keyword/regex правилами
  Матчинг за ~50 мс
  Без мережі, без GPU

Рівень 2: Ollama (fallback)
  phi-3-mini (2.3 GB VRAM — Pi 5 8GB)
  gemma:2b (1.5 GB)
  Автовимкнення при RAM < 5 GB
```

---

### 9. Secrets Vault (`system_modules/secrets_vault/`)

```
/secure/tokens/<module>/<key>.enc
```

Кожен секрет: `nonce(12 байт) + ciphertext` (AES-256-GCM).
Ключ шифрування: PBKDF2(HMAC-SHA256, passphrase, salt=module_name, iterations=480000).

**OAuth Device Flow (RFC 8628):**
1. `POST /api/v1/secrets/oauth/start` → device_code, QR-код
2. Polling → токен зберігається зашифрованим
3. Модуль використовує `POST /api/v1/secrets/proxy` — токен **ніколи не покидає** ядро

**SSRF-захист proxy:**
- Лише `https://`
- Блокування приватних IP: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- Дозволених redirect немає (follow_redirects=False)

---

## Мережева ізоляція

```
Зовнішня мережа
    ↓ :443 (HTTPS — лише платформа та OAuth)
    ↓ :80  (редирект)
WiFi інтерфейс (wlan0 / wlan1)
    ↓
iptables FORWARD DROP
    ↓
localhost
  :7070  Core API        (лише модулі + UI)
  :80  UI Core         (браузер користувача)
  :8100  Користувацький модуль 1 (тільки Docker sandbox — НЕ системні)
  :8101  Користувацький модуль 2
  ...
  :8200  Користувацький модуль 100
```

Docker network: `selena-net` (driver: bridge, internal).
Користувацькі модулі НЕ можуть спілкуватися між собою напряму.
Системні модулі працюють у процесі ядра і НЕ мають мережевих портів.

---

## Сховище даних

```
/var/lib/selena/selena.db     SQLite (Registry, AuditLog, Voice History)
/var/lib/selena/modules/      Розпаковані модулі
/var/lib/selena/backups/      Локальні архіви

/secure/platform.key          API ключ платформи (600 байт, AES-256-GCM)
/secure/tls/                  Self-signed HTTPS сертифікати
/secure/tokens/<module>/      Зашифровані OAuth токени
/secure/core.manifest         SHA256 файлів ядра
/secure/master.hash           SHA256 самого маніфесту
/secure/core_backup/v0.3.0/   Резервна копія файлів ядра
```
