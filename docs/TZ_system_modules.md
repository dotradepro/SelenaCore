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

## Модуль 11: `media_player`

**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 128 MB  
**CPU:** 0.5  

### Назначение

Медиаплеер: интернет-радио, USB/SD, SMB/CIFS сетевые шары, Internet Archive. Голосовое управление, обложки альбомов, плейлисты M3U/PLS.

### 11.1 Движок воспроизведения

```python
# Бэкенд: libvlc (python-vlc) в headless-режиме
# Поддерживаемые форматы: MP3, OGG, FLAC, WAV, OPUS, HTTP streams, M3U/PLS

SUPPORTED_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav", ".opus", ".m3u", ".pls"}
```

### 11.2 Источники аудио

**Интернет-радио (RadioBrowser API):**

```python
# RadioBrowserSource — поиск по тегу, стране, языку
# Локальная библиотека станций: /var/lib/selena/modules/media-player/stations.json
# Эндпоинт: POST /api/import/radiobrowser?tag=jazz&country=uk
```

**USB/SD медиа:**

```python
# USBSource — автодетект подключённых USB-дисков
# Рекурсивный скан аудиофайлов
# Эндпоинт: GET /import/usb/scan
```

**SMB/CIFS сетевые шары:**

```python
# SMBSource — подключение к сетевым папкам
# Учётные данные: username, password, domain (default: WORKGROUP)
# Эндпоинт: POST /api/import/smb
```

**Internet Archive.org:**

```python
# InternetArchiveSource — публичные коллекции (музыка, аудиокниги)
# Эндпоинт: POST /api/import/archive?query=public+radio
```

### 11.3 Обложки альбомов

```python
# CoverFetcher — Last.fm API (требует API ключ)
# Кеш: /var/lib/selena/modules/media-player/covers/
# Конфиг: MEDIA_LASTFM_API_KEY
```

### 11.4 Голосовое управление

```python
# MediaVoiceHandler — слушает voice.intent события
# Интенты: media.play_artist, media.pause, media.stop, media.next, media.previous
# Триггер: "включи музыку", "поставь на паузу", "следующий трек"
```

### 11.5 API модуля

```
GET  /player/state              → текущее состояние (track, position, volume)
POST /player/play               → начать воспроизведение
POST /player/pause              → пауза
POST /player/stop               → стоп
POST /player/next               → следующий трек
POST /player/previous           → предыдущий трек
POST /player/volume             → { volume: 0-100 }
POST /player/seek               → { position: <seconds> }

GET  /radio/stations            → список станций
POST /radio/add-station         → добавить станцию
POST /radio/import-m3u          → импорт M3U плейлиста
POST /import/radiobrowser       → импорт из RadioBrowser
POST /import/smb                → импорт с SMB шары
POST /import/archive            → импорт с Internet Archive
GET  /import/usb/scan           → скан USB-дисков

POST /config                    → обновить настройки
```

### 11.6 Трансляция состояния

```python
# Каждые 3 секунды во время воспроизведения:
await self.publish_event("media.state_changed", {
    "state":    "playing",      # "playing" | "paused" | "stopped"
    "track":    "Song Name",
    "artist":   "Artist",
    "album":    "Album",
    "cover_url": "/covers/abc.jpg",
    "position": 45.2,           # секунды
    "duration": 210.0,
})
```

### 11.7 Events

**Публикуемые:**

```
media.state_changed    { state, track, artist, album, cover_url, position, duration }
```

**Слушает:**

```
voice.intent           → обработка media.* интентов
```

### 11.8 Настройки

```
MEDIA_LASTFM_API_KEY=...       # API ключ Last.fm для обложек
MEDIA_DEFAULT_VOLUME=70         # громкость по умолчанию (0-100)
MEDIA_STREAM_BUFFER_MS=1000     # буфер потока (мс)
MEDIA_NORMALIZE=false           # нормализация громкости
```

### widget.html (FULL, размер 2x2)

```
Обложка альбома (если доступна)
Название трека · Исполнитель
Прогресс-бар с таймером
Кнопки: ⏮ ▶/⏸ ⏭ 🔊
Мини-плейлист: 3-5 треков
```

**Зависимости:**

```
python-vlc>=3.0
httpx>=0.27
smbprotocol>=1.10       # для SMB
```

**Тесты:**

```python
# test: play/pause/stop/next/previous state transitions
# test: radio station added and persisted
# test: M3U playlist imported correctly
# test: USB scan finds audio files
# test: media.state_changed event published every 3 sec
# test: voice intent media.pause triggers pause
# test: volume set correctly (0-100 range validation)
```

---

## Модуль 12: `voice_core`

**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 256 MB  
**CPU:** 0.5  

### Назначение

Голосовая подсистема SelenaCore. Включает: распознавание речи (STT, Vosk), синтез речи (TTS, Piper), детектор wake-word (openWakeWord), идентификацию говорящего (resemblyzer), режим приватности (отключение микрофонов через GPIO).

### 12.1 Распознавание речи (STT)

```python
# Движок: Vosk (офлайн, поддержка украинского и русского)
# Модель: настраивается через VOSK_MODEL (default: vosk-model-small-uk)
# Sample rate: 16 kHz, mono
# WebSocket стриминг: WS /api/ui/modules/voice-core/stream

# Реальное время: аудио с микрофона → Vosk → текст → Intent Router
```

### 12.2 Синтез речи (TTS)

