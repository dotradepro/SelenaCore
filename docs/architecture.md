# Архитектура SelenaCore

## Обзор

SelenaCore состоит из двух независимых процессов:

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
│              smarthome-agent (systemd, отдельный процесс)        │
│                                                                   │
│  SHA256 проверка файлов ядра каждые 30 сек                       │
│  При нарушении: стоп модулей → уведомление → откат → SAFE MODE   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Компоненты

### 1. Core API (`core/api/`)

REST-сервер на FastAPI, порт `7070`. Точка входа для всех модулей.

**Middleware:**
- `X-Request-Id` генерируется для каждого запроса, пробрасывается через `contextvars`
- CORS — разрешён только `localhost`
- Rate limiting — 100 req/sec на токен

**Авторизация (`core/api/auth.py`):**
- `Authorization: Bearer <module_token>` обязателен для всех endpoints кроме `/health`
- Токен выдаётся при установке модуля, хранится в SQLite
- Тип токена проверяется: SYSTEM-модули видят больше endpoints

---

### 2. Device Registry (`core/registry/`)

SQLite-хранилище устройств через SQLAlchemy 2.0 async.

**Таблицы:**
- `devices` — устройства (id, name, type, protocol, state JSON, capabilities, last_seen, module_id, meta)
- `state_history` — последние 1000 состояний на устройство (архив)
- `audit_log` — все действия пользователей (10 000 записей, ротация)

**Автоматическое событие:**
При `PATCH /devices/{id}/state` автоматически публикуется `device.state_changed` в Event Bus.

---

### 3. Event Bus (`core/eventbus/`)

```python
# Публикация
await bus.publish(event)          # кладёт в asyncio.Queue

# Подписка
await bus.subscribe("device.*", webhook_url)  # wildcard

# Доставка (background task)
POST http://module:810X/webhook/events
X-Selena-Signature: sha256=<hmac>    # HMAC-SHA256 подпись
```

**Защита:**
- `core.*` события нельзя публиковать из модуля → 403 Forbidden
- HMAC-SHA256 подпись на каждый webhook delivery

---

### 4. Module Loader (`core/module_loader/`)

#### Жизненный цикл модуля

```
UPLOADED → VALIDATING → READY → RUNNING → STOPPED → REMOVED
                                    ↓
                                  ERROR
```

#### Установка модуля

1. Upload ZIP → `/api/v1/modules/install`
2. **Validator** (`validator.py`) проверяет `manifest.json`:
   - Обязательные поля: `name`, `version`, `type`, `api_version`, `port`, `permissions`
   - `name` — RFC 1123 slug (`[a-z0-9-]+`)
   - `version` — semver (`^\d+\.\d+\.\d+$`)
   - `port` — 8100–8200
   - `permissions` — только разрешённые значения
3. **Sandbox** — тестирование в `smarthome-sandbox` контейнере (--rm)
4. **DockerSandbox** — запуск в `smarthome-modules` на выделенном порту

#### Защита SYSTEM-модулей

Модули с `type: SYSTEM` нельзя остановить через API → 403 Forbidden.

---

### 5. Integrity Agent (`agent/`)

Независимый процесс (systemd юнит `smarthome-agent.service`), **не импортирует** ядро.

```
Каждые 30 сек:
  1. Читает /secure/master.hash
  2. Считает SHA256 файла /secure/core.manifest
  3. Сравнивает → если расхождение: MANIFEST TAMPERED
  4. Для каждого файла ядра: SHA256 из manifest vs SHA256 на диске
  5. Если изменения найдены:
       а) Лог в /var/log/selena/integrity.log
       б) Стоп всех модулей (Docker stop)
       в) Уведомление платформы
       г) Откат из /secure/core_backup/ (3 попытки, 5 сек пауза)
       д) Если откат не удался → SAFE MODE
```

**SAFE MODE:**
- Core API только для чтения (`GET` методы)
- Установка и запуск новых модулей запрещены
- `GET /health` возвращает `"mode": "safe_mode"`

