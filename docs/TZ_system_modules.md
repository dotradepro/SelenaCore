# ТЗ: system_modules/ — Системные модули SelenaCore
**Исполнитель:** AI-агент кодирования  
**Приоритет:** 🔴 Высокий  
**Ветка:** `feat/<N>-system-modules`  
**Зависит от:** core/ полностью реализован и работает (Core API :7070, Event Bus, Device Registry, Module Loader)

---

## Обязательно прочитать перед началом

```
AGENTS.md (SelenaCore)              ← правила агента, git workflow
docs/architecture.md                ← архитектура ядра
docs/module-core-protocol.md        ← протокол модуль↔ядро, токены, HMAC
docs/module-development.md          ← SDK, manifest.json, permissions
README.md                           ← структура проекта, env vars
```

---

## Критические правила (нарушение = сломанный код)

```
❌ print() — только logging.getLogger(__name__)
❌ bare except: pass — всегда except Exception as e:
❌ отсутствие type hints на публичных методах
❌ синхронные def вместо async def в публичных методах
❌ eval(), exec() в любом коде
❌ shell=True без крайней необходимости
❌ прямое чтение /secure/ из любого системного модуля
❌ публикация core.* событий из модулей (только из ядра)
❌ хранение секретов в .env (только в .env.example шаблон)
❌ один файл = несколько ответственностей
```

---

## Общие требования ко всем системным модулям

### Структура каждого модуля

```
system_modules/<name>/
  manifest.json          ← обязательно
  main.py                ← точка входа, FastAPI + SDK
  <name>.py              ← бизнес-логика (отдельный файл)
  Dockerfile             ← как собрать образ
  requirements.txt       ← зависимости
  widget.html            ← UI виджет (если ui_profile != HEADLESS)
  settings.html          ← страница настроек (если есть)
  icon.svg               ← иконка для UI
  tests/
    test_<name>.py       ← pytest тесты
  README.md              ← описание модуля
```

### Шаблон main.py

```python
# system_modules/<name>/main.py
import logging
import os
from fastapi import FastAPI
from contextlib import asynccontextmanager
from sdk.base_module import SmartHomeModule
from .<name> import <NameModule>

logger = logging.getLogger(__name__)

module = <NameModule>()
app = FastAPI(title=module.name)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await module.start(app)
    yield
    await module.stop()

app = FastAPI(title=module.name, lifespan=lifespan)
module.register_static_routes(app)
```

### manifest.json — обязательные поля

```json
{
  "name": "<slug>",
  "version": "0.1.0",
  "description": "...",
  "type": "SYSTEM",
  "ui_profile": "FULL | HEADLESS | SETTINGS_ONLY | ICON_SETTINGS",
  "api_version": "1.0",
  "runtime_mode": "always_on",
  "port": <8100-8200>,
  "permissions": [...],
  "ui": {
    "icon": "icon.svg",
    "widget": { "file": "widget.html", "size": "NxM" },
    "settings": "settings.html"
  },
  "resources": { "memory_mb": <N>, "cpu": <0.N> }
}
```

### Порты — распределение

| Модуль | Порт |
|---|---|
| voice_core | 8100 |
| llm_engine | 8101 |
| ui_core | 80 (отдельно) |
| secrets_vault | 8102 |
| user_manager | 8103 |
| hw_monitor | 8104 |
| backup_manager | 8105 |
| remote_access | 8106 |
| network_scanner | 8107 |
| **automation_engine** | **8108** |
| **protocol_bridge** | **8109** |
| **device_watchdog** | **8110** |
| **scheduler** | **8111** |
| **presence_detection** | **8112** |
| **update_manager** | **8113** |
| **energy_monitor** | **8114** |
| **weather_service** | **8115** |
| **notification_router** | **8116** |
| **import_adapters** | **8117** |
| notify_push | 8118 |
| **media_player** | **8119** |

---

## Порядок реализации

Коммитить после каждого шага. Каждый коммит = рабочий модуль.

```
Шаг 1:  scheduler           ← все остальные зависят от него
Шаг 2:  device_watchdog     ← нужен для automation_engine
Шаг 3:  protocol_bridge     ← нужен для реальных устройств
Шаг 4:  automation_engine   ← ключевой модуль
Шаг 5:  presence_detection  ← используется в automation_engine
Шаг 6:  weather_service     ← используется в automation_engine
Шаг 7:  energy_monitor
Шаг 8:  notification_router ← используется в automation_engine
Шаг 9:  update_manager
Шаг 10: import_adapters     ← рефакторинг существующего
Шаг 9.5: media_player      ← зависит от scheduler (sleep-таймер), voice_core (TTS)
Шаг 11: pytest для всех модулей
```

---

## Модуль 1: `scheduler`

**Порт:** 8111  
**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 64 MB  

### Назначение

Центральный планировщик для всего SelenaCore. Все модули которым нужно "запустить в X время" — обращаются к нему через Core API events. Поддерживает cron, interval, и астрономические триггеры (sunrise/sunset).

### Функциональность

**Типы триггеров:**

```python
# Cron-выражение (стандартный синтаксис)
"cron:0 7 * * 1-5"        # будни в 07:00

# Интервал
"every:5m"                 # каждые 5 минут
"every:1h"                 # каждый час
"every:30s"                # каждые 30 секунд

# Астрономические события (требуют координат из настроек)
"sunrise"                  # на восходе солнца
"sunset"                   # на закате
"sunrise+30m"              # 30 минут после восхода
"sunset-1h"                # за час до заката
```

**Вычисление sunrise/sunset:**

```python
# Библиотека: astral (pip install astral)
from astral import LocationInfo
from astral.sun import sun
from datetime import date

city = LocationInfo(
    latitude=float(config["latitude"]),
    longitude=float(config["longitude"]),
    timezone=config["timezone"]
)
s = sun(city.observer, date=date.today(), tzinfo=city.timezone)
sunrise = s["sunrise"]
sunset  = s["sunset"]
```

**API для регистрации задачи (другие модули вызывают через Core API events):**

Scheduler слушает событие `scheduler.register`:
```json
{
  "job_id":     "automation:morning-lights",
  "trigger":    "sunrise+30m",
  "event_type": "automation.trigger",
  "payload":    { "automation_id": "morning-lights" },
  "owner":      "automation-engine"
}
```

При срабатывании триггера — публикует событие из `payload.event_type` с `payload.payload`.

Scheduler слушает событие `scheduler.unregister`:
```json
{ "job_id": "automation:morning-lights" }
```

**Персистентность задач:**

```python
# Задачи хранятся в SQLite через Core API /modules/{name}/config
# При рестарте — все задачи загружаются заново из конфига
# Astral пересчитывает sunrise/sunset каждый день автоматически
```

**Events публикуемые:**

```
scheduler.fired          { job_id, trigger, fired_at }
scheduler.job_registered { job_id, trigger, next_run }
scheduler.job_removed    { job_id }
```

**Events которые слушает:**