```python
# Движок: Piper (нейросетевой, локальный)
# Голоса:
VOICES = {
    "uk_UA-ukrainian_tts-medium": "Українська (середня якість)",
    "uk_UA-lada-medium":          "Українська Lada",
    "ru_RU-irina-medium":         "Русский Irina",
    "ru_RU-ruslan-medium":        "Русский Ruslan",
    "en_US-amy-medium":           "English Amy",
    "en_US-ryan-high":            "English Ryan (HQ)",
}

# Эндпоинты:
# GET  /tts/voices     → список голосов
# POST /tts/test       → тест синтеза (возвращает WAV)
```

### 12.3 Детектор wake-word

```python
# Движок: openWakeWord (ONNX inference)
# Wake-word по умолчанию: "hey_selena"
# Порог: 0.1–1.0 (default 0.5, настраивается)
# Фоновый цикл: постоянное прослушивание микрофона через asyncio

# При обнаружении → публикует voice.wake_word событие
# → запускает STT запись → текст → Intent Router
```

### 12.4 Идентификация говорящего (Speaker ID)

```python
# Движок: resemblyzer (голосовые эмбеддинги)
# Хранение: numpy arrays в /var/lib/selena/speaker_embeddings/
# Порог схожести: 0.75 (настраивается)

# Эндпоинты:
# GET    /speakers                → список зарегистрированных
# DELETE /speakers/{user_id}      → удалить голосовой слепок
```

### 12.5 Режим приватности

```python
# GPIO кнопка (pin 17, настраивается) + голосовая команда
# При активации:
#   - Полная остановка STT/wake-word прослушивания
#   - LED индикатор (если подключён)
#   - Публикация voice.privacy_on события

# Эндпоинты:
# GET  /privacy                → текущий статус
# POST /privacy/toggle         → переключить
```

### 12.6 История голосовых запросов

```python
# Хранение: SQLite в /var/lib/selena/selena.db
# Таблица: voice_history(id, timestamp, user_id, wake_word,
#                         recognized_text, intent, response, duration_ms)
# Ротация: максимум 10,000 записей

# Эндпоинт: GET /history?limit=50
```

### 12.7 Управление аудиоустройствами

```python
# Автодетект: ALSA карты (/proc/asound/cards) + PulseAudio/PipeWire (Bluetooth)
# Приоритет входа:  USB > I2S GPIO > Bluetooth > HDMI > встроенный
# Приоритет выхода: USB > I2S GPIO > Bluetooth > HDMI > jack

# Эндпоинт: GET /audio/devices → список входов и выходов
```

### 12.8 API модуля

```
GET  /config               → настройки STT/TTS/wake-word
POST /config               → обновить настройки
GET  /privacy              → статус режима приватности
POST /privacy/toggle       → переключить приватность
GET  /audio/devices        → список аудиоустройств
GET  /stt/status           → статус STT
WS   /stream               → WebSocket стриминг аудио
GET  /tts/voices           → список голосов TTS
POST /tts/test             → тестовый синтез
GET  /wakeword/status      → статус wake-word детектора
GET  /speakers             → список зарегистрированных голосов
DELETE /speakers/{user_id} → удалить голосовой слепок
GET  /history?limit=50     → история запросов
```

### 12.9 Events

**Публикуемые:**

```
voice.wake_word        { wake_word, score }
voice.recognized       { text, user_id, duration_ms }
voice.privacy_on       { privacy_mode: true }
voice.privacy_off      { privacy_mode: false }
voice.speak_done       { text }
```

**Слушает:**

```
voice.speak            { text, lang?, volume? }  → синтез TTS и воспроизведение
```

### widget.html (FULL, размер 2x2)

```
Индикатор микрофона (активен / приватность)
Последний распознанный текст
Статус STT/TTS/Wake-word (зелёный/красный)
Кнопка "Тест TTS"
Кнопка "Приватность вкл/выкл"
```

**Зависимости:**

```
vosk>=0.3
piper-tts>=1.0
openwakeword>=0.6
resemblyzer>=0.1
pyaudio>=0.2
RPi.GPIO>=0.7        # опционально, только Raspberry Pi
```

**Тесты:**

```python
# test: STT возвращает текст из аудио (mock Vosk)
# test: TTS генерирует WAV (mock Piper)
# test: wake-word обнаружен при score > threshold (mock)
# test: speaker ID совпадает с зарегистрированным (mock resemblyzer)
# test: privacy toggle публикует voice.privacy_on/off
# test: voice.speak событие → TTS → воспроизведение
# test: history ротация при > 10,000 записей
# test: аудио devices endpoint возвращает корректную структуру
```

---

## Модуль 13: `llm_engine`

**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 512 MB – 2 GB (зависит от модели)  
**CPU:** 1.0 – 2.0  

### Назначение

LLM движок и маршрутизатор интентов. Трёхуровневая архитектура: Fast Matcher (ключевые слова/regex, 0 мс) → Module Intents (HTTP к модулям, <1 с) → Ollama LLM (2–10 с). Автоматическое отключение LLM при нехватке RAM.

### 13.1 Fast Matcher (уровень 1)

```python
# Конфиг: /opt/selena-core/config/intent_rules.yaml
# Формат: YAML правила с keywords, regex шаблонами, response шаблонами, действиями

# Пример правила:
# - name: lights_on
#   keywords: ["включи свет", "turn on lights"]
#   regex: "(включи|turn on)\\s+(свет|light)"
#   response: "Включаю свет"
#   action: { type: "device_state", device_id: "@lights", state: { power: true } }

# Время отклика: < 1 мс (in-memory lookup)
# Перезагрузка: reload() обновляет правила из файла на лету
```

### 13.2 Module Intents (уровень 2)

