# Архітектура SelenaCore

🇬🇧 [English version](../architecture.md)

## Огляд

SelenaCore складається з двох незалежних процесів:

```
┌─────────────────────────────────────────────────────────────────┐
│                      smarthome-core (Docker)                     │
│                                                                   │
│  FastAPI :7070 (Core API)          FastAPI :8080 (UI Core)       │
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

### 1. Core API (`core/api/`)

REST-сервер на FastAPI, порт `7070`. Точка входу для всіх модулів.

**Middleware:**
- `X-Request-Id` генерується для кожного запиту, пробрасується через `contextvars`
- CORS — дозволено лише `localhost`
- Rate limiting — 100 req/sec на токен

**Авторизація (`core/api/auth.py`):**
- `Authorization: Bearer <module_token>` обов'язковий для всіх endpoints крім `/health`
- Токен видається при встановленні модуля, зберігається в SQLite
- Тип токена перевіряється: SYSTEM-модулі бачать більше endpoints

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

# Підписка
await bus.subscribe("device.*", webhook_url)  # wildcard

# Доставка (background task)
POST http://module:810X/webhook/events
X-Selena-Signature: sha256=<hmac>    # HMAC-SHA256 підпис
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
| STT | `stt.py` | Whisper.cpp (pywhispercpp) |
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
  :8080  UI Core         (браузер користувача)
  :8100  Модуль 1
  :8101  Модуль 2
  ...
  :8200  Модуль 100
```

Docker network: `selena-net` (driver: bridge, internal).
Модулі НЕ можуть спілкуватися між собою напряму.

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