```
scheduler.register
scheduler.unregister
scheduler.list_jobs      → публикует scheduler.jobs_list в ответ
```

**widget.html (SETTINGS_ONLY — только настройки):**

```
Настройки:
  Широта  (float, -90..90)
  Долгота (float, -180..180)
  Часовой пояс (select из pytz)
  
Список активных задач:
  job_id | trigger | owner | следующий запуск
```

**Зависимости:**

```
astral>=3.2
apscheduler>=3.10
```

**Тесты (tests/test_scheduler.py):**

```python
# test: cron trigger fires at correct time (mock time)
# test: interval trigger fires N times in period (mock)
# test: sunrise/sunset computed correctly for known location
# test: job persists across restart (save → reload)
# test: scheduler.register event → job created
# test: scheduler.unregister event → job removed
```

---

## Модуль 2: `device_watchdog`

**Порт:** 8110  
**Тип:** SYSTEM  
**ui_profile:** ICON_SETTINGS  
**Память:** 64 MB  

### Назначение

Следит за доступностью всех устройств в Device Registry. Периодически проверяет их доступность (ping по IP, protocol-specific heartbeat), обновляет статус online/offline, публикует события при изменении состояния.

### Функциональность

**Алгоритм проверки:**

```python
# Каждые 60 секунд (настраивается):
async def check_all_devices():
    devices = await self.list_devices()   # GET /devices через SDK
    for device in devices:
        was_online = device["meta"].get("watchdog_online", True)
        is_online  = await self._ping(device)

        if was_online != is_online:
            # Статус изменился — обновить и оповестить
            await self.update_device_state(
                device["id"],
                {"watchdog_online": is_online,
                 "watchdog_last_seen": datetime.utcnow().isoformat()}
            )
            event = "device.online" if is_online else "device.offline"
            await self.publish_event(event, {
                "device_id":   device["id"],
                "device_name": device["name"],
                "protocol":    device["protocol"],
                "ip":          device["meta"].get("ip_address")
            })
```

**Методы проверки по протоколу:**

```python
async def _ping(self, device: dict) -> bool:
    protocol = device.get("protocol", "unknown")
    meta     = device.get("meta", {})

    match protocol:
        case "wifi" | "http":
            ip = meta.get("ip_address")
            if not ip:
                return False
            return await self._icmp_ping(ip, timeout=2.0)

        case "mqtt":
            # Проверить last_seen из MQTT broker (через protocol_bridge)
            # Offline если last_seen > threshold
            last_seen = meta.get("mqtt_last_seen")
            if not last_seen:
                return False
            delta = (datetime.utcnow() - datetime.fromisoformat(last_seen)).seconds
            return delta < int(self._config.get("mqtt_timeout_sec", 120))

        case "zigbee" | "zwave":
            # Через protocol_bridge событие device.protocol_heartbeat
            last_seen = meta.get("protocol_last_seen")
            if not last_seen:
                return True  # не знаем — считаем онлайн
            delta = (datetime.utcnow() - datetime.fromisoformat(last_seen)).seconds
            return delta < int(self._config.get("protocol_timeout_sec", 300))

        case _:
            return True   # неизвестный протокол — не проверяем
```

**ICMP ping без root:**

```python
# Использовать icmplib (работает без root через unprivileged ICMP)
from icmplib import async_ping

async def _icmp_ping(self, host: str, timeout: float) -> bool:
    try:
        result = await async_ping(host, count=1, timeout=timeout, privileged=False)
        return result.is_alive
    except Exception:
        return False
```

**Настройки:**

```
check_interval_sec:      60       # как часто проверять все устройства
ping_timeout_sec:        2        # таймаут одного ping
mqtt_timeout_sec:        120      # через сколько секунд MQTT считать offline
protocol_timeout_sec:    300      # Zigbee/Z-Wave timeout
offline_threshold:       3        # сколько провалов подряд перед offline
notify_on_offline:       true     # публиковать device.offline событие
```

**Events публикуемые:**

```
device.online        { device_id, device_name, protocol }
device.offline       { device_id, device_name, protocol, offline_since }
device.watchdog_scan { checked: N, online: N, offline: N, duration_ms: N }
```

**Events которые слушает:**

```
device.protocol_heartbeat   { device_id, timestamp }  ← от protocol_bridge
```

**widget.html (ICON_SETTINGS):**

```
Иконка: пульс-индикатор (зелёный если все онлайн, красный если есть offline)
Badge: "12/14 online"

Страница настроек:
  Настройки таймаутов
  Список устройств с последним временем проверки
  Кнопка "Проверить сейчас"
```

**Зависимости:**

```
icmplib>=3.0
```

**Тесты:**

```python
# test: device goes offline after N failed pings
# test: device.offline event published on status change
# test: device.online event published on recovery
# test: mqtt_last_seen timeout detection
# test: watchdog_scan event contains correct counts
```

---

## Модуль 3: `protocol_bridge`

**Порт:** 8109  
**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 256 MB  

### Назначение

Шлюз между физическими протоколами умного дома (MQTT, Zigbee, Z-Wave) и Device Registry ядра. Устройства на этих протоколах регистрируются в Registry и управляются через стандартный Core API. Другие модули не знают о протоколах — они работают только с абстрактными устройствами.

### 3.1 MQTT

**Встроенный MQTT broker (Mosquitto через Docker):**

```python
# При старте модуля — запускать Mosquitto в том же контейнере
# или подключаться к внешнему брокеру (настраивается)

MQTT_BROKER_HOST = config.get("mqtt_host", "localhost")
MQTT_BROKER_PORT = int(config.get("mqtt_port", 1883))
```

**Auto-discovery через MQTT (стандарт Home Assistant):**

```python
# Слушать: homeassistant/+/+/config
# При получении config сообщения — регистрировать устройство в Registry

async def on_mqtt_discovery(topic: str, payload: bytes):
    # topic: homeassistant/<component>/<object_id>/config
    config = json.loads(payload)
    device_id = await self.register_device(
        name=config.get("name", config["unique_id"]),
        type="sensor" | "switch" | "light" | ...,
        protocol="mqtt",
        capabilities=_extract_capabilities(config),
        meta={
            "mqtt_state_topic":   config.get("state_topic"),
            "mqtt_command_topic": config.get("command_topic"),
            "mqtt_unique_id":     config["unique_id"],
        }
    )
```

**Управление устройством через MQTT:**

```python
# Когда приходит PATCH /devices/{id}/state от любого модуля
# Core API публикует device.state_changed
# protocol_bridge перехватывает и отправляет MQTT команду

@on_event("device.state_changed")
async def on_state_changed(self, payload: dict):
    device = await self.get_device(payload["device_id"])
    if device["protocol"] != "mqtt":
        return

    command_topic = device["meta"].get("mqtt_command_topic")
    if not command_topic:
        return

    new_state = payload["new_state"]
    await self._mqtt_publish(command_topic, json.dumps(new_state))
```

### 3.2 Zigbee