```python
# Зарегистрированные модули могут объявить свои интенты
# Intent Router запрашивает каждый модуль HTTP POST /intent
# Если модуль понимает запрос — возвращает результат
# Время отклика: < 1 с
```

### 13.3 Ollama LLM (уровень 3)

```python
# Эндпоинт: http://localhost:11434 (настраивается OLLAMA_URL)
# Модель по умолчанию: phi3:mini (настраивается OLLAMA_MODEL)

# Рекомендуемые модели:
MODELS = {
    "phi3:mini":     {"params": "3.8B", "size": "2.2 GB", "note": "default, fast"},
    "gemma2:2b":     {"params": "2B",   "size": "1.6 GB", "note": "multilingual"},
    "qwen2.5:0.5b":  {"params": "0.5B", "size": "0.4 GB", "note": "ultra-lightweight"},
    "llama3.2:1b":   {"params": "1B",   "size": "0.7 GB", "note": "small English"},
}

# Авто-отключение: если свободная RAM < 5 GB (настраивается OLLAMA_MIN_RAM_GB)
# Temperature: 0.7 (настраивается)
# Max tokens: 512 (на запрос)
# API: /api/generate (streaming и non-streaming)
```

### 13.4 Model Manager

```python
# Управление моделями Ollama:
# - Список рекомендуемых моделей с статусом установки
# - Скачивание моделей через Ollama pull
# - Переключение активной модели (персистентно)
# - Автодетект невалидного выбора
```

### 13.5 Динамический системный промпт

```python
# При вызове LLM — автоматически формируется system prompt:
# - Список зарегистрированных устройств
# - Список доступных команд
# - Текущее время и дата
# - Статус присутствия (кто дома)
# - Контекст последних 5 голосовых запросов
```

### 13.6 API модуля

```
POST /intent               → { text: "включи свет" } → IntentResult
GET  /models               → список моделей с статусами
POST /models/pull          → { model: "phi3:mini" } → запуск скачивания
POST /models/switch        → { model: "gemma2:2b" } → переключить
GET  /rules                → текущие правила Fast Matcher
POST /rules/reload         → перезагрузить правила из YAML
GET  /health               → статус LLM (доступен / отключён по RAM)
```

### 13.7 Events

**Публикуемые:**

```
voice.intent           { intent, response, action, source, latency_ms }
llm.model_switched     { model, previous }
llm.disabled           { reason: "low_ram", available_gb }
llm.enabled            { model }
```

**Слушает:**

```
voice.recognized       { text, user_id }  → запуск Intent Router
```

### Настройки

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=phi3:mini
OLLAMA_TIMEOUT=30              # секунды
OLLAMA_MIN_RAM_GB=5.0          # порог отключения LLM
FAST_MATCHER_RULES=/opt/selena-core/config/intent_rules.yaml
```

**Зависимости:**

```
httpx>=0.27
pyyaml>=6.0
psutil>=5.9
```

**Тесты:**

```python
# test: Fast Matcher находит интент по ключевому слову
# test: Fast Matcher находит интент по regex
# test: Fast Matcher miss → fallback к Ollama (mock)
# test: Ollama отключён при RAM < 5 GB (mock psutil)
# test: model switch сохраняется между рестартами
# test: rules reload подхватывает изменения из YAML
# test: IntentResult содержит source, latency
# test: динамический system prompt содержит список устройств
```

---

## Модуль 14: `secrets_vault`

**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 64 MB  
**CPU:** 0.1  

### Назначение

Защищённое хранилище секретов и OAuth-токенов. AES-256-GCM шифрование. OAuth Device Authorization Grant (RFC 8628) с QR-кодами. API-прокси для модулей — модули НИКОГДА не видят токены.

### 14.1 Зашифрованное хранилище

```python
# Хранение: /secure/tokens/<service>.enc
# Мастер-ключ: /secure/vault_key (base64, 256 бит)
# Шифрование: AES-256-GCM с рандомным 96-бит nonce на каждый секрет
# Ключ генерируется автоматически при первом запуске

# Модель данных:
@dataclass
class SecretRecord:
    access_token: str
    refresh_token: str | None
    expires_at: float | None
    scopes: list[str]
    extra: dict
```

### 14.2 OAuth Device Authorization Grant (RFC 8628)

```python
# Провайдеры: Google, GitHub (расширяемо через KNOWN_PROVIDERS)
# Поток:
# 1. POST /api/v1/secrets/oauth/start → session_id, user_code, verification_uri, QR
# 2. Пользователь сканирует QR или вводит код на сайте провайдера
# 3. Модуль поллит GET /api/v1/secrets/oauth/status/{session_id}
# 4. При авторизации → токены шифруются и сохраняются в vault
# QR-код: генерируется на лету (qrcode библиотека)
# Экспирация сессии: 30 минут (настраивается)
```

### 14.3 API-прокси (Token Injection)

```python
# POST /api/v1/secrets/proxy
# Назначение: пересылка HTTP-запросов к внешним API с подстановкой токена
# Безопасность:
#   - Только HTTPS URL (защита от SSRF)
#   - Блокировка приватных IP-диапазонов (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8)
#   - Токены НИКОГДА не возвращаются модулю
#   - Максимальный размер ответа: 5 MB

# Запрос:
# { "service": "google", "method": "GET",
#   "url": "https://gmail.googleapis.com/...",
#   "extra_headers": {}, "json_body": null, "params": {} }