---

### 6. Cloud Sync (`core/cloud_sync/`)

Background task: heartbeat каждые 60 сек + long-poll команд.

```
Heartbeat:
  POST /api/v1/device/heartbeat
  Headers:
    X-Device-Hash: <hash>
    X-Signature: sha256=<hmac>    # HMAC-SHA256 тело + timestamp + ключ из /secure/platform.key
  Body: { status, uptime, modules, integrity }

Long-poll:
  GET /api/v1/device/commands?device_hash=...&wait=30
  → Обработка: INSTALL_MODULE | STOP_MODULE | REBOOT | SYNC_STATE | FACTORY_RESET
  → Ответ: POST /api/v1/device/commands/{id}/ack
```

**Retry:** экспоненциальный backoff 2^n сек, max 300 сек.

---

### 7. Voice Core (`system_modules/voice_core/`)

| Компонент | Файл | Технология |
|-----------|------|------------|
| Wake-word | `wake_word.py` | openWakeWord |
| STT | `stt.py` | Whisper.cpp (pywhispercpp) |
| TTS | `tts.py` | Piper TTS |
| Speaker ID | `speaker_id.py` | resemblyzer |
| Аудио I/O | `audio_manager.py` | ALSA + PipeWire |
| WebRTC | — | браузер → Whisper pipeline |

**Приоритеты аудио входов:** `usb > i2s_gpio > bluetooth > hdmi > builtin`

---

### 8. LLM Engine (`system_modules/llm_engine/`)

Двухуровневый routers:

```
Уровень 1: Fast Matcher
  Загружает YAML с keyword/regex правилами
  Матчинг за ~50 ms
  Без сети, без GPU

Уровень 2: Ollama (fallback)
  phi-3-mini (2.3 GB VRAM — Pi 5 8GB)
  gemma:2b (1.5 GB)
  Автоотключение при RAM < 5 GB
```

---

### 9. Secrets Vault (`system_modules/secrets_vault/`)

```
/secure/tokens/<module>/<key>.enc
```

Каждый секрет: `nonce(12 байт) + ciphertext` (AES-256-GCM).
Ключ шифрования: PBKDF2(HMAC-SHA256, passphrase, salt=module_name, iterations=480000).

**OAuth Device Flow (RFC 8628):**
1. `POST /api/v1/secrets/oauth/start` → device_code, QR-код
2. Polling → токен сохраняется зашифрованным
3. Модуль использует `POST /api/v1/secrets/proxy` — токен **никогда не покидает** ядро

**SSRF-защита proxy:**
- Только `https://`
- Блокировка приватных IP: `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
- Разрешённых redirect нет (follow_redirects=False)

---

## Сетевая изоляция

```
Внешняя сеть
    ↓ :443 (HTTPS — только платформа и OAuth)
    ↓ :80  (редирект)
WiFi интерфейс (wlan0 / wlan1)
    ↓
iptables FORWARD DROP
    ↓
localhost
  :7070  Core API        (только модули + UI)
  :8080  UI Core         (браузер пользователя)
  :8100  Модуль 1
  :8101  Модуль 2
  ...
  :8200  Модуль 100
```

Docker network: `selena-net` (driver: bridge, internal).
Модули НЕ могут общаться между собой напрямую.

---

## Хранилище данных

```
/var/lib/selena/selena.db     SQLite (Registry, AuditLog, Voice History)
/var/lib/selena/modules/      Распакованные модули
/var/lib/selena/backups/      Локальные архивы

/secure/platform.key          API ключ платформы (600 байт, AES-256-GCM)
/secure/tls/                  Self-signed HTTPS сертификаты
/secure/tokens/<module>/      Зашифрованные OAuth токены
/secure/core.manifest         SHA256 файлов ядра
/secure/master.hash           SHA256 самого манифеста
/secure/core_backup/v0.3.0/   Резервная копия файлов ядра
```