**Через zigbee2mqtt (отдельный процесс):**

```python
# zigbee2mqtt запускается как subprocess или отдельный Docker сервис
# protocol_bridge подключается к нему через MQTT

# zigbee2mqtt публикует:
#   zigbee2mqtt/<friendly_name>        → state сообщения
#   zigbee2mqtt/<friendly_name>/set   ← команды

# protocol_bridge:
# 1. Подписывается на zigbee2mqtt/bridge/devices → список устройств
# 2. Регистрирует каждое в Device Registry с protocol="zigbee"
# 3. Транслирует state изменения ↔ Core API
```

**Поддерживаемые адаптеры Zigbee:**

```
SONOFF Zigbee 3.0 USB Dongle Plus (рекомендован)
Conbee II
Tube's Zigbee Coordinator
Texas Instruments CC2652R/CC2652P
```

### 3.3 Z-Wave

**Через zwave-js-ui (опционально, если есть USB адаптер):**

```python
# Аналогично Zigbee — через промежуточный сервис
# Конфигурируется если Z_WAVE_ENABLED=true в настройках модуля
# По умолчанию отключён (не все пользователи имеют USB адаптер)
```

### 3.4 Прямой HTTP/REST (WiFi устройства)

```python
# Для устройств с REST API (Shelly, Sonoff DIY, etc.)
# Polling каждые N секунд

async def _poll_http_device(self, device: dict):
    url = device["meta"].get("poll_url")
    if not url:
        return
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            resp = await client.get(url)
            state = self._parse_response(resp.json(), device["meta"].get("state_template"))
            await self.update_device_state(device["id"], state)
        except Exception as e:
            logger.warning(f"HTTP poll failed for {device['id']}: {e}")
```

### 3.5 Events

**Публикуемые:**

```
device.protocol_heartbeat    { device_id, protocol, timestamp }
device.protocol_discovered   { name, protocol, meta }  ← новое устройство найдено
device.protocol_lost         { device_id, protocol }
protocol_bridge.mqtt_connected    { host, port }
protocol_bridge.mqtt_disconnected { reason }
protocol_bridge.zigbee_devices    { count }
```

**Слушает:**

```
device.state_changed    → отправить команду на физическое устройство
```

### Настройки (settings.html)

```
MQTT:
  Enabled: toggle
  Host: localhost
  Port: 1883
  Username/Password (опционально)
  
Zigbee:
  Enabled: toggle
  Adapter path: /dev/ttyUSB0
  Channel: 11-26
  
Z-Wave:
  Enabled: toggle
  Adapter path: /dev/ttyUSB1
  
HTTP polling:
  Poll interval: 30s
```

**widget.html (FULL, размер 2x1):**

```
Левая половина:
  MQTT: ● Connected / ○ Offline
  Zigbee: N устройств
  Z-Wave: N устройств / Disabled

Правая половина:
  Последние события протокола (5 строк)
```

**Зависимости:**

```
aiomqtt>=1.2
httpx>=0.27
# zigbee2mqtt и zwave-js-ui запускаются как Docker сервисы, не pip
```

**Зависимости системные (в Dockerfile):**

```dockerfile
# Для Zigbee USB адаптера — передать device в Docker:
# docker run --device /dev/ttyUSB0:/dev/ttyUSB0 ...
```

**Тесты:**

```python
# test: MQTT discovery message → device registered in Registry (mock)
# test: device.state_changed → MQTT command published
# test: device.protocol_heartbeat published on MQTT message
# test: HTTP poll → device state updated
# test: MQTT disconnect → reconnect after timeout
```

---

## Модуль 4: `automation_engine`

**Порт:** 8108  
**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 128 MB  

### Назначение

Движок автоматизаций. Пользователь описывает правила "если X → то Y". Движок подписывается на события ядра, проверяет условия и выполняет действия. Это ключевой модуль — без него умный дом требует ручного управления.

### 4.1 Формат автоматизации (YAML)

```yaml
# Пример файла автоматизации
id: morning-lights
name: "Утреннее освещение"
enabled: true

trigger:
  - type: time
    at: "sunrise+30m"           # через scheduler
  - type: event
    event_type: "presence.home" # кто-то пришёл домой

condition:
  - type: time_range
    from: "06:00"
    to:   "10:00"
  - type: state
    device_id: "dev_bedroom_light"
    attribute: "power"
    operator: "=="
    value: false

action:
  - type: device_state
    device_id: "dev_living_light"
    state: { power: true, brightness: 80 }

  - type: device_state
    device_id: "dev_kitchen_light"
    state: { power: true, brightness: 60 }
    delay_ms: 500               # с задержкой 500мс

  - type: event
    event_type: "notification.send"
    payload:
      message: "Доброе утро! Свет включён."
      channel: "tts"

  - type: scene
    scene_id: "morning"
```

### 4.2 Типы триггеров

```yaml
# Время (через scheduler)
trigger:
  type: time
  at: "07:00" | "sunrise" | "sunset+30m" | "every:5m" | "cron:0 8 * * 1-5"

# Событие Core API
trigger:
  type: event
  event_type: "device.state_changed"
  filter:                         # опциональные фильтры payload
    device_id: "dev_door_sensor"
    new_state.contact: false      # dot-notation для вложенных полей

# Изменение состояния устройства
trigger:
  type: device_state
  device_id: "dev_motion_sensor"
  attribute: "motion"
  to: true                        # сработать когда motion стало true
  from: false                     # опционально: откуда

# Присутствие (от presence_detection)
trigger:
  type: presence
  action: "home" | "away"        # кто-то пришёл / ушёл
  user_id: "user_alice"          # опционально: конкретный пользователь
```

### 4.3 Типы условий

```yaml
# Временной диапазон
condition:
  type: time_range
  from: "22:00"
  to:   "07:00"              # поддерживает переход через полночь

# Состояние устройства
condition:
  type: state
  device_id: "dev_abc"
  attribute: "temperature"
  operator: ">" | "<" | ">=" | "<=" | "==" | "!="
  value: 25.0

# Присутствие
condition:
  type: presence
  state: "home" | "away"    # кто-то дома / никого нет
  user_id: "user_alice"     # опционально

# Погода (от weather_service)
condition:
  type: weather
  attribute: "condition"
  operator: "=="
  value: "rain"

# Время суток
condition:
  type: sun
  state: "above_horizon" | "below_horizon"

# Логические операторы
condition:
  type: and | or | not
  conditions: [...]
```

### 4.4 Типы действий

```yaml
# Изменить состояние устройства
action:
  type: device_state
  device_id: "dev_abc"
  state: { power: true, brightness: 80 }
  delay_ms: 0               # задержка перед выполнением

# Опубликовать событие
action:
  type: event
  event_type: "any.event"
  payload: {}

# Активировать сцену
action:
  type: scene
  scene_id: "evening"

# Отправить уведомление
action:
  type: notify
  message: "Текст уведомления"
  channel: "tts" | "push" | "telegram" | "all"

# Пауза между действиями
action:
  type: delay
  ms: 1000

# Условное действие
action:
  type: if
  condition: { type: state, ... }
  then: [...]
  else: [...]
```