# Ответ: { "status": 200, "headers": {...}, "body": {...} }
```

### 14.4 Автообновление токенов

```python
# Фоновая задача: проверяет все токены каждые 60 секунд
# Авто-обновление: за 5 минут до истечения через refresh_token
# PBKDF2: 600,000 итераций (RFC 8617)
```

### 14.5 API модуля

```
POST /api/v1/secrets/oauth/start          → начать OAuth-поток
GET  /api/v1/secrets/oauth/status/{id}    → статус сессии
GET  /api/v1/secrets/oauth/qr/{id}        → QR-код (PNG)
POST /api/v1/secrets/proxy                → API-прокси запрос
GET  /api/v1/secrets/services             → список подключённых сервисов
DELETE /api/v1/secrets/services/{name}    → отключить сервис
```

### 14.6 Events

**Публикуемые:**

```
secrets.token_refreshed   { service, expires_at }
secrets.token_expired     { service, reason }
secrets.oauth_completed   { service, module }
```

### Структура каталогов

```
/secure/
  vault_key                    # Мастер-ключ (permissions 600)
  tokens/
    google.enc                 # Зашифрованные токены
    github.enc
    tuya.enc
```

**Зависимости:**

```
cryptography>=46.0
httpx>=0.27
qrcode>=7.4
```

**Тесты:**

```python
# test: store/retrieve секрет → расшифровка корректна
# test: AES-256-GCM nonce уникален для каждого секрета
# test: OAuth start возвращает session_id и user_code
# test: OAuth status polling → authorized после мок-авторизации
# test: proxy блокирует HTTP URL (только HTTPS)
# test: proxy блокирует приватные IP (SSRF protection)
# test: auto-refresh за 5 минут до истечения (mock time)
# test: vault_key генерируется при первом запуске
```

---

## Модуль 15: `user_manager`

**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 128 MB  
**CPU:** 0.2  

### Назначение

Управление пользователями SelenaCore. CRUD профилей (admin/resident/guest), PIN-аутентификация с rate limiting, Face ID через face_recognition, голосовая биометрия через resemblyzer, аудит-лог действий.

### 15.1 Профили пользователей

```python
# Хранение: SQLite в /var/lib/selena/selena.db
# Роли: admin | resident | guest

# Таблица users:
# user_id TEXT PK, username TEXT UNIQUE, display_name TEXT,
# role TEXT DEFAULT 'resident', pin_hash TEXT,
# created_at REAL, last_seen REAL,
# face_enrolled INTEGER DEFAULT 0, voice_enrolled INTEGER DEFAULT 0,
# active INTEGER DEFAULT 1
```

### 15.2 PIN-аутентификация

```python
# Алгоритм: SHA-256 с солью "selena-pin-salt-v1"
# Защита от brute-force:
#   - Максимум 5 неудачных попыток на пользователя
#   - После 5 попыток → блокировка на 10 минут (LOCK_DURATION_SEC = 600)
#   - Lock state: в памяти (сбрасывается при рестарте)
```

### 15.3 Face ID

```python
# Движок: face_recognition (dlib бэкенд)
# Регистрация: JPEG из веб-камеры браузера → 128-мерный face encoding
# Хранение: numpy arrays в /var/lib/selena/face_encodings/
# Верификация: сравнение с всеми зарегистрированными
# Порог: 0.5 (default, настраивается FACE_TOLERANCE, ниже = строже)

# Функции:
# enroll(user_id, jpeg_bytes) → bool
# identify(jpeg_bytes) → user_id | None
# list_enrolled() → list[user_id]
```

### 15.4 Голосовая биометрия

```python
# Движок: resemblyzer (через voice_core)
# Регистрация: запись голоса → вычисление эмбеддинга → сохранение
# Идентификация: сравнение с зарегистрированными эмбеддингами
# Порог: 0.75 (default)
```

### 15.5 Аудит-лог

```python
# Хранение: SQLite таблица audit_log
# Поля: timestamp, user_id, action, resource, result
# Ротация: 10,000 записей
# Действия: login, logout, pin_failed, face_enrolled, device_added, etc.
```

### 15.6 API модуля

```
GET    /users                      → список пользователей
POST   /users                      → создать пользователя
GET    /users/{user_id}            → профиль
PUT    /users/{user_id}            → обновить
DELETE /users/{user_id}            → удалить
POST   /auth/pin                   → { user_id, pin } → аутентификация
POST   /auth/face                  → multipart JPEG → идентификация
POST   /users/{id}/face/enroll     → multipart JPEG → регистрация Face ID
DELETE /users/{id}/face            → удалить Face ID
POST   /users/{id}/voice/enroll    → аудио → регистрация голоса
DELETE /users/{id}/voice           → удалить голосовой слепок
GET    /audit?limit=100            → аудит-лог
```

### 15.7 Events

**Публикуемые:**

```
user.authenticated     { user_id, method: "pin"|"face"|"voice" }
user.login_failed      { user_id, method, reason }
user.lockout           { user_id, duration_sec: 600 }
user.created           { user_id, username, role }
user.deleted           { user_id }
```

### widget.html (FULL, размер 2x1)

```
Список пользователей:
  Аватар · Имя · Роль · Последний вход
  Значки: 🔐 PIN | 👤 Face ID | 🎤 Voice ID