### 4.5 Сцены

```yaml
# scenes/<id>.yaml
id: evening
name: "Вечер"
actions:
  - type: device_state
    device_id: "dev_living_light"
    state: { power: true, brightness: 40, color_temp: 3000 }
  - type: device_state
    device_id: "dev_tv_backlight"
    state: { power: true, brightness: 30 }
```

### 4.6 Хранение

```python
# Автоматизации хранятся в:
# /var/lib/selena/modules/automation-engine/automations/<id>.yaml
# Сцены:
# /var/lib/selena/modules/automation-engine/scenes/<id>.yaml

# При старте — загрузить все файлы
# При изменении — сохранить файл + перезагрузить
# Watchdog на директорию (watchfiles) — hot reload без рестарта
```

### 4.7 Регистрация триггеров при старте

```python
async def on_start(self):
    automations = self._load_all_automations()
    for automation in automations:
        await self._register_triggers(automation)

async def _register_triggers(self, automation: Automation):
    for trigger in automation.triggers:
        if trigger.type == "time":
            # Зарегистрировать задачу в scheduler
            await self.publish_event("scheduler.register", {
                "job_id":     f"automation:{automation.id}:{trigger.at}",
                "trigger":    trigger.at,
                "event_type": "automation.time_trigger",
                "payload":    { "automation_id": automation.id },
                "owner":      "automation-engine"
            })
        else:
            # Event-based триггеры — подписаться через Core API
            # (SDK делает это через @on_event декоратор)
            pass
```

### 4.8 Events

**Публикуемые:**

```
automation.triggered     { automation_id, trigger_type, timestamp }
automation.executed      { automation_id, actions_count, duration_ms }
automation.failed        { automation_id, error }
automation.created       { automation_id }
automation.updated       { automation_id }
automation.deleted       { automation_id }
scene.activated          { scene_id, scene_name }
```

**Слушает:**

```
device.state_changed
device.online
device.offline
presence.home
presence.away
weather.updated
automation.time_trigger    ← от scheduler
```

### 4.9 API эндпоинты модуля

```
GET  /automations              → список всех автоматизаций
POST /automations              → создать (body: YAML text или JSON)
GET  /automations/{id}         → одна автоматизация
PUT  /automations/{id}         → обновить
DELETE /automations/{id}       → удалить
PATCH /automations/{id}/toggle → включить/выключить

GET  /scenes                   → список сцен
POST /scenes                   → создать сцену
PUT  /scenes/{id}              → обновить
DELETE /scenes/{id}            → удалить
POST /scenes/{id}/activate     → активировать немедленно

GET  /history?limit=50         → история срабатываний
GET  /health                   → {"status": "ok"}
```

### widget.html (FULL, размер 2x2)

```
Верхняя половина:
  Список активных автоматизаций (toggle включить/выключить)
  Счётчик: "7 автоматизаций · 12 срабатываний сегодня"

Нижняя половина:
  Последние 5 срабатываний с временем и статусом
  Кнопка "Открыть редактор"
```

**settings.html — редактор автоматизаций:**

```
Список автоматизаций с кнопками редактировать/удалить/toggle
Редактор: YAML textarea с подсветкой синтаксиса (CodeMirror)
Кнопка "Тест" — запустить автоматизацию вручную
Вкладка "Сцены"
Вкладка "История"
```

**Зависимости:**

```
watchfiles>=0.21
pyyaml>=6.0
jsonpath-ng>=1.6       # для dot-notation фильтров в триггерах
```

**Тесты:**

```python
# test: automation loads from YAML correctly
# test: time trigger registered in scheduler on start
# test: event trigger fires when matching event received
# test: condition time_range blocks execution outside range
# test: condition state checks device attribute correctly
# test: action device_state calls update_device_state (mock)
# test: action delay pauses execution
# test: scene activates all devices in correct order
# test: automation with condition=false does NOT execute actions
# test: failed action does not stop subsequent actions
# test: hot reload detects file change and reloads automation
```

---

## Модуль 5: `presence_detection`

**Порт:** 8112  
**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 64 MB  

### Назначение

Определяет кто из пользователей находится дома. Использует несколько методов параллельно: ARP ping MAC-адресов телефонов, Bluetooth beacon, GPS геозоны (через мобильное приложение). Публикует события при приходе/уходе.

### 5.1 Методы обнаружения

**WiFi / ARP (основной, работает без приложения):**

```python
# Для каждого tracked устройства (MAC-адрес телефона):
# ARP ping каждые 30 секунд
# Если MAC отвечает → пользователь дома

import subprocess

async def _arp_check(self, mac: str) -> bool:
    # arping требует root или cap NET_RAW
    # Альтернатива: парсить /proc/net/arp (не требует root)
    try:
        with open("/proc/net/arp") as f:
            arp_table = f.read()
        # Найти MAC в таблице (нормализовать формат)
        mac_normalized = mac.lower().replace("-", ":")
        return mac_normalized in arp_table.lower()
    except Exception as e:
        logger.warning(f"ARP check failed: {e}")
        return False
```

**Bluetooth (опционально, если BT адаптер доступен):**

```python
# Сканировать BLE advertisements
# Если device с known UUID/MAC видно → пользователь дома

import asyncio
from bleak import BleakScanner

async def _bluetooth_scan(self) -> set[str]:
    """Возвращает set MAC-адресов видимых BT устройств."""
    try:
        devices = await BleakScanner.discover(timeout=5.0)
        return {d.address.lower() for d in devices}
    except Exception as e:
        logger.warning(f"BT scan failed: {e}")
        return set()
```

**GPS геозоны (через webhook от мобильного приложения):**

```python
# Мобильное приложение отправляет POST когда входит/выходит из зоны:
@app.post("/webhook/location")
async def location_webhook(request: Request):
    body = await request.json()
    # body: { user_id, event: "enter"|"leave", zone: "home" }
    user_id = body["user_id"]
    is_home = body["event"] == "enter" and body["zone"] == "home"
    await self._update_presence(user_id, is_home, method="gps")
```

### 5.2 Логика определения присутствия

```python
# Пользователь считается "дома" если ХОТЯ БЫ ОДИН метод говорит да
# Grace period: 5 минут после последнего видения перед объявлением "away"
# Prevents flapping: не публиковать "away" если через 30с снова "home"

async def _update_presence(
    self,
    user_id: str,
    detected: bool,
    method: str
):
    user = self._users[user_id]
    user.last_seen[method] = datetime.utcnow() if detected else None

    was_home = user.is_home
    is_home  = any(
        ts and (datetime.utcnow() - ts).seconds < self._grace_period
        for ts in user.last_seen.values()
    )

    if was_home != is_home:
        user.is_home = is_home
        event = "presence.home" if is_home else "presence.away"
        await self.publish_event(event, {
            "user_id":   user_id,
            "user_name": user.name,
            "method":    method,
            "timestamp": datetime.utcnow().isoformat()
        })
        # Также обновить глобальный статус "кто-то дома"
        anyone_home = any(u.is_home for u in self._users.values())
        await self.publish_event("presence.anyone_home" if anyone_home
                                 else "presence.everyone_away", {})
```

### 5.3 Настройки пользователей

```python
# Конфигурация через settings.html → POST /modules/presence-detection/config

{
  "users": [
    {
      "user_id":   "user_alice",
      "name":      "Alice",
      "wifi_mac":  "AA:BB:CC:DD:EE:FF",    # MAC телефона
      "bt_mac":    "11:22:33:44:55:66",    # опционально
      "gps_token": "abc123"                # опционально
    }
  ],
  "grace_period_sec":    300,    # 5 минут
  "wifi_check_interval": 30,     # секунды
  "bt_scan_enabled":     false,
  "gps_enabled":         false
}
```

### 5.4 Events

**Публикуемые:**

```
presence.home              { user_id, user_name, method, timestamp }
presence.away              { user_id, user_name, method, timestamp }
presence.anyone_home       { users_home: [user_id, ...] }
presence.everyone_away     {}
presence.status            { users: [{user_id, name, is_home, last_seen}] }
```

**Слушает:**

```
presence.request_status    → публикует presence.status
```

### widget.html (FULL, размер 1x2)

```
Для каждого пользователя:
  Аватар (инициалы) + имя
  ● Дома (зелёный) / ○ Нет дома (серый)
  Последний визит: "14:32"
  Метод: wifi / bt / gps

Внизу:
  "2 из 3 дома"
```

**Зависимости:**

```
bleak>=0.21          # Bluetooth (опционально)
```

**Тесты:**

```python
# test: ARP check returns True when MAC in /proc/net/arp
# test: grace_period prevents immediate away after not seen
# test: presence.home event on transition away→home
# test: presence.away event on transition home→away (after grace period)
# test: anyone_home/everyone_away published correctly
# test: multiple methods: any=True → home
# test: GPS webhook updates presence
```

---

## Модуль 6: `weather_service`

**Порт:** 8115  
**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 64 MB  

### Назначение

Получает данные о погоде из open-meteo API (бесплатно, без API ключа, работает offline в смысле без регистрации). Кэширует локально. Предоставляет данные другим модулям через события и API.

### 6.1 Источник данных

```python
# open-meteo.com — бесплатно, без ключа, GDPR-compliant

BASE_URL = "https://api.open-meteo.com/v1/forecast"

PARAMS = {
    "latitude":              config["latitude"],
    "longitude":             config["longitude"],
    "current":               "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
    "hourly":                "temperature_2m,precipitation_probability,weather_code",
    "daily":                 "temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_sum,weather_code",
    "timezone":              config["timezone"],
    "forecast_days":         3,
    "wind_speed_unit":       "ms",
    "temperature_unit":      "celsius",
}
```

**Коды погоды WMO → человекочитаемые:**

```python
WMO_CODES = {
    0:  "clear",
    1:  "mostly_clear",
    2:  "partly_cloudy",
    3:  "overcast",
    45: "fog",
    48: "icy_fog",
    51: "drizzle_light",
    53: "drizzle",
    61: "rain_light",
    63: "rain",
    65: "rain_heavy",
    71: "snow_light",
    73: "snow",
    75: "snow_heavy",
    80: "showers_light",
    81: "showers",
    82: "showers_heavy",
    95: "thunderstorm",
    99: "thunderstorm_hail",
}
```

### 6.2 Обновление данных

```python
# Обновлять каждые 30 минут (не чаще — open-meteo обновляет раз в час)
# Кэш в памяти + сохранить в /config для восстановления после рестарта

async def _fetch_weather(self):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(BASE_URL, params=PARAMS)
            resp.raise_for_status()
            raw = resp.json()

        self._cache = self._parse(raw)
        self._last_updated = datetime.utcnow()

        await self.publish_event("weather.updated", self._cache["current"])
        logger.info(f"Weather updated: {self._cache['current']['condition']}")

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        # Продолжать работу с кэшированными данными
```

### 6.3 Формат данных

```python
# weather.updated payload и GET /weather ответ:
{
  "current": {
    "temperature":  22.4,          # °C
    "humidity":     58,            # %
    "precipitation": 0.0,          # мм
    "condition":    "partly_cloudy",
    "wind_speed":   3.2,           # м/с
    "weather_code": 2,
    "updated_at":   "2026-03-21T14:00:00Z"
  },
  "today": {
    "temp_min":     14.0,
    "temp_max":     24.0,
    "precipitation_sum": 0.0,
    "condition":    "partly_cloudy",
    "sunrise":      "06:42",
    "sunset":       "19:18"
  },
  "forecast": [           # 3 дня
    { "date": "2026-03-22", "temp_min": 12, "temp_max": 20, "condition": "rain" },
    { "date": "2026-03-23", "temp_min": 10, "temp_max": 18, "condition": "rain_light" }
  ],
  "hourly": [             # 24 часа
    { "time": "15:00", "temperature": 23.1, "precipitation_probability": 5 }
  ]
}
```

### 6.4 API модуля

```
GET /weather              → текущие данные (из кэша)
GET /weather/forecast     → прогноз на 3 дня
GET /weather/hourly       → почасовой прогноз
POST /weather/refresh     → принудительное обновление
```

### 6.5 Events

**Публикуемые:**

```
weather.updated            { current: { temperature, humidity, condition, ... } }
weather.alert              { type: "heavy_rain"|"frost"|..., message }
```

**Алерты:**

```python
# После каждого обновления проверять пороги:
ALERTS = [
    ("frost",       lambda w: w["temperature"] < 2),
    ("heat",        lambda w: w["temperature"] > 35),
    ("heavy_rain",  lambda w: w["condition"] in ("rain_heavy", "showers_heavy")),
    ("strong_wind", lambda w: w["wind_speed"] > 15),
    ("thunderstorm",lambda w: "thunderstorm" in w["condition"]),
]
```

### widget.html (FULL, размер 2x1)

```
Иконка погоды (SVG, зависит от condition)
Температура: 22°
Влажность: 58% · Ветер: 3.2 м/с
"Переменная облачность"
Мини-прогноз: 3 иконки дней с min/max температурами
```

**Зависимости:**

```
httpx>=0.27
```

**Тесты:**

```python
# test: fetch returns correct structure (mock httpx)
# test: WMO code mapped to condition string
# test: cache returned when API unavailable
# test: weather.updated event published after fetch
# test: frost alert when temperature < 2°C
# test: no duplicate alerts in same hour
```

---

## Модуль 7: `energy_monitor`

**Порт:** 8114  
**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 64 MB  

### Назначение

Агрегирует данные о потреблении электроэнергии со всех умных розеток и устройств с поддержкой мониторинга мощности. Строит статистику, обнаруживает аномалии, показывает стоимость.