Кнопка "Добавить пользователя"
```

**Зависимости:**

```
SQLAlchemy>=2.0
aiosqlite>=0.19
face_recognition>=1.3
numpy>=1.24
```

**Тесты:**

```python
# test: создание пользователя → сохранение в БД
# test: PIN-аутентификация → успех с корректным PIN
# test: PIN-аутентификация → отказ при неверном PIN
# test: 5 неудачных попыток → блокировка 10 минут
# test: Face ID enroll → face_enrolled = 1
# test: Face ID identify → корректный user_id
# test: аудит-лог записывает все действия
# test: ротация аудит-лога при > 10,000 записей
```

---

## Модуль 16: `hw_monitor`

**Тип:** SYSTEM  
**ui_profile:** ICON_SETTINGS  
**Память:** 32 MB  
**CPU:** 0.05  

### Назначение

Мониторинг аппаратных ресурсов: температура CPU, использование RAM и диска. Алерты при превышении порогов. Автоматическая деградация (остановка модулей) при критической нагрузке.

### 16.1 Сбор метрик

```python
# Источники данных:
# CPU температура: /sys/class/thermal/ или vcgencmd (Raspberry Pi)
# RAM: /proc/meminfo (процент, MB использовано, MB всего)
# Диск: shutil.disk_usage() (процент, свободно GB)

@dataclass
class SystemMetrics:
    cpu_temp_c: float | None     # °C
    ram_used_pct: float          # %
    ram_used_mb: float
    ram_total_mb: float
    disk_used_pct: float         # %
    disk_free_gb: float
```

### 16.2 Пороги алертов

```python
CPU_TEMP_WARN  = 70.0   # °C
CPU_TEMP_CRIT  = 85.0   # °C
RAM_WARN_PCT   = 80     # %
RAM_CRIT_PCT   = 92     # %
DISK_WARN_PCT  = 85     # %
DISK_CRIT_PCT  = 95     # %
MONITOR_INTERVAL = 30   # секунды
```

### 16.3 Стратегия деградации RAM

```python
# При RAM > 92%:
# 1. Отправить hw.ram_crit событие
# 2. Остановить опциональные модули в порядке приоритета (low → high)
# 3. Системные модули (voice_core, llm_engine) — последние
# Модуль throttle.py управляет порядком остановки
```

### 16.4 API модуля

```
GET /metrics              → текущие метрики (CPU, RAM, диск)
GET /metrics/history      → история за последний час
GET /thresholds           → текущие пороги
POST /thresholds          → обновить пороги
```

### 16.5 Events

**Публикуемые:**

```
hw.metrics_collected   { cpu_temp_c, ram_used_pct, ram_used_mb, ram_total_mb, disk_used_pct, disk_free_gb }
hw.cpu_temp_warn       { cpu_temp_c, threshold }
hw.cpu_temp_crit       { cpu_temp_c, threshold }
hw.ram_warn            { ram_used_pct, threshold }
hw.ram_crit            { ram_used_pct, threshold } → может запустить деградацию
hw.disk_warn           { disk_used_pct, threshold }
hw.disk_crit           { disk_used_pct, threshold }
```

### widget.html (ICON_SETTINGS)

```
Иконка: термометр (зелёный < 70°, жёлтый < 85°, красный > 85°)
Badge: "62°C · 74% RAM"
```

**Зависимости:**

```
psutil>=5.9              # fallback для /proc/meminfo
```

**Тесты:**

```python
# test: CPU температура читается из /sys/class/thermal (mock)
# test: RAM использование из /proc/meminfo (mock)
# test: hw.cpu_temp_warn при temperature > 70°C
# test: hw.ram_crit при usage > 92%
# test: метрики публикуются каждые 30 секунд
# test: деградация останавливает модули в правильном порядке
```

---

## Модуль 17: `network_scanner`

**Тип:** SYSTEM  
**ui_profile:** FULL  
**Память:** 64 MB  
**CPU:** 0.3  

### Назначение

Сканер сети. Обнаруживает устройства через ARP sweep (Layer 2), mDNS/Bonjour, SSDP/UPnP. Автоклассификация по OUI (производитель по MAC-адресу). Результаты → Device Registry.

### 17.1 ARP Scanner (Layer 2)

```python
# Предпочтительный метод: arp-scan --localnet (активный L2 broadcast)
# Запускается ОДИН РАЗ за цикл сканирования (не per-device)
# Результат кешируется в set для O(1) lookup

# Пассивный режим: чтение /proc/net/arp (без root)
# Активный режим: arping команда (требует cap NET_RAW)
# Ограничение: максимум /24 подсеть (256 адресов)
# Конкурентность: asyncio.Semaphore(20) для arping вызовов