### 7.1 Сбор данных

```python
# Слушает device.state_changed события
# Если устройство имеет атрибуты power_w, energy_kwh — записывает

@on_event("device.state_changed")
async def on_state_changed(self, payload: dict):
    new_state = payload.get("new_state", {})

    power_w = new_state.get("power_w")         # текущая мощность Вт
    energy_kwh = new_state.get("energy_kwh")   # накопленная энергия кВт·ч

    if power_w is None and energy_kwh is None:
        return   # устройство не поддерживает мониторинг мощности

    device_id = payload["device_id"]
    await self._record(device_id, power_w, energy_kwh)
```

### 7.2 Хранение данных

```python
# Временные ряды в SQLite (отдельная БД модуля, не ядра)
# /var/lib/selena/modules/energy-monitor/energy.db

CREATE TABLE readings (
    device_id   TEXT NOT NULL,
    timestamp   DATETIME NOT NULL,
    power_w     REAL,           -- мгновенная мощность
    energy_kwh  REAL,           -- накопленная энергия
    PRIMARY KEY (device_id, timestamp)
);

CREATE TABLE daily_summary (
    device_id   TEXT NOT NULL,
    date        DATE NOT NULL,
    kwh_total   REAL NOT NULL,
    peak_w      REAL,
    cost_uah    REAL,
    PRIMARY KEY (device_id, date)
);

# Ротация: хранить сырые данные 7 дней, daily summary — 1 год
```

### 7.3 Агрегация и аномалии

```python
# Подсчёт общего потребления:
async def get_total_power_now(self) -> float:
    """Сумма текущей мощности всех устройств (Вт)."""
    ...

# Аномалия: устройство потребляет дольше обычного
async def check_anomalies(self):
    for device_id, stats in self._device_stats.items():
        if stats.consecutive_on_minutes > stats.avg_on_minutes * 2:
            await self.publish_event("energy.anomaly", {
                "device_id":       device_id,
                "type":            "unusually_long_on",
                "duration_minutes": stats.consecutive_on_minutes,
                "normal_minutes":  stats.avg_on_minutes,
                "message":         f"Устройство работает уже {stats.consecutive_on_minutes} мин (обычно {stats.avg_on_minutes})"
            })
```

### 7.4 API модуля

```
GET /energy/now              → текущая мощность всего дома (Вт)
GET /energy/today            → потребление сегодня (кВт·ч, стоимость)
GET /energy/devices          → потребление по устройствам
GET /energy/history?days=7   → история по дням
GET /energy/forecast         → прогноз на месяц (на основе истории)
```

### 7.5 Events

**Публикуемые:**

```
energy.total_power     { watts: 1840.5, timestamp }   ← каждые 60 сек
energy.anomaly         { device_id, type, message }
energy.daily_summary   { date, kwh_total, cost, by_device: [...] }
```

**Слушает:**

```
device.state_changed   → записать показания если есть power_w/energy_kwh
```

### Настройки (settings.html)

```
Тариф электроэнергии (грн/кВт·ч или USD/kWh)
Валюта отображения
Порог аномалии (множитель от среднего)
Период хранения данных (дни)
```

### widget.html (FULL, размер 2x1)

```
Большое число: "1840 Вт" (сейчас)
Сегодня: 14.2 кВт·ч · ≈ $1.84
Мини-график: потребление за 24 часа (SVG sparkline)
Топ-3 потребителя сейчас
```

**Зависимости:**

```
aiosqlite>=0.19
```

**Тесты:**

```python
# test: reading recorded on device.state_changed with power_w
# test: total power aggregated correctly across devices
# test: anomaly detected when duration > 2x average
# test: daily_summary computed correctly
# test: old readings rotated after 7 days
# test: energy.anomaly event published with correct payload
```

---

## Модуль 8: `notification_router`

**Порт:** 8116  
**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 64 MB  

### Назначение

Маршрутизатор уведомлений. Другие модули публикуют событие `notification.send` — router решает куда его доставить: TTS голосом, Telegram, Web Push, или всё сразу. Пользователь настраивает правила маршрутизации.

### 8.1 Отправка уведомления (входной интерфейс)

```python
# Любой модуль может опубликовать:
await self.publish_event("notification.send", {
    "message":   "Движение обнаружено у входной двери",
    "title":     "Безопасность",     # опционально
    "priority":  "high",              # low | normal | high | critical
    "channel":   "all",               # конкретный канал или "all"
    "icon":      "security",          # опционально
    "data":      { ... }              # дополнительные данные
})
```

### 8.2 Каналы доставки

**TTS (через voice_core):**

```python
async def _send_tts(self, notification: Notification):
    await self.publish_event("voice.speak", {
        "text":   notification.message,
        "lang":   self._config.get("tts_lang", "ru"),
        "volume": self._config.get("tts_volume", 0.8),
    })
```

**Telegram Bot:**

```python
# Токен бота — через Secrets Vault (OAuth не нужен, просто Bot Token)
async def _send_telegram(self, notification: Notification):
    token   = await self._get_secret("telegram_bot_token")
    chat_id = self._config["telegram_chat_id"]
    text    = f"*{notification.title}*\n{notification.message}" if notification.title else notification.message

    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        )
```

**Web Push (через notify_push системный модуль):**

```python
async def _send_push(self, notification: Notification):
    await self.publish_event("push.send", {
        "title":   notification.title or "SelenaCore",
        "body":    notification.message,
        "icon":    notification.icon,
        "data":    notification.data,
    })
```

**Email (через SMTP):**

```python
import aiosmtplib
from email.mime.text import MIMEText

async def _send_email(self, notification: Notification):
    cfg = self._config["email"]
    msg = MIMEText(notification.message)
    msg["Subject"] = notification.title or "SelenaCore"
    msg["From"]    = cfg["from"]
    msg["To"]      = cfg["to"]

    await aiosmtplib.send(msg,
        hostname=cfg["host"], port=cfg["port"],
        username=cfg.get("username"),
        password=cfg.get("password"),
        use_tls=cfg.get("tls", True)
    )
```

### 8.3 Правила маршрутизации

```python
# Настройки: список правил (проверяются по порядку, применяются все совпадающие)

ROUTING_RULES = [
    {
        "name":     "critical → все каналы",
        "filter":   { "priority": "critical" },
        "channels": ["tts", "telegram", "push"]
    },
    {
        "name":     "ночью → только push, не TTS",
        "filter":   { "priority": ["high", "normal"] },
        "time_range": { "from": "22:00", "to": "08:00" },
        "channels": ["push"]     # НЕ tts чтобы не будить
    },
    {
        "name":     "всё остальное → TTS + push",
        "filter":   {},           # совпадает со всем
        "channels": ["tts", "push"]
    }
]
```

### 8.4 Events

**Слушает:**

```
notification.send      { message, title?, priority?, channel?, icon?, data? }
voice.speak_done       { text }    ← подтверждение от voice_core
```

**Публикуемые:**

```
notification.delivered { message, channels: [...], timestamp }
notification.failed    { message, channel, error }
```

### Настройки (settings.html)

```
Telegram:
  Bot Token (через Secrets Vault — кнопка "Подключить")
  Chat ID
  
Email:
  SMTP host/port, from/to, username/password, TLS

TTS:
  Язык, громкость
  
Правила маршрутизации:
  Таблица с фильтрами и каналами
  "Тест" — отправить тестовое уведомление
```

**Зависимости:**

```
httpx>=0.27
aiosmtplib>=3.0
```

**Тесты:**

```python
# test: notification.send → TTS event published (mock)
# test: notification.send → Telegram POST called (mock httpx)
# test: routing rule priority=critical → all channels
# test: time_range rule blocks TTS during night hours
# test: channel="tts" explicitly → only TTS sent
# test: failed delivery → notification.failed event
```

---

## Модуль 9: `update_manager`

**Порт:** 8113  
**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 64 MB  

### Назначение

OTA (Over-The-Air) обновления SelenaCore и системных модулей. Проверяет GitHub Releases, скачивает, верифицирует SHA256, применяет с возможностью rollback.

### 9.1 Проверка обновлений

```python
RELEASES_URL = "https://api.github.com/repos/dotradepro/SelenaCore/releases/latest"

async def check_updates(self) -> UpdateInfo | None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(RELEASES_URL,
            headers={"Accept": "application/vnd.github.v3+json"})

    release = resp.json()
    latest_version = release["tag_name"].lstrip("v")   # "0.4.0"
    current_version = self._current_version()           # из VERSION файла

    if latest_version == current_version:
        return None

    # Найти asset с sha256sum файлом
    assets = {a["name"]: a for a in release["assets"]}
    return UpdateInfo(
        version=latest_version,
        download_url=assets["selenacore.tar.gz"]["browser_download_url"],
        sha256_url=assets["selenacore.tar.gz.sha256"]["browser_download_url"],
        changelog=release["body"],
        published_at=release["published_at"],
    )
```

### 9.2 Процесс обновления

```python
async def apply_update(self, update: UpdateInfo) -> bool:
    # Шаг 1: Скачать архив
    archive_path = Path("/tmp/selenacore-update.tar.gz")
    await self._download(update.download_url, archive_path,
                         progress_callback=self._emit_progress)

    # Шаг 2: Верифицировать SHA256 (ОБЯЗАТЕЛЬНО)
    sha256_file = await self._fetch_text(update.sha256_url)
    expected_hash = sha256_file.split()[0]
    actual_hash   = sha256(archive_path.read_bytes()).hexdigest()

    if actual_hash != expected_hash:
        logger.error(f"SHA256 mismatch! Expected {expected_hash}, got {actual_hash}")
        await self.publish_event("update.failed", {
            "version": update.version,
            "reason":  "sha256_mismatch"
        })
        return False

    # Шаг 3: Создать backup текущей версии
    backup_dir = Path("/secure/core_backup") / self._current_version()
    shutil.copytree("/opt/selenacore/core", backup_dir, dirs_exist_ok=True)

    # Шаг 4: Распаковать в временную директорию
    tmp_dir = Path("/tmp/selenacore-new")
    shutil.unpack_archive(str(archive_path), str(tmp_dir))

    # Шаг 5: Обновить core.manifest и master.hash для новых файлов
    await self._update_manifest(tmp_dir)

    # Шаг 6: Применить (атомарная замена через rename)
    shutil.copytree(tmp_dir / "core", "/opt/selenacore/core",
                    dirs_exist_ok=True)

    # Шаг 7: Перезапустить ядро через systemd
    subprocess.run(["systemctl", "restart", "smarthome-core"],
                   check=True)

    await self.publish_event("update.applied", {
        "version":    update.version,
        "from":       self._current_version(),
        "applied_at": datetime.utcnow().isoformat()
    })
    return True
```

### 9.3 Автоматическая проверка

```python
# Проверять обновления раз в сутки в 03:00
# Через scheduler:
await self.publish_event("scheduler.register", {
    "job_id":     "update_manager:daily_check",
    "trigger":    "cron:0 3 * * *",
    "event_type": "update.check_requested",
    "payload":    {},
    "owner":      "update-manager"
})
```

### 9.4 Events

**Публикуемые:**

```
update.available    { version, changelog, published_at }
update.downloading  { version, progress_percent }
update.applying     { version }
update.applied      { version, from, applied_at }
update.failed       { version, reason }
update.no_updates   { current_version }
```

**Слушает:**

```
update.check_requested    → запустить проверку
update.apply_requested    → { version } → применить
```

### widget.html (FULL, размер 2x1)

```
Текущая версия: v0.3.0-beta
Статус: ✓ Актуальная версия / ⚠ Доступна v0.4.0

Если доступно обновление:
  Версия, дата, первые 200 символов changelog
  [Обновить] button (с подтверждением)
  
Прогресс-бар при загрузке/применении
```

**Зависимости:**

```
httpx>=0.27
```

**Тесты:**

```python
# test: GitHub API returns newer version → update.available published
# test: same version → update.no_updates
# test: SHA256 mismatch → update.failed, no files changed
# test: download progress emitted (mock)
# test: backup created before applying update
# test: manifest updated after applying update
```

---

## Модуль 10: `import_adapters`

**Порт:** 8117  
**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 128 MB  

### Назначение

Импорт устройств из существующих экосистем. Пользователь уже использует Home Assistant, Tuya или Philips Hue — нужно перенести все устройства в SelenaCore одним кликом. Это был отдельный модуль, теперь расширяем спецификацию.

### 10.1 Home Assistant импорт

```python
# Подключение через Long-Lived Access Token HA

async def import_from_ha(self, ha_url: str, token: str):
    async with httpx.AsyncClient(
        base_url=ha_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0
    ) as client:
        # Получить все states
        resp = await client.get("/api/states")
        states = resp.json()

    imported = 0
    for state in states:
        entity_id = state["entity_id"]
        domain    = entity_id.split(".")[0]

        # Маппинг HA domain → SelenaCore type
        device_type = HA_DOMAIN_MAP.get(domain)
        if not device_type:
            continue   # пропустить неизвестные домены

        await self.register_device(
            name         = state["attributes"].get("friendly_name", entity_id),
            type         = device_type,
            protocol     = "ha_import",
            capabilities = _extract_ha_capabilities(state),
            meta         = {
                "ha_entity_id": entity_id,
                "ha_url":       ha_url,
                "imported_at":  datetime.utcnow().isoformat(),
            }
        )
        imported += 1

    await self.publish_event("import.completed", {
        "source":   "home_assistant",
        "imported": imported,
        "total":    len(states)
    })
```

**HA Domain маппинг:**