# Время сканирования всей /24: ~1.9 секунды
```

### 17.2 mDNS/Bonjour

```python
# Библиотека: zeroconf (async-safe)
# Мониторимые сервисы:
MDNS_SERVICES = [
    "_http._tcp.local.",         # HTTP-устройства
    "_https._tcp.local.",        # HTTPS-устройства
    "_hap._tcp.local.",          # HomeKit
    "_googlecast._tcp.local.",   # Chromecast
    "_airplay._tcp.local.",      # Apple AirPlay
    "_ipp._tcp.local.",          # Принтеры
    "_smartthings._tcp.local.",  # SmartThings
    "_home-assistant._tcp.local.", # Home Assistant
    "_esphomelib._tcp.local.",   # ESPHome
]
# Данные: имя, тип сервиса, hostname, IP, порт, properties
```

### 17.3 SSDP/UPnP

```python
# Протокол: мультикаст UDP на 239.255.255.250:1900
# Пассивный: слушает NOTIFY и M-SEARCH ответы
# Активный: отправляет M-SEARCH probe (ST: ssdp:all), таймаут 3 секунды
# Данные: USN, LOCATION, SERVER, ST
```

### 17.4 OUI Lookup

```python
# База IEEE OUI: MAC prefix → производитель
# Пример: AA:BB:CC → "Apple, Inc."
# Цель: автоклассификация устройств по типу
```

### 17.5 API модуля

```
GET  /scan/arp              → запустить ARP scan, вернуть результаты
GET  /scan/mdns             → список обнаруженных mDNS сервисов
GET  /scan/ssdp             → список обнаруженных UPnP устройств
POST /scan/full             → полный скан всеми методами
GET  /devices               → все найденные устройства с классификацией
GET  /oui/{mac}             → производитель по MAC-адресу
```

### 17.6 Events

**Публикуемые:**

```
device.discovered          { name, ip, mac, protocol, manufacturer, service_type }
device.offline             { device_id, ip, mac }
device.online              { device_id, ip, mac }
network.scan_complete      { method, found: N, new: N, duration_ms }
```

### widget.html (FULL, размер 2x1)

```
Сеть: 14 устройств · Последний скан: 2 мин назад
Новые: 2 (показать)
Список: IP · MAC · Производитель · Тип
Кнопка "Сканировать сейчас"
```

**Зависимости:**

```
zeroconf>=0.131
arp-scan               # системный пакет, установлен в Dockerfile
arping                 # системный пакет
```

**Тесты:**

```python
# test: ARP scan парсит /proc/net/arp корректно
# test: mDNS обнаруживает _googlecast сервис (mock zeroconf)
# test: SSDP обнаруживает UPnP устройство (mock)
# test: OUI lookup возвращает производителя по MAC
# test: device.discovered событие при новом устройстве
# test: полный скан объединяет результаты всех методов
# test: arp-scan cache — одна операция на цикл
```

---

## Модуль 18: `ui_core`

**Тип:** SYSTEM  
**ui_profile:** (является UI сервером)  
**Память:** 96 MB  
**CPU:** 0.2  

### Назначение

Веб-сервер пользовательского интерфейса. Раздаёт PWA (React SPA) на порту :80. Реверс-прокси к Core API :7070. Onboarding Wizard (9 шагов первого запуска). Автодетект режима дисплея.

### 18.1 FastAPI сервер

```python
# Порт: 80 (UI_PORT)
# Контент: статические файлы PWA из /static/ (собранные через npx vite build)
# Прокси: /api/* → Core API :7070 (CoreApiProxyMiddleware)
# SSE: поддержка стриминга через pure ASGI (не BaseHTTPMiddleware)
```

### 18.2 CoreApiProxyMiddleware

```python
# Реверс-прокси /api/* запросов к Core API :7070
# X-Forwarded-For / X-Real-IP для трекинга клиентов
# SSE поддержка (non-buffered, direct ASGI send)
# Автоматический детект host/scheme
# Реализация: pure ASGI (избегаем BaseHTTPMiddleware для zero-copy)
```

### 18.3 PWA (Progressive Web App)

```python
# Манифест: /manifest.json (имя, иконки, display mode)
# Service Worker: /sw.js (кеширование + offline-страница)
# Иконки: 192x192 и 512x512
# Display mode: standalone (полный экран, без адресной строки)
# Offline: cached shell + "No connection" fallback page
```

### 18.4 Onboarding Wizard (9 шагов)

```python
# Шаги первого запуска (последовательные):
WIZARD_STEPS = [
    "wifi",          # 1. Подключение к Wi-Fi
    "language",      # 2. Выбор языка (en / uk)
    "device_name",   # 3. Имя устройства (hostname)
    "timezone",      # 4. Часовой пояс (TZ database)
    "stt_model",     # 5. Выбор STT модели (Vosk)
    "tts_voice",     # 6. Выбор TTS голоса (Piper)
    "admin_user",    # 7. Создание admin пользователя + PIN
    "platform",      # 8. Регистрация на платформе SmartHome LK
    "import",        # 9. Импорт устройств (HA / Tuya / Hue)
]

# Хранение стейта: /var/lib/selena/wizard_state.json
# Валидация: каждый шаг валидируется перед переходом к следующему