```python
HA_DOMAIN_MAP = {
    "light":         "light",
    "switch":        "switch",
    "sensor":        "sensor",
    "binary_sensor": "binary_sensor",
    "climate":       "climate",
    "cover":         "cover",      # жалюзи, шторы
    "fan":           "fan",
    "lock":          "lock",
    "media_player":  "media_player",
    "camera":        "camera",
}
```

### 10.2 Tuya импорт

```python
# Через Tuya Open API (требует developer account)
# Токены через Secrets Vault (secrets.oauth)

async def import_from_tuya(self, region: str):
    # region: "eu" | "us" | "cn" | "in"
    token = await self._get_secret("tuya_access_token")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"https://openapi.tuya{region}.com/v2.0/cloud/thing/device",
            headers={
                "client_id":     self._config["tuya_client_id"],
                "access_token":  token,
                "sign_method":   "HMAC-SHA256",
                # подпись HMAC вычисляется по Tuya API spec
            }
        )
        devices = resp.json()["result"]["list"]

    for device in devices:
        await self.register_device(
            name         = device["name"],
            type         = _tuya_category_to_type(device["category"]),
            protocol     = "tuya",
            capabilities = [],
            meta         = {
                "tuya_device_id": device["id"],
                "tuya_product_id": device["product_id"],
                "tuya_category": device["category"],
            }
        )
```

### 10.3 Philips Hue импорт

```python
# Через локальный Hue Bridge API (без cloud)
# Авторизация: нажать кнопку на Bridge → получить username

async def import_from_hue(self, bridge_ip: str, username: str):
    async with httpx.AsyncClient(
        base_url=f"http://{bridge_ip}/api/{username}",
        timeout=10.0
    ) as client:
        lights  = (await client.get("/lights")).json()
        sensors = (await client.get("/sensors")).json()

    for light_id, light in lights.items():
        await self.register_device(
            name         = light["name"],
            type         = "light",
            protocol     = "hue",
            capabilities = ["brightness", "color", "color_temp"],
            meta         = {
                "hue_light_id":  light_id,
                "hue_bridge_ip": bridge_ip,
                "hue_username":  username,
                "hue_type":      light["type"],
            }
        )
```

### 10.4 Events

**Публикуемые:**

```
import.started      { source }
import.progress     { source, imported, total }
import.completed    { source, imported, skipped, total }
import.failed       { source, error }
```

### Настройки (settings.html)

```
Вкладка "Home Assistant":
  URL: http://homeassistant.local:8123
  Token: [Введите Long-Lived Token]
  [Импортировать] button

Вкладка "Tuya":
  Client ID, Client Secret
  Регион: EU / US / CN / IN
  [Авторизовать через OAuth] → [Импортировать]

Вкладка "Philips Hue":
  IP адрес Bridge
  [Нажмите кнопку на Bridge] → [Получить токен] → [Импортировать]

История импортов:
  Дата | Источник | Импортировано | Статус
```

**Зависимости:**

```
httpx>=0.27
```

**Тесты:**

```python
# test: HA states imported correctly (mock httpx)
# test: unknown HA domain skipped without error
# test: Tuya HMAC signature computed correctly
# test: Hue lights registered with correct capabilities
# test: import.completed event with correct counts
# test: import.failed on connection error
```

---

## Критерии готовности всех модулей

### Каждый модуль должен:

- [ ] Иметь `manifest.json` с корректными полями
- [ ] Отвечать `GET /health → 200 { status: "ok" }`
- [ ] Отдавать `GET /widget.html` (если `ui_profile != HEADLESS`)
- [ ] Отдавать `GET /settings.html` (если есть настройки)
- [ ] Читать `SELENA_MODULE_TOKEN`, `SELENA_WEBHOOK_SECRET`, `SELENA_CORE_URL` из env
- [ ] Проверять HMAC подпись входящих webhook (SDK делает автоматически)
- [ ] Иметь все `async def` на публичных методах
- [ ] Иметь type hints на всех публичных методах
- [ ] Иметь тесты покрывающие основную логику
- [ ] Проходить `pytest tests/ -x -q`
- [ ] Проходить `python -m mypy <module_dir>/`
- [ ] Иметь `Dockerfile` и `requirements.txt`
- [ ] Не использовать `print()`, `eval()`, `exec()`
- [ ] Не обращаться к `/secure/` напрямую
- [ ] Не публиковать `core.*` события

### Интеграционные требования:

- [ ] `scheduler` работает и правильно вычисляет sunrise/sunset для заданных координат
- [ ] `automation_engine` регистрирует триггеры в `scheduler` при старте
- [ ] `automation_engine` реагирует на `device.state_changed` от `protocol_bridge`
- [ ] `automation_engine` использует `presence_detection` через `presence.home/away` события
- [ ] `automation_engine` использует `weather_service` через `weather.updated` события
- [ ] `automation_engine` отправляет уведомления через `notification.send`
- [ ] `device_watchdog` получает heartbeat от `protocol_bridge`
- [ ] `notification_router` доставляет через TTS (публикует `voice.speak`)
- [ ] `update_manager` использует `scheduler` для ежесуточной проверки

### Тест интеграции (конец реализации):

```python
# tests/test_integration.py

# Сценарий: "Утреннее освещение"
# 1. scheduler отправляет событие в 07:00
# 2. automation_engine получает, проверяет условие (кто-то дома)
# 3. presence_detection говорит "Alice дома"
# 4. automation_engine отправляет device_state для лампочки
# 5. protocol_bridge получает state_changed и публикует MQTT команду
# 6. notification_router получает notification.send → TTS
# 7. voice_core получает voice.speak

# Всё через mock SDK без реального Core API
```

---

## Git workflow

```bash
# Ветка
git checkout -b feat/N-system-modules

# Коммиты по шагам:
git commit -m "feat(scheduler): implement cron/interval/astro triggers [#N]"
git commit -m "feat(device_watchdog): add ARP and MQTT presence check [#N]"
git commit -m "feat(protocol_bridge): add MQTT broker and Zigbee bridge [#N]"
git commit -m "feat(automation_engine): implement YAML rules engine [#N]"
git commit -m "feat(presence_detection): add WiFi ARP and BT detection [#N]"
git commit -m "feat(weather_service): add open-meteo integration [#N]"
git commit -m "feat(energy_monitor): add power tracking and anomalies [#N]"
git commit -m "feat(notification_router): add TTS/Telegram/push routing [#N]"
git commit -m "feat(update_manager): add OTA with SHA256 verification [#N]"
git commit -m "feat(import_adapters): add HA/Tuya/Hue import [#N]"
git commit -m "test(system_modules): add integration tests [#N]"

# Мёрдж
git checkout main
git merge feat/N-system-modules
git push origin main
```

---

## Связанные документы

```
docs/architecture.md              ← компоненты ядра
docs/module-core-protocol.md      ← токены, HMAC, lifecycle
docs/module-development.md        ← SDK, manifest, permissions
docs/deployment.md                ← Raspberry Pi деплой
CONTRIBUTING.md                   ← стандарты кода
```