# Эндпоинты:
# GET  /api/ui/wizard/status    → текущий шаг и прогресс
# POST /api/ui/wizard/step      → { step, data } → переход к следующему
```

### 18.5 Автодетект дисплея

```python
# Возможные режимы:
# headless     → нет дисплея (server-only)
# tty          → текстовый терминал (Textual TUI на TTY1)
# kiosk        → Chromium в kiosk-режиме (Wayland cage)
# framebuffer  → прямой вывод на framebuffer
```

### 18.6 AP Mode (первый запуск)

```python
# При отсутствии Wi-Fi — создаётся точка доступа:
# SSID: Selena-<hash>
# Без пароля (открытая)
# Captive portal → redirect на wizard
# QR-код для подключения (генерируется через qrcode)
```

### 18.7 Роутинг

```
/                    → index.html (PWA entrypoint)
/manifest.json       → PWA manifest
/sw.js               → Service Worker
/icons/*             → иконки
/api/*               → реверс-прокси к :7070 (Core API)
/api/ui/wizard/*     → Wizard endpoints
/api/ui/modules/*    → эндпоинты системных модулей
```

### Настройки

```
CORE_API_BASE=http://127.0.0.1:7070
UI_PORT=80
UI_HTTPS=true
STATIC_DIR=/opt/selena-core/system_modules/ui_core/static/
```

**Зависимости:**

```
FastAPI>=0.111
httpx>=0.27
zeroconf>=0.131       # mDNS для onboarding
qrcode>=7.4           # QR для AP mode
```

**Тесты:**

```python
# test: GET / возвращает index.html
# test: /api/* проксируется к :7070 (mock httpx)
# test: wizard status возвращает текущий шаг
# test: wizard step валидирует данные
# test: wizard step advancing сохраняет стейт
# test: SSE стриминг через прокси
# test: AP mode QR-код генерируется
```

---

## Модуль 19: `backup_manager`

**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 96 MB  
**CPU:** 0.3  

### Назначение

Локальный и облачный бэкап. Локальные бэкапы на USB/SD в .tar.gz. Облачные бэкапы с E2E шифрованием (PBKDF2-HMAC-SHA256 + AES-256-GCM). QR-перенос секретов между устройствами.

### 19.1 Локальный бэкап

```python
# Директории: /var/lib/selena/ (registry, history) + /etc/selena/ (config)
# Исключения: /secure/vault_key (НИКОГДА не бэкапится)
# Формат: .tar.gz без шифрования
# Имя: selena_backup_{YYYYMMDDTHHMMSSZ}.tar.gz
# Ретенция: 5 последних (настраивается MAX_LOCAL_BACKUPS)
# Каталог: /var/lib/selena/backups/
# Права: 0o600 (только владелец)
```

### 19.2 Облачный бэкап (E2E)

```python
# Шифрование: PBKDF2-HMAC-SHA256 + AES-256-GCM
# PBKDF2: 600,000 итераций, рандомная 16-байт соль на бэкап
# Nonce: рандомный 12-байт на бэкап (в заголовке)
# Формат файла: salt(16) + nonce(12) + ciphertext

# Загрузка: POST на PLATFORM_BACKUP_URL
# Заголовки:
#   X-Selena-Device: {device_hash}
#   X-Archive-Hash: {SHA256 plaintext}
#   Content-Type: application/octet-stream
```

### 19.3 QR-перенос секретов

```python
# Кодирование секретов в QR-код (сжатые чанки)
# Для переноса между устройствами
# Чтение через камеру нового устройства
```

### 19.4 API модуля

```
POST /api/backup/local/create        → создать локальный бэкап
GET  /api/backup/local/list          → список локальных бэкапов
POST /api/backup/cloud/create        → создать и загрузить облачный
GET  /api/backup/cloud/list          → список облачных бэкапов
POST /api/backup/restore             → восстановить из бэкапа
GET  /api/backup/status              → статус текущей операции
```

### 19.5 Events

**Публикуемые:**

```
backup.created_local   { path, size_mb, sha256 }
backup.created_cloud   { backup_id, size_mb, encrypted: true }
backup.restored        { source, restored_at }
backup.failed          { operation, error }
```

### Настройки (settings.html)

```
Локальный бэкап:
  Каталог: /var/lib/selena/backups
  Максимум копий: 5
  [Создать бэкап сейчас]

Облачный бэкап:
  Пароль шифрования: [input]
  [Создать E2E бэкап]

Восстановление:
  Выбор файла / загрузка
  [Восстановить]

QR-перенос:
  [Сгенерировать QR секретов]
```

**Зависимости:**

```
cryptography>=46.0
httpx>=0.27
qrcode>=7.4
```

**Тесты:**

```python
# test: локальный бэкап создаёт .tar.gz с правильным содержимым
# test: vault_key НЕ включён в бэкап
# test: облачный бэкап шифрует AES-256-GCM
# test: расшифровка возвращает оригинальные данные
# test: PBKDF2 использует 600,000 итераций
# test: ретенция — оставляет только 5 последних
# test: backup.failed при I/O ошибке
```

---

## Модуль 20: `notify_push`

**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 32 MB  
**CPU:** 0.1  

### Назначение

Web Push уведомления (RFC 8292, VAPID). Генерация VAPID ключей, управление подписками браузеров, доставка push-уведомлений. Используется notification_router для канала "push".

### 20.1 VAPID ключи

```python
# Стандарт: RFC 8292 (Voluntary Application Server Identification)
# Библиотека: pywebpush
# Приватный ключ: /secure/vapid_private.pem (генерируется при первом запуске)
# Публичный ключ: экспонируется через API для подписки браузера
# Claims: VAPID_CLAIMS_SUB (e.g., "mailto:admin@selena.local")
```

### 20.2 Управление подписками

```python
# Хранение: /var/lib/selena/push_subscriptions.json
# Модель: PushSubscription { endpoint, keys: { auth, p256dh }, user_id }
# Регистрация: браузер → Service Worker API → POST /subscribe
# Удаление: при unsubscribe или explicit DELETE
```

### 20.3 Доставка

```python
# Payload: JSON { title, body, icon, data }
# Доставка: HTTP POST на endpoint подписки с VAPID-Auth заголовком
# Ретрай: до 3 попыток с backoff
# Обработка ответов:
#   201/204 → успех
#   410     → endpoint истёк → удалить подписку
#   413     → payload слишком большой → отклонить
#   4xx/5xx → retry с backoff
```

### 20.4 API модуля

```
GET    /api/push/vapid-public-key      → публичный ключ для подписки
POST   /api/push/subscribe             → зарегистрировать подписку
GET    /api/push/subscriptions         → список подписок (admin)
DELETE /api/push/subscriptions/{id}    → удалить подписку
POST   /api/push/test/{user_id}       → отправить тестовое уведомление
```

### 20.5 Events

**Публикуемые:**

```
notification.sent        { user_id, title }
notification.failed      { user_id, error }
notification.subscribed  { user_id, endpoint }
```

**Слушает:**

```
push.send               { title, body, icon, data, user_id? }
```

**Зависимости:**

```
pywebpush>=2.0
py-vapid>=1.9
```

**Тесты:**

```python
# test: VAPID ключи генерируются при первом запуске
# test: subscribe сохраняет подписку в JSON
# test: push доставляется через pywebpush (mock)
# test: 410 → подписка удалена
# test: retry при network failure
# test: test endpoint отправляет тестовое уведомление
```

---

## Модуль 21: `remote_access`

**Тип:** SYSTEM  
**ui_profile:** SETTINGS_ONLY  
**Память:** 32 MB  
**CPU:** 0.15  

### Назначение

Безопасный удалённый доступ через Tailscale VPN. Подключение к WireGuard-mesh сети без открытия портов и port forwarding. Управление через настройки UI.

### 21.1 Tailscale интеграция

```python
# Предварительные условия: tailscaled (демон) установлен на хосте
# Авторизация:
# 1. Сгенерировать auth key в Tailscale admin console
# 2. Установить TAILSCALE_AUTH_KEY в env
# 3. connect() → устройство подключается к mesh-сети
# 4. Доступ через Tailscale IP из любой точки мира

async def get_status() -> TailscaleStatus:
    # tailscale status --json
    # Возвращает: connected, tailscale_ip, hostname, version

async def connect(auth_key: str | None = None) -> bool:
    # tailscale up --auth-key {key} --accept-routes

async def disconnect() -> bool:
    # tailscale logout
```

### 21.2 API модуля

```
GET  /api/remote/status         → статус подключения Tailscale
POST /api/remote/connect        → подключиться (auth_key в body)
POST /api/remote/disconnect     → отключиться
```

### 21.3 Events

**Публикуемые:**

```
remote.connected       { tailscale_ip, hostname }
remote.disconnected    { reason }
```

### Настройки (settings.html)

```
Статус: ● Подключён / ○ Отключён
Tailscale IP: 100.64.x.x
Auth Key: [input, masked]
[Подключить] / [Отключить]
Ссылка: "Получить Auth Key в admin.tailscale.com"
```

**Зависимости:**

```
tailscale              # системный пакет на хосте
```

**Тесты:**

```python
# test: get_status парсит tailscale status --json (mock subprocess)
# test: connect вызывает tailscale up с auth key
# test: disconnect вызывает tailscale logout
# test: remote.connected событие при успешном подключении
```

---

## Полная таблица системных модулей

| # | Модуль | Тип | ui_profile | Память | CPU | Описание |
|---|--------|-----|------------|--------|-----|----------|
| 1 | scheduler | SYSTEM | SETTINGS_ONLY | 64 MB | 0.15 | Планировщик: cron, interval, sunrise/sunset |
| 2 | device_watchdog | SYSTEM | ICON_SETTINGS | 64 MB | 0.1 | Мониторинг доступности устройств |
| 3 | protocol_bridge | SYSTEM | FULL | 256 MB | 0.3 | MQTT / Zigbee / Z-Wave / HTTP шлюз |
| 4 | automation_engine | SYSTEM | FULL | 128 MB | 0.3 | Движок автоматизаций (если X → то Y) |
| 5 | presence_detection | SYSTEM | FULL | 64 MB | 0.15 | ARP/BT/GPS определение присутствия |
| 6 | weather_service | SYSTEM | FULL | 64 MB | 0.1 | Погода (open-meteo, без API ключа) |
| 7 | energy_monitor | SYSTEM | FULL | 64 MB | 0.1 | Мониторинг энергопотребления |
| 8 | notification_router | SYSTEM | SETTINGS_ONLY | 64 MB | 0.1 | Маршрутизатор уведомлений |
| 9 | update_manager | SYSTEM | FULL | 64 MB | 0.1 | OTA-обновления с SHA256 верификацией |
| 10 | import_adapters | SYSTEM | SETTINGS_ONLY | 128 MB | 0.2 | Импорт из HA / Tuya / Hue |
| 11 | media_player | SYSTEM | FULL | 128 MB | 0.5 | Медиаплеер: радио, USB, SMB |
| 12 | voice_core | SYSTEM | FULL | 256 MB | 0.5 | STT / TTS / Wake-word / Speaker ID |
| 13 | llm_engine | SYSTEM | SETTINGS_ONLY | 512+ MB | 1.0+ | Fast Matcher + Ollama LLM |
| 14 | secrets_vault | SYSTEM | SETTINGS_ONLY | 64 MB | 0.1 | AES-256-GCM хранилище + OAuth + прокси |
| 15 | user_manager | SYSTEM | FULL | 128 MB | 0.2 | Профили / PIN / Face ID / Voice ID |
| 16 | hw_monitor | SYSTEM | ICON_SETTINGS | 32 MB | 0.05 | CPU / RAM / Disk мониторинг |
| 17 | network_scanner | SYSTEM | FULL | 64 MB | 0.3 | ARP / mDNS / SSDP сканер |
| 18 | ui_core | SYSTEM | — | 96 MB | 0.2 | PWA сервер :80 + Wizard + прокси |
| 19 | backup_manager | SYSTEM | SETTINGS_ONLY | 96 MB | 0.3 | Локальный + E2E облачный бэкап |
| 20 | notify_push | SYSTEM | SETTINGS_ONLY | 32 MB | 0.1 | Web Push VAPID уведомления |
| 21 | remote_access | SYSTEM | SETTINGS_ONLY | 32 MB | 0.15 | Tailscale VPN удалённый доступ |

**Общее потребление RAM (все 21 модуль):** ~1.8 GB (без LLM модели) / ~4 GB (с LLM phi3:mini)

---

## Связанные документы

```
docs/architecture.md              ← компоненты ядра
docs/module-core-protocol.md      ← токены, HMAC, lifecycle
docs/module-development.md        ← SDK, manifest, permissions
docs/deployment.md                ← Raspberry Pi деплой
CONTRIBUTING.md                   ← стандарты кода
```
