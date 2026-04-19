# Spec: system_modules/ — SelenaCore System Modules
**Executor:** AI coding agent
**Priority:** High
**Branch:** `feat/<N>-system-modules`
**Depends on:** core/ fully implemented and running (Core API :80, Event Bus, Device Registry, Module Loader)

---

## Required reading before starting

```
AGENTS.md (SelenaCore)              <- agent rules, git workflow
docs/architecture.md                <- core architecture
docs/module-bus-protocol.md         <- module<>core protocol, tokens, HMAC
docs/module-development.md          <- SDK, manifest.json, permissions
README.md                           <- project structure, env vars
```

---

## Critical rules (violation = broken code)

```
NO print() — only logging.getLogger(__name__)
NO bare except: pass — always except Exception as e:
NO missing type hints on public methods
NO synchronous def instead of async def in public methods
NO eval(), exec() in any code
NO shell=True without absolute necessity
NO direct reading of /secure/ from any system module
NO publishing core.* events from modules (only from the core)
NO storing secrets in .env (only templates in .env.example)
NO one file = multiple responsibilities
```

---

## General requirements for all system modules

### Structure of each module

```
system_modules/<name>/
  manifest.json          <- required
  __init__.py            <- exports module_class
  module.py              <- entry point, SystemModule subclass
  <name>.py              <- business logic (separate file)
  widget.html            <- UI widget (if ui_profile != HEADLESS)
  settings.html          <- settings page (if applicable)
  icon.svg               <- icon for UI
  tests/
    test_<name>.py       <- pytest tests
  README.md              <- module description
```

### module.py template

```python
# system_modules/<name>/module.py
import logging
from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)

class <Name>Module(SystemModule):
    name = "<name>"

    async def start(self) -> None:
        # Subscribe to events, initialize service
        await self.publish("module.started", {"name": self.name})
        logger.info("%s started", self.name)

    async def stop(self) -> None:
        self._cleanup_subscriptions()
        logger.info("%s stopped", self.name)

    def get_router(self):
        # Optional: return FastAPI APIRouter for UI endpoints
        return None
```

### `__init__.py`

```python
from .module import <Name>Module as module_class
```

### manifest.json — required fields

```json
{
  "name": "<slug>",
  "version": "0.1.0",
  "description": "...",
  "type": "SYSTEM",
  "ui_profile": "FULL | HEADLESS | SETTINGS_ONLY | ICON_SETTINGS",
  "api_version": "1.0",
  "runtime_mode": "always_on",
  "permissions": [...],
  "ui": {
    "icon": "icon.svg",
    "widget": { "file": "widget.html", "size": "NxM" },
    "settings": "settings.html"
  },
  "resources": { "memory_mb": <N>, "cpu": <0.N> }
}
```

---

## Implementation order

Commit after each step. Each commit = a working module.

```
Step 1:  scheduler           <- all others depend on it
Step 2:  device_watchdog     <- needed for automation_engine
Step 3:  protocol_bridge     <- needed for real devices
Step 4:  automation_engine   <- key module
Step 5:  presence_detection  <- used in automation_engine
Step 6:  weather_service     <- used in automation_engine
Step 7:  energy_monitor
Step 8:  notification_router <- used in automation_engine
Step 9:  update_manager
Step 10: device_control      <- smart device manager (Tuya cloud/local), owns device.on/off
Step 9.5: media_player      <- depends on scheduler (sleep timer), voice_core (TTS)
Step 11: pytest for all modules
```

---

## Module 1: `scheduler`

**Type:** SYSTEM
**ui_profile:** SETTINGS_ONLY
**Memory:** 64 MB

### Purpose

Central scheduler for all of SelenaCore. All modules that need to "run at time X" communicate with it through EventBus events (EventBus.subscribe, EventBus.publish). Supports cron, interval, and astronomical triggers (sunrise/sunset).

### Functionality

**Trigger types:**

```python
# Cron expression (standard syntax)
"cron:0 7 * * 1-5"        # weekdays at 07:00

# Interval
"every:5m"                 # every 5 minutes
"every:1h"                 # every hour
"every:30s"                # every 30 seconds

# Astronomical events (require coordinates from settings)
"sunrise"                  # at sunrise
"sunset"                   # at sunset
"sunrise+30m"              # 30 minutes after sunrise
"sunset-1h"                # 1 hour before sunset
```

**Sunrise/sunset computation:**

```python
# Library: astral (pip install astral)
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

**API for task registration (other modules call via EventBus.publish):**

Scheduler listens to the `scheduler.register` event:
```json
{
  "job_id":     "automation:morning-lights",
  "trigger":    "sunrise+30m",
  "event_type": "automation.trigger",
  "payload":    { "automation_id": "morning-lights" },
  "owner":      "automation-engine"
}
```

When the trigger fires — publishes the event from `payload.event_type` with `payload.payload`.

Scheduler listens to the `scheduler.unregister` event:
```json
{ "job_id": "automation:morning-lights" }
```

**Task persistence:**

```python
# Tasks are stored in SQLite via DeviceRegistry / module config
# On restart — all tasks are reloaded from config
# Astral recalculates sunrise/sunset every day automatically
```

**Published events:**

```
scheduler.fired          { job_id, trigger, fired_at }
scheduler.job_registered { job_id, trigger, next_run }
scheduler.job_removed    { job_id }
```

**Listened events:**

```
scheduler.register
scheduler.unregister
scheduler.list_jobs      -> publishes scheduler.jobs_list in response
```

**widget.html (SETTINGS_ONLY — settings only):**

```
Settings:
  Latitude  (float, -90..90)
  Longitude (float, -180..180)
  Timezone (select from pytz)

Active jobs list:
  job_id | trigger | owner | next run
```

**Dependencies:**

```
astral>=3.2
apscheduler>=3.10
```

**REST endpoints (mounted at `/api/ui/modules/scheduler/` via `get_router()`):**

```
GET  /jobs                → list active jobs
POST /jobs                -> register job
DELETE /jobs/{job_id}     -> unregister job
GET  /health              -> {"status": "ok"}
```

**Tests (tests/test_scheduler.py):**

```python
# test: cron trigger fires at correct time (mock time)
# test: interval trigger fires N times in period (mock)
# test: sunrise/sunset computed correctly for known location
# test: job persists across restart (save -> reload)
# test: scheduler.register event -> job created
# test: scheduler.unregister event -> job removed
```

---

## Module 2: `device_watchdog`

**Type:** SYSTEM
**ui_profile:** ICON_SETTINGS
**Memory:** 64 MB

### Purpose

Monitors the availability of all devices in the Device Registry. Periodically checks their availability (ping by IP, protocol-specific heartbeat), updates online/offline status, publishes events on state changes.

### Functionality

**Check algorithm:**

```python
# Every 60 seconds (configurable):
async def check_all_devices():
    devices = await self.list_devices()   # DeviceRegistry.list_devices() in-process
    for device in devices:
        was_online = device["meta"].get("watchdog_online", True)
        is_online  = await self._ping(device)

        if was_online != is_online:
            # Status changed — update and notify
            await self.update_device_state(
                device["id"],
                {"watchdog_online": is_online,
                 "watchdog_last_seen": datetime.utcnow().isoformat()}
            )
            event = "device.online" if is_online else "device.offline"
            await self.publish("event", {
                "device_id":   device["id"],
                "device_name": device["name"],
                "protocol":    device["protocol"],
                "ip":          device["meta"].get("ip_address")
            })
```

**Check methods by protocol:**

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
            # Check last_seen from MQTT broker (via protocol_bridge)
            # Offline if last_seen > threshold
            last_seen = meta.get("mqtt_last_seen")
            if not last_seen:
                return False
            delta = (datetime.utcnow() - datetime.fromisoformat(last_seen)).seconds
            return delta < int(self._config.get("mqtt_timeout_sec", 120))

        case "zigbee" | "zwave":
            # Via protocol_bridge event device.protocol_heartbeat
            last_seen = meta.get("protocol_last_seen")
            if not last_seen:
                return True  # unknown — assume online
            delta = (datetime.utcnow() - datetime.fromisoformat(last_seen)).seconds
            return delta < int(self._config.get("protocol_timeout_sec", 300))

        case _:
            return True   # unknown protocol — do not check
```

**ICMP ping without root:**

```python
# Use icmplib (works without root via unprivileged ICMP)
from icmplib import async_ping

async def _icmp_ping(self, host: str, timeout: float) -> bool:
    try:
        result = await async_ping(host, count=1, timeout=timeout, privileged=False)
        return result.is_alive
    except Exception:
        return False
```

**Settings:**

```
check_interval_sec:      60       # how often to check all devices
ping_timeout_sec:        2        # timeout for a single ping
mqtt_timeout_sec:        120      # seconds before considering MQTT offline
protocol_timeout_sec:    300      # Zigbee/Z-Wave timeout
offline_threshold:       3        # how many consecutive failures before offline
notify_on_offline:       true     # publish device.offline event
```

**Published events:**

```
device.online        { device_id, device_name, protocol }
device.offline       { device_id, device_name, protocol, offline_since }
device.watchdog_scan { checked: N, online: N, offline: N, duration_ms: N }
```

**Listened events:**

```
device.protocol_heartbeat   { device_id, timestamp }  <- from protocol_bridge
```

**REST endpoints (mounted at `/api/ui/modules/device_watchdog/` via `get_router()`):**

```
GET  /status              -> device online/offline summary
POST /scan                -> trigger manual scan
GET  /health              -> {"status": "ok"}
```

**widget.html (ICON_SETTINGS):**

```
Icon: pulse indicator (green if all online, red if any offline)
Badge: "12/14 online"

Settings page:
  Timeout settings
  Device list with last check time
  "Check Now" button
```

**Dependencies:**

```
icmplib>=3.0
```

**Tests:**

```python
# test: device goes offline after N failed pings
# test: device.offline event published on status change
# test: device.online event published on recovery
# test: mqtt_last_seen timeout detection
# test: watchdog_scan event contains correct counts
```

---

## Module 3: `protocol_bridge`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 256 MB

### Purpose

Gateway between physical smart home protocols (MQTT, Zigbee, Z-Wave) and the core's Device Registry. Devices on these protocols are registered in the Registry and controlled through the standard EventBus and DeviceRegistry methods. Other modules are unaware of protocols — they only work with abstract devices. This module runs in-process alongside the core.

### 3.1 MQTT

**MQTT broker connection:**

```python
# Connect to an external MQTT broker (e.g. Mosquitto running on the host)
# or a locally installed broker (configurable)

MQTT_BROKER_HOST = config.get("mqtt_host", "localhost")
MQTT_BROKER_PORT = int(config.get("mqtt_port", 1883))
```

**Auto-discovery via MQTT (Home Assistant standard):**

```python
# Listen: homeassistant/+/+/config
# On receiving a config message — register device in Registry

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

**Controlling a device via MQTT:**

```python
# When device state is changed via DeviceRegistry
# Core publishes device.state_changed via EventBus
# protocol_bridge intercepts (EventBus.subscribe) and sends an MQTT command

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

**Via zigbee2mqtt (separate process on the host):**

```python
# zigbee2mqtt runs as a systemd service or separate process on the host
# protocol_bridge connects to it via MQTT

# zigbee2mqtt publishes:
#   zigbee2mqtt/<friendly_name>        -> state messages
#   zigbee2mqtt/<friendly_name>/set   <- commands

# protocol_bridge:
# 1. Subscribes to zigbee2mqtt/bridge/devices -> device list
# 2. Registers each in the Device Registry with protocol="zigbee"
# 3. Translates state changes <> EventBus
```

**Supported Zigbee adapters:**

```
SONOFF Zigbee 3.0 USB Dongle Plus (recommended)
Conbee II
Tube's Zigbee Coordinator
Texas Instruments CC2652R/CC2652P
```

### 3.3 Z-Wave

**Via zwave-js-ui (optional, if USB adapter is available):**

```python
# Similar to Zigbee — through an intermediary service on the host
# Configured if Z_WAVE_ENABLED=true in module settings
# Disabled by default (not all users have a USB adapter)
```

### 3.4 Direct HTTP/REST (WiFi devices)

```python
# For devices with REST API (Shelly, Sonoff DIY, etc.)
# Polling every N seconds

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

**Published:**

```
device.protocol_heartbeat    { device_id, protocol, timestamp }
device.protocol_discovered   { name, protocol, meta }  <- new device found
device.protocol_lost         { device_id, protocol }
protocol_bridge.mqtt_connected    { host, port }
protocol_bridge.mqtt_disconnected { reason }
protocol_bridge.zigbee_devices    { count }
```

**Listens to:**

```
device.state_changed    -> send command to physical device
```

### Settings (settings.html)

```
MQTT:
  Enabled: toggle
  Host: localhost
  Port: 1883
  Username/Password (optional)

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

**REST endpoints (mounted at `/api/ui/modules/protocol_bridge/` via `get_router()`):**

```
GET  /protocols           -> status of all protocols (MQTT, Zigbee, Z-Wave)
GET  /devices             -> devices discovered via protocols
POST /mqtt/test           -> test MQTT connection
GET  /health              -> {"status": "ok"}
```

**widget.html (FULL, size 2x1):**

```
Left half:
  MQTT: * Connected / o Offline
  Zigbee: N devices
  Z-Wave: N devices / Disabled

Right half:
  Recent protocol events (5 lines)
```

**Dependencies:**

```
aiomqtt>=1.2
httpx>=0.27
# zigbee2mqtt and zwave-js-ui run as host services, not pip packages
```

**System dependencies:**

```
# For Zigbee USB adapter — ensure /dev/ttyUSB0 is accessible to the process
# zigbee2mqtt: install and run as a systemd service on the host
# zwave-js-ui: install and run as a systemd service on the host (optional)
```

**Tests:**

```python
# test: MQTT discovery message -> device registered in Registry (mock)
# test: device.state_changed -> MQTT command published
# test: device.protocol_heartbeat published on MQTT message
# test: HTTP poll -> device state updated
# test: MQTT disconnect -> reconnect after timeout
```

---

## Module 4: `automation_engine`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 128 MB

### Purpose

Automation engine. The user describes rules "if X -> then Y". The engine subscribes to EventBus events, checks conditions, and executes actions via direct Python calls (EventBus.publish, DeviceRegistry methods). This is the key module — without it, the smart home requires manual control.

### 4.1 Automation format (YAML)

```yaml
# Example automation file
id: morning-lights
name: "Morning lighting"
enabled: true

trigger:
  - type: time
    at: "sunrise+30m"           # via scheduler
  - type: event
    event_type: "presence.home" # someone arrived home

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
    delay_ms: 500               # with 500ms delay

  - type: event
    event_type: "notification.send"
    payload:
      message: "Good morning! Lights are on."
      channel: "tts"

  - type: scene
    scene_id: "morning"
```

### 4.2 Trigger types

```yaml
# Time (via scheduler)
trigger:
  type: time
  at: "07:00" | "sunrise" | "sunset+30m" | "every:5m" | "cron:0 8 * * 1-5"

# EventBus event
trigger:
  type: event
  event_type: "device.state_changed"
  filter:                         # optional payload filters
    device_id: "dev_door_sensor"
    new_state.contact: false      # dot-notation for nested fields

# Device state change
trigger:
  type: device_state
  device_id: "dev_motion_sensor"
  attribute: "motion"
  to: true                        # fire when motion becomes true
  from: false                     # optional: from which value

# Presence (from presence_detection)
trigger:
  type: presence
  action: "home" | "away"        # someone arrived / left
  user_id: "user_alice"          # optional: specific user
```

### 4.3 Condition types

```yaml
# Time range
condition:
  type: time_range
  from: "22:00"
  to:   "07:00"              # supports crossing midnight

# Device state
condition:
  type: state
  device_id: "dev_abc"
  attribute: "temperature"
  operator: ">" | "<" | ">=" | "<=" | "==" | "!="
  value: 25.0

# Presence
condition:
  type: presence
  state: "home" | "away"    # someone home / nobody home
  user_id: "user_alice"     # optional

# Weather (from weather_service)
condition:
  type: weather
  attribute: "condition"
  operator: "=="
  value: "rain"

# Time of day
condition:
  type: sun
  state: "above_horizon" | "below_horizon"

# Logical operators
condition:
  type: and | or | not
  conditions: [...]
```

### 4.4 Action types

```yaml
# Change device state (via DeviceRegistry.update_device_state)
action:
  type: device_state
  device_id: "dev_abc"
  state: { power: true, brightness: 80 }
  delay_ms: 0               # delay before execution

# Publish event (via EventBus.publish)
action:
  type: event
  event_type: "any.event"
  payload: {}

# Activate scene
action:
  type: scene
  scene_id: "evening"

# Send notification
action:
  type: notify
  message: "Notification text"
  channel: "tts" | "push" | "telegram" | "all"

# Pause between actions
action:
  type: delay
  ms: 1000

# Conditional action
action:
  type: if
  condition: { type: state, ... }
  then: [...]
  else: [...]
```

### 4.5 Scenes

```yaml
# scenes/<id>.yaml
id: evening
name: "Evening"
actions:
  - type: device_state
    device_id: "dev_living_light"
    state: { power: true, brightness: 40, color_temp: 3000 }
  - type: device_state
    device_id: "dev_tv_backlight"
    state: { power: true, brightness: 30 }
```

### 4.6 Storage

```python
# Automations are stored in:
# /var/lib/selena/modules/automation-engine/automations/<id>.yaml
# Scenes:
# /var/lib/selena/modules/automation-engine/scenes/<id>.yaml

# On start — load all files
# On change — save file + reload
# Watchdog on directory (watchfiles) — hot reload without restart
```

### 4.7 Trigger registration on start

```python
async def on_start(self):
    automations = self._load_all_automations()
    for automation in automations:
        await self._register_triggers(automation)

async def _register_triggers(self, automation: Automation):
    for trigger in automation.triggers:
        if trigger.type == "time":
            # Register task in scheduler via EventBus.publish
            await self.publish("scheduler.register", {
                "job_id":     f"automation:{automation.id}:{trigger.at}",
                "trigger":    trigger.at,
                "event_type": "automation.time_trigger",
                "payload":    { "automation_id": automation.id },
                "owner":      "automation-engine"
            })
        else:
            # Event-based triggers — subscribe via EventBus.subscribe
            pass
```

### 4.8 Events

**Published:**

```
automation.triggered     { automation_id, trigger_type, timestamp }
automation.executed      { automation_id, actions_count, duration_ms }
automation.failed        { automation_id, error }
automation.created       { automation_id }
automation.updated       { automation_id }
automation.deleted       { automation_id }
scene.activated          { scene_id, scene_name }
```

**Listens to:**

```
device.state_changed
device.online
device.offline
presence.home
presence.away
weather.updated
automation.time_trigger    <- from scheduler
```

### 4.9 Module API endpoints

REST endpoints mounted at `/api/ui/modules/automation_engine/` via `get_router()`:

```
GET  /automations              -> list all automations
POST /automations              -> create (body: YAML text or JSON)
GET  /automations/{id}         -> single automation
PUT  /automations/{id}         -> update
DELETE /automations/{id}       -> delete
PATCH /automations/{id}/toggle -> enable/disable

GET  /scenes                   -> list scenes
POST /scenes                   -> create scene
PUT  /scenes/{id}              -> update
DELETE /scenes/{id}            -> delete
POST /scenes/{id}/activate     -> activate immediately

GET  /history?limit=50         -> trigger history
GET  /health                   -> {"status": "ok"}
```

### widget.html (FULL, size 2x2)

```
Top half:
  List of active automations (toggle enable/disable)
  Counter: "7 automations * 12 triggers today"

Bottom half:
  Last 5 triggers with time and status
  "Open Editor" button
```

**settings.html — automation editor:**

```
Automation list with edit/delete/toggle buttons
Editor: YAML textarea with syntax highlighting (CodeMirror)
"Test" button — run automation manually
"Scenes" tab
"History" tab
```

**Dependencies:**

```
watchfiles>=0.21
pyyaml>=6.0
jsonpath-ng>=1.6       # for dot-notation filters in triggers
```

**Tests:**

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

## Module 5: `presence_detection`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 64 MB

### Purpose

Determines which users are currently at home. Uses multiple methods in parallel: ARP ping of phone MAC addresses, Bluetooth beacon, GPS geofencing (via mobile app). Publishes events on arrival/departure via EventBus.publish.

### 5.1 Detection methods

**WiFi / ARP (primary, works without an app):**

```python
# For each tracked device (phone MAC address):
# ARP ping every 30 seconds
# If MAC responds -> user is home

import subprocess

async def _arp_check(self, mac: str) -> bool:
    # arping requires root or cap NET_RAW
    # Alternative: parse /proc/net/arp (no root required)
    try:
        with open("/proc/net/arp") as f:
            arp_table = f.read()
        # Find MAC in table (normalize format)
        mac_normalized = mac.lower().replace("-", ":")
        return mac_normalized in arp_table.lower()
    except Exception as e:
        logger.warning(f"ARP check failed: {e}")
        return False
```

**Bluetooth (optional, if BT adapter is available):**

```python
# Scan BLE advertisements
# If device with known UUID/MAC is visible -> user is home

import asyncio
from bleak import BleakScanner

async def _bluetooth_scan(self) -> set[str]:
    """Returns set of MAC addresses of visible BT devices."""
    try:
        devices = await BleakScanner.discover(timeout=5.0)
        return {d.address.lower() for d in devices}
    except Exception as e:
        logger.warning(f"BT scan failed: {e}")
        return set()
```

**GPS geofencing (via webhook from mobile app):**

```python
# Mobile app sends POST when entering/leaving a zone.
# Endpoint mounted at /api/ui/modules/presence_detection/webhook/location via get_router():
async def location_webhook(request: Request):
    body = await request.json()
    # body: { user_id, event: "enter"|"leave", zone: "home" }
    user_id = body["user_id"]
    is_home = body["event"] == "enter" and body["zone"] == "home"
    await self._update_presence(user_id, is_home, method="gps")
```

### 5.2 Presence determination logic

```python
# User is considered "home" if AT LEAST ONE method says yes
# Grace period: 5 minutes after last seen before declaring "away"
# Prevents flapping: do not publish "away" if "home" again within 30s

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
        await self.publish(event, {
            "user_id":   user_id,
            "user_name": user.name,
            "method":    method,
            "timestamp": datetime.utcnow().isoformat()
        })
        # Also update global status "anyone home"
        anyone_home = any(u.is_home for u in self._users.values())
        await self.publish("presence.anyone_home" if anyone_home
                                 else "presence.everyone_away", {})
```

### 5.3 User settings

```python
# Configuration via settings.html -> module config

{
  "users": [
    {
      "user_id":   "user_alice",
      "name":      "Alice",
      "wifi_mac":  "AA:BB:CC:DD:EE:FF",    # phone MAC
      "bt_mac":    "11:22:33:44:55:66",    # optional
      "gps_token": "abc123"                # optional
    }
  ],
  "grace_period_sec":    300,    # 5 minutes
  "wifi_check_interval": 30,     # seconds
  "bt_scan_enabled":     false,
  "gps_enabled":         false
}
```

### 5.4 Events

**Published:**

```
presence.home              { user_id, user_name, method, timestamp }
presence.away              { user_id, user_name, method, timestamp }
presence.anyone_home       { users_home: [user_id, ...] }
presence.everyone_away     {}
presence.status            { users: [{user_id, name, is_home, last_seen}] }
```

**Listens to:**

```
presence.request_status    -> publishes presence.status
```

### REST endpoints (mounted at `/api/ui/modules/presence_detection/` via `get_router()`)

```
GET  /status               -> current presence for all users
POST /webhook/location     -> GPS geofence webhook
GET  /health               -> {"status": "ok"}
```

### widget.html (FULL, size 1x2)

```
For each user:
  Avatar (initials) + name
  * Home (green) / o Away (gray)
  Last visit: "14:32"
  Method: wifi / bt / gps

Bottom:
  "2 of 3 home"
```

**Dependencies:**

```
bleak>=0.21          # Bluetooth (optional)
```

**Tests:**

```python
# test: ARP check returns True when MAC in /proc/net/arp
# test: grace_period prevents immediate away after not seen
# test: presence.home event on transition away->home
# test: presence.away event on transition home->away (after grace period)
# test: anyone_home/everyone_away published correctly
# test: multiple methods: any=True -> home
# test: GPS webhook updates presence
```

---

## Module 6: `weather_service`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 64 MB

### Purpose

Retrieves weather data from the open-meteo API (free, no API key required, works offline in the sense of no registration needed). Caches locally. Provides data to other modules via EventBus events and REST API.

### 6.1 Data source

```python
# open-meteo.com — free, no key, GDPR-compliant

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

**WMO weather codes -> human-readable:**

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

### 6.2 Data updates

```python
# Update every 30 minutes (not more often — open-meteo updates once per hour)
# In-memory cache + save to /config for recovery after restart

async def _fetch_weather(self):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(BASE_URL, params=PARAMS)
            resp.raise_for_status()
            raw = resp.json()

        self._cache = self._parse(raw)
        self._last_updated = datetime.utcnow()

        await self.publish("weather.updated", self._cache["current"])
        logger.info(f"Weather updated: {self._cache['current']['condition']}")

    except Exception as e:
        logger.error(f"Weather fetch failed: {e}")
        # Continue operating with cached data
```

### 6.3 Data format

```python
# weather.updated payload and GET /weather response:
{
  "current": {
    "temperature":  22.4,          # C
    "humidity":     58,            # %
    "precipitation": 0.0,          # mm
    "condition":    "partly_cloudy",
    "wind_speed":   3.2,           # m/s
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
  "forecast": [           # 3 days
    { "date": "2026-03-22", "temp_min": 12, "temp_max": 20, "condition": "rain" },
    { "date": "2026-03-23", "temp_min": 10, "temp_max": 18, "condition": "rain_light" }
  ],
  "hourly": [             # 24 hours
    { "time": "15:00", "temperature": 23.1, "precipitation_probability": 5 }
  ]
}
```

### 6.4 Module API

REST endpoints mounted at `/api/ui/modules/weather_service/` via `get_router()`:

```
GET /weather              -> current data (from cache)
GET /weather/forecast     -> 3-day forecast
GET /weather/hourly       -> hourly forecast
POST /weather/refresh     -> force update
GET /health               -> {"status": "ok"}
```

### 6.5 Events

**Published:**

```
weather.updated            { current: { temperature, humidity, condition, ... } }
weather.alert              { type: "heavy_rain"|"frost"|..., message }
```

**Alerts:**

```python
# After each update, check thresholds:
ALERTS = [
    ("frost",       lambda w: w["temperature"] < 2),
    ("heat",        lambda w: w["temperature"] > 35),
    ("heavy_rain",  lambda w: w["condition"] in ("rain_heavy", "showers_heavy")),
    ("strong_wind", lambda w: w["wind_speed"] > 15),
    ("thunderstorm",lambda w: "thunderstorm" in w["condition"]),
]
```

### widget.html (FULL, size 2x1)

```
Weather icon (SVG, depends on condition)
Temperature: 22 C
Humidity: 58% * Wind: 3.2 m/s
"Partly cloudy"
Mini forecast: 3 day icons with min/max temperatures
```

**Dependencies:**

```
httpx>=0.27
```

**Tests:**

```python
# test: fetch returns correct structure (mock httpx)
# test: WMO code mapped to condition string
# test: cache returned when API unavailable
# test: weather.updated event published after fetch
# test: frost alert when temperature < 2 C
# test: no duplicate alerts in same hour
```

---

## Module 7: `energy_monitor`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 64 MB

### Purpose

Aggregates electricity consumption data from all smart plugs and devices with power monitoring support. Builds statistics, detects anomalies, shows costs.

### 7.1 Data collection

```python
# Listens to device.state_changed events via EventBus.subscribe
# If device has power_w, energy_kwh attributes — records them

async def on_state_changed(self, payload: dict):
    new_state = payload.get("new_state", {})

    power_w = new_state.get("power_w")         # current power W
    energy_kwh = new_state.get("energy_kwh")   # accumulated energy kWh

    if power_w is None and energy_kwh is None:
        return   # device does not support power monitoring

    device_id = payload["device_id"]
    await self._record(device_id, power_w, energy_kwh)
```

### 7.2 Data storage

```python
# Time series in SQLite (module's own DB, not the core's)
# /var/lib/selena/modules/energy-monitor/energy.db

CREATE TABLE readings (
    device_id   TEXT NOT NULL,
    timestamp   DATETIME NOT NULL,
    power_w     REAL,           -- instantaneous power
    energy_kwh  REAL,           -- accumulated energy
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

# Rotation: keep raw data for 7 days, daily summary — 1 year
```

### 7.3 Aggregation and anomalies

```python
# Total consumption calculation:
async def get_total_power_now(self) -> float:
    """Sum of current power across all devices (W)."""
    ...

# Anomaly: device consuming longer than usual
async def check_anomalies(self):
    for device_id, stats in self._device_stats.items():
        if stats.consecutive_on_minutes > stats.avg_on_minutes * 2:
            await self.publish("energy.anomaly", {
                "device_id":       device_id,
                "type":            "unusually_long_on",
                "duration_minutes": stats.consecutive_on_minutes,
                "normal_minutes":  stats.avg_on_minutes,
                "message":         f"Device has been running for {stats.consecutive_on_minutes} min (usually {stats.avg_on_minutes})"
            })
```

### 7.4 Module API

REST endpoints mounted at `/api/ui/modules/energy_monitor/` via `get_router()`:

```
GET /energy/now              -> current whole-house power (W)
GET /energy/today            -> today's consumption (kWh, cost)
GET /energy/devices          -> consumption by device
GET /energy/history?days=7   -> history by day
GET /energy/forecast         -> monthly forecast (based on history)
GET /health                  -> {"status": "ok"}
```

### 7.5 Events

**Published:**

```
energy.total_power     { watts: 1840.5, timestamp }   <- every 60 sec
energy.anomaly         { device_id, type, message }
energy.daily_summary   { date, kwh_total, cost, by_device: [...] }
```

**Listens to:**

```
device.state_changed   -> record readings if power_w/energy_kwh present
```

### Settings (settings.html)

```
Electricity tariff (UAH/kWh or USD/kWh)
Display currency
Anomaly threshold (multiplier from average)
Data retention period (days)
```

### widget.html (FULL, size 2x1)

```
Large number: "1840 W" (now)
Today: 14.2 kWh * ~$1.84
Mini chart: consumption over 24 hours (SVG sparkline)
Top 3 consumers right now
```

**Dependencies:**

```
aiosqlite>=0.19
```

**Tests:**

```python
# test: reading recorded on device.state_changed with power_w
# test: total power aggregated correctly across devices
# test: anomaly detected when duration > 2x average
# test: daily_summary computed correctly
# test: old readings rotated after 7 days
# test: energy.anomaly event published with correct payload
```

---

## Module 8: `notification_router`

**Type:** SYSTEM
**ui_profile:** SETTINGS_ONLY
**Memory:** 64 MB

### Purpose

Notification router. Other modules publish a `notification.send` event via EventBus — the router decides where to deliver it: TTS voice, Telegram, Web Push, or all at once. The user configures routing rules.

### 8.1 Sending a notification (input interface)

```python
# Any module can publish via EventBus.publish:
await self.publish("notification.send", {
    "message":   "Motion detected at the front door",
    "title":     "Security",              # optional
    "priority":  "high",                  # low | normal | high | critical
    "channel":   "all",                   # specific channel or "all"
    "icon":      "security",              # optional
    "data":      { ... }                  # additional data
})
```

### 8.2 Delivery channels

**TTS (via voice_core):**

```python
async def _send_tts(self, notification: Notification):
    await self.publish("voice.speak", {
        "text":   notification.message,
        "lang":   self._config.get("tts_lang", "en"),
        "volume": self._config.get("tts_volume", 0.8),
    })
```

**Telegram Bot:**

```python
# Bot token — via Secrets Vault (no OAuth needed, just Bot Token)
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

**Web Push (via notify_push system module):**

```python
async def _send_push(self, notification: Notification):
    await self.publish("push.send", {
        "title":   notification.title or "SelenaCore",
        "body":    notification.message,
        "icon":    notification.icon,
        "data":    notification.data,
    })
```

**Email (via SMTP):**

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

### 8.3 Routing rules

```python
# Settings: list of rules (checked in order, all matching rules are applied)

ROUTING_RULES = [
    {
        "name":     "critical -> all channels",
        "filter":   { "priority": "critical" },
        "channels": ["tts", "telegram", "push"]
    },
    {
        "name":     "at night -> push only, no TTS",
        "filter":   { "priority": ["high", "normal"] },
        "time_range": { "from": "22:00", "to": "08:00" },
        "channels": ["push"]     # NOT tts to avoid waking people
    },
    {
        "name":     "everything else -> TTS + push",
        "filter":   {},           # matches everything
        "channels": ["tts", "push"]
    }
]
```

### 8.4 Events

**Listens to:**

```
notification.send      { message, title?, priority?, channel?, icon?, data? }
voice.speak_done       { text }    <- confirmation from voice_core
```

**Published:**

```
notification.delivered { message, channels: [...], timestamp }
notification.failed    { message, channel, error }
```

### Settings (settings.html)

```
Telegram:
  Bot Token (via Secrets Vault — "Connect" button)
  Chat ID

Email:
  SMTP host/port, from/to, username/password, TLS

TTS:
  Language, volume

Routing rules:
  Table with filters and channels
  "Test" — send a test notification
```

**Dependencies:**

```
httpx>=0.27
aiosmtplib>=3.0
```

**Tests:**

```python
# test: notification.send -> TTS event published (mock)
# test: notification.send -> Telegram POST called (mock httpx)
# test: routing rule priority=critical -> all channels
# test: time_range rule blocks TTS during night hours
# test: channel="tts" explicitly -> only TTS sent
# test: failed delivery -> notification.failed event
```

---

## Module 9: `update_manager`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 64 MB

### Purpose

OTA (Over-The-Air) updates for SelenaCore and system modules. Checks GitHub Releases, downloads, verifies SHA256, applies with rollback capability.

### 9.1 Checking for updates

```python
RELEASES_URL = "https://api.github.com/repos/dotradepro/SelenaCore/releases/latest"

async def check_updates(self) -> UpdateInfo | None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(RELEASES_URL,
            headers={"Accept": "application/vnd.github.v3+json"})

    release = resp.json()
    latest_version = release["tag_name"].lstrip("v")   # "0.4.0"
    current_version = self._current_version()           # from VERSION file

    if latest_version == current_version:
        return None

    # Find asset with sha256sum file
    assets = {a["name"]: a for a in release["assets"]}
    return UpdateInfo(
        version=latest_version,
        download_url=assets["selenacore.tar.gz"]["browser_download_url"],
        sha256_url=assets["selenacore.tar.gz.sha256"]["browser_download_url"],
        changelog=release["body"],
        published_at=release["published_at"],
    )
```

### 9.2 Update process

```python
async def apply_update(self, update: UpdateInfo) -> bool:
    # Step 1: Download archive
    archive_path = Path("/tmp/selenacore-update.tar.gz")
    await self._download(update.download_url, archive_path,
                         progress_callback=self._emit_progress)

    # Step 2: Verify SHA256 (MANDATORY)
    sha256_file = await self._fetch_text(update.sha256_url)
    expected_hash = sha256_file.split()[0]
    actual_hash   = sha256(archive_path.read_bytes()).hexdigest()

    if actual_hash != expected_hash:
        logger.error(f"SHA256 mismatch! Expected {expected_hash}, got {actual_hash}")
        await self.publish("update.failed", {
            "version": update.version,
            "reason":  "sha256_mismatch"
        })
        return False

    # Step 3: Create backup of current version
    backup_dir = Path("/secure/core_backup") / self._current_version()
    shutil.copytree("/opt/selenacore/core", backup_dir, dirs_exist_ok=True)

    # Step 4: Extract to temporary directory
    tmp_dir = Path("/tmp/selenacore-new")
    shutil.unpack_archive(str(archive_path), str(tmp_dir))

    # Step 5: Update core.manifest and master.hash for new files
    await self._update_manifest(tmp_dir)

    # Step 6: Apply (atomic replacement via rename)
    shutil.copytree(tmp_dir / "core", "/opt/selenacore/core",
                    dirs_exist_ok=True)

    # Step 7: Restart core via systemd
    subprocess.run(["systemctl", "restart", "smarthome-core"],
                   check=True)

    await self.publish("update.applied", {
        "version":    update.version,
        "from":       self._current_version(),
        "applied_at": datetime.utcnow().isoformat()
    })
    return True
```

### 9.3 Automatic check

```python
# Check for updates once daily at 03:00
# Via scheduler (EventBus.publish):
await self.publish("scheduler.register", {
    "job_id":     "update_manager:daily_check",
    "trigger":    "cron:0 3 * * *",
    "event_type": "update.check_requested",
    "payload":    {},
    "owner":      "update-manager"
})
```

### 9.4 Events

**Published:**

```
update.available    { version, changelog, published_at }
update.downloading  { version, progress_percent }
update.applying     { version }
update.applied      { version, from, applied_at }
update.failed       { version, reason }
update.no_updates   { current_version }
```

**Listens to:**

```
update.check_requested    -> run check
update.apply_requested    -> { version } -> apply
```

### REST endpoints (mounted at `/api/ui/modules/update_manager/` via `get_router()`)

```
GET  /status              -> current version and update availability
POST /check               -> trigger update check
POST /apply               -> apply available update
GET  /health              -> {"status": "ok"}
```

### widget.html (FULL, size 2x1)

```
Current version: v0.3.0-beta
Status: Up to date / v0.4.0 available

If update available:
  Version, date, first 200 characters of changelog
  [Update] button (with confirmation)

Progress bar during download/apply
```

**Dependencies:**

```
httpx>=0.27
```

**Tests:**

```python
# test: GitHub API returns newer version -> update.available published
# test: same version -> update.no_updates
# test: SHA256 mismatch -> update.failed, no files changed
# test: download progress emitted (mock)
# test: backup created before applying update
# test: manifest updated after applying update
```

---

## Module 10: `device_control`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 64 MB

### Purpose

Universal smart-device manager. Owns the `device.on` / `device.off` voice
intents, stores devices in the shared `Device` registry (module_id
`device-control`), and dispatches commands via pluggable drivers. Designed
for personal-use installations — **no Tuya developer account required**.

### 10.1 Architecture

```
voice.intent (device.on/off)
    ↓
DeviceControlModule._on_voice_intent
    ↓  _resolve_device(entity, location)  — 4-tier search:
    ↓    1. entity_type + location strict
    ↓    2. location keyword vs location/name/meta.name_en
    ↓    3. entity_type alone
    ↓    4. single-device fallback
    ↓
driver = get_driver(device.protocol, meta)
    ↓  tuya_local (tinytuya, persistent socket, push events)
    ↓  tuya_cloud (tuya-device-sharing-sdk)
    ↓  mqtt      (via protocol-bridge, stub)
    ↓
driver.set_state({"on": True/False})
    ↓
patch_device_state + publish("device.state_changed")
    ↓
speak_action → rephrase → TTS acknowledgement
```

### 10.2 Tuya onboarding — user-code wizard (no developer account)

Uses the same flow as Home Assistant 2024.2+ "Smart Life" integration:

1. User opens the Smart Life / Tuya Smart mobile app → Me → ⚙️ → "Authorization code" (or "User code")
2. User enters the 6-8 character code in the SelenaCore wizard
3. Backend calls `LoginControl.qr_code(HA_CLIENT_ID, "haauthorize", user_code)` and renders the returned QR as a PNG
4. User scans the QR inside the same Smart Life app (+ → Scan)
5. Backend polls `LoginControl.login_result(...)` every 2 s until Tuya returns `{access_token, refresh_token, endpoint, terminal_id, uid}`
6. Credentials are stored encrypted in `SecretsVault` (service: `device-control_tuya_cloud`) and the token is auto-refreshed by the SDK
7. `Manager.update_device_cache()` pulls the full device list; user picks devices → bulk import creates `Device` rows with `protocol=tuya_local` (if IP + local_key present) or `tuya_cloud` (fallback)

### 10.3 Pluggable driver architecture

Each driver subclasses `DeviceDriver` and implements:

```python
async def connect(self) -> dict                # initial logical state
async def disconnect(self) -> None
async def set_state(self, state: dict) -> None # e.g. {"on": True}
async def get_state(self) -> dict
async def stream_events(self) -> AsyncGenerator[dict, None]  # push
```

One watcher asyncio task per device holds the persistent connection and
yields state changes as the device pushes them (no polling).

### 10.4 Auto-downgrade local → cloud

If `tuya_local` driver gets `Err 905: Device Unreachable` twice in a row,
the module automatically patches `device.protocol = "tuya_cloud"` and
continues through the cloud path. This handles mixed-subnet / mesh-router
setups where LAN broadcast cannot reach the device.

### 10.5 Auto-translation of display names

On wizard import and on `PATCH /devices`, the display name is translated
to English via `core.api.helpers.translate_to_en` and stored in
`meta.name_en`. `PatternGenerator._gen_device` uses `meta.name_en` (if
present) as the source for the auto-generated English regex patterns,
falling back to `device.name` only if it is already ASCII.

### 10.6 Events

```
device.registered    { device_id, name }
device.removed       { device_id }
device.online        { device_id }
device.offline       { device_id }
device.state_changed { device_id, new_state, source: "device-control" }
```

### REST endpoints (mounted at `/api/ui/modules/device-control/`)

```
GET    /devices                          -> list devices we own
POST   /devices                          -> manual add
PATCH  /devices/{id}                     -> edit (auto-translates name_en)
DELETE /devices/{id}                     -> delete + purge auto_entity patterns
POST   /devices/{id}/test                -> toggle on→off→on
POST   /devices/{id}/command             -> {state: {...}}
GET    /drivers                          -> available driver types
GET    /tuya/wizard/status               -> vault summary
POST   /tuya/wizard/start                -> user_code → qr payload
POST   /tuya/wizard/poll                 -> block until user scans
POST   /tuya/wizard/refresh              -> re-query with stored creds
POST   /tuya/wizard/import               -> bulk-import selected cloud devices
POST   /tuya/wizard/disconnect           -> wipe credentials
GET    /tuya/wizard/qr.png?url=...       -> render QR as PNG
GET    /widget                           -> compact device list
GET    /settings                         -> 2-tab UI (Devices + Tuya wizard)
```

### Settings (settings.html)

```
Tab "Devices":
  Table of devices: name, type, location, protocol, state, [Test][Edit][Delete]

Tab "Tuya Cloud Wizard":
  Status badge (connected/not connected)
  Step instructions (Smart Life → Me → ⚙️ → User code)
  Authorization code input + [Start] button
  QR display (after Start) + polling spinner
  [Refresh devices] button (re-use stored creds for new devices)
  [Disconnect] button

Edit dialog:
  Name (displayed, any language)
  Name in English (for voice patterns — auto-translated if empty)
  Entity type (light / switch / outlet / fan / thermostat / sensor)
  Location (room, English — auto-translated if non-ASCII)
```

**Dependencies:**

```
tinytuya>=1.13.0
tuya-device-sharing-sdk>=0.2
qrcode (already in core requirements)
```

**Tests:**

```python
# test: wizard/start returns qr_url containing the tuyaSmart-- prefix
# test: _resolve_device 4-tier search (strict/location/entity/single)
# test: auto-downgrade on Err 905 switches protocol in DB
# test: PATCH name=... + empty name_en auto-translates via llm_call
# test: PATCH without location does NOT wipe existing location
# test: ASCII guard skips devices with no English name
```

---

## Readiness criteria for all modules

### Each module must:

- [ ] Have a `manifest.json` with correct fields
- [ ] Respond to `GET /health -> 200 { status: "ok" }` (via `get_router()` mounted at `/api/ui/modules/{name}/`)
- [ ] Serve `GET /widget.html` (if `ui_profile != HEADLESS`)
- [ ] Serve `GET /settings.html` (if it has settings)
- [ ] Subclass `SystemModule` with proper `start()` and `stop()` methods
- [ ] Export `module_class` from `__init__.py`
- [ ] Have all `async def` on public methods
- [ ] Have type hints on all public methods
- [ ] Have tests covering the main logic
- [ ] Pass `pytest tests/ -x -q`
- [ ] Pass `python -m mypy <module_dir>/`
- [ ] Not use `print()`, `eval()`, `exec()`
- [ ] Not access `/secure/` directly
- [ ] Not publish `core.*` events

### Integration requirements:

- [ ] `scheduler` works and correctly computes sunrise/sunset for given coordinates
- [ ] `automation_engine` registers triggers in `scheduler` on start
- [ ] `automation_engine` reacts to `device.state_changed` from `protocol_bridge`
- [ ] `automation_engine` uses `presence_detection` through `presence.home/away` events
- [ ] `automation_engine` uses `weather_service` through `weather.updated` events
- [ ] `automation_engine` sends notifications through `notification.send`
- [ ] `device_watchdog` receives heartbeat from `protocol_bridge`
- [ ] `notification_router` delivers via TTS (publishes `voice.speak`)
- [ ] `update_manager` uses `scheduler` for daily check

### Integration test (end of implementation):

```python
# tests/test_integration.py

# Scenario: "Morning lighting"
# 1. scheduler sends event at 07:00
# 2. automation_engine receives it, checks condition (someone is home)
# 3. presence_detection says "Alice is home"
# 4. automation_engine sends device_state for the light
# 5. protocol_bridge receives state_changed and publishes MQTT command
# 6. notification_router receives notification.send -> TTS
# 7. voice_core receives voice.speak

# All through mock SystemModule without real Core API
```

---

## Git workflow

```bash
# Branch
git checkout -b feat/N-system-modules

# Commits by step:
git commit -m "feat(scheduler): implement cron/interval/astro triggers [#N]"
git commit -m "feat(device_watchdog): add ARP and MQTT presence check [#N]"
git commit -m "feat(protocol_bridge): add MQTT broker and Zigbee bridge [#N]"
git commit -m "feat(automation_engine): implement YAML rules engine [#N]"
git commit -m "feat(presence_detection): add WiFi ARP and BT detection [#N]"
git commit -m "feat(weather_service): add open-meteo integration [#N]"
git commit -m "feat(energy_monitor): add power tracking and anomalies [#N]"
git commit -m "feat(notification_router): add TTS/Telegram/push routing [#N]"
git commit -m "feat(update_manager): add OTA with SHA256 verification [#N]"
git commit -m "feat(device_control): smart device manager + Tuya Smart Life wizard [#N]"
git commit -m "test(system_modules): add integration tests [#N]"

# Merge
git checkout main
git merge feat/N-system-modules
git push origin main
```

---

## Module 11: `media_player`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 128 MB
**CPU:** 0.5

### Purpose

Media player: internet radio, USB/SD, SMB/CIFS network shares, Internet Archive. Voice control, album covers, M3U/PLS playlists.

### 11.1 Playback engine

```python
# Backend: libvlc (python-vlc) in headless mode
# Supported formats: MP3, OGG, FLAC, WAV, OPUS, HTTP streams, M3U/PLS

SUPPORTED_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav", ".opus", ".m3u", ".pls"}
```

### 11.2 Audio sources

**Internet radio (RadioBrowser API):**

```python
# RadioBrowserSource — search by tag, country, language
# Local station library: /var/lib/selena/modules/media-player/stations.json
# Endpoint: POST /api/import/radiobrowser?tag=jazz&country=uk
```

**USB/SD media:**

```python
# USBSource — auto-detect connected USB drives
# Recursive scan for audio files
# Endpoint: GET /import/usb/scan
```

**SMB/CIFS network shares:**

```python
# SMBSource — connect to network folders
# Credentials: username, password, domain (default: WORKGROUP)
# Endpoint: POST /api/import/smb
```

**Internet Archive.org:**

```python
# InternetArchiveSource — public collections (music, audiobooks)
# Endpoint: POST /api/import/archive?query=public+radio
```

### 11.3 Album covers

```python
# CoverFetcher — Last.fm API (requires API key)
# Cache: /var/lib/selena/modules/media-player/covers/
# Config: MEDIA_LASTFM_API_KEY
```

### 11.4 Voice control

```python
# MediaVoiceHandler — listens to voice.intent events via EventBus.subscribe
# Intents: media.play_artist, media.pause, media.stop, media.next, media.previous
# Trigger: "play music", "pause", "next track"
```

### 11.5 Module API

REST endpoints mounted at `/api/ui/modules/media_player/` via `get_router()`:

```
GET  /player/state              -> current state (track, position, volume)
POST /player/play               -> start playback
POST /player/pause              -> pause
POST /player/stop               -> stop
POST /player/next               -> next track
POST /player/previous           -> previous track
POST /player/volume             -> { volume: 0-100 }
POST /player/seek               -> { position: <seconds> }

GET  /radio/stations            -> station list
POST /radio/add-station         -> add station
POST /radio/import-m3u          -> import M3U playlist
POST /import/radiobrowser       -> import from RadioBrowser
POST /import/smb                -> import from SMB share
POST /import/archive            -> import from Internet Archive
GET  /import/usb/scan           -> scan USB drives

POST /config                    -> update settings
GET  /health                    -> {"status": "ok"}
```

### 11.6 State broadcasting

```python
# Every 3 seconds during playback (via EventBus.publish):
await self.publish("media.state_changed", {
    "state":    "playing",      # "playing" | "paused" | "stopped"
    "track":    "Song Name",
    "artist":   "Artist",
    "album":    "Album",
    "cover_url": "/covers/abc.jpg",
    "position": 45.2,           # seconds
    "duration": 210.0,
})
```

### 11.7 Events

**Published:**

```
media.state_changed    { state, track, artist, album, cover_url, position, duration }
```

**Listens to:**

```
voice.intent           -> handle media.* intents
```

### 11.8 Settings

```
MEDIA_LASTFM_API_KEY=...       # Last.fm API key for covers
MEDIA_DEFAULT_VOLUME=70         # default volume (0-100)
MEDIA_STREAM_BUFFER_MS=1000     # stream buffer (ms)
MEDIA_NORMALIZE=false           # volume normalization
```

### widget.html (FULL, size 2x2)

```
Album cover (if available)
Track name * Artist
Progress bar with timer
Buttons: prev play/pause next volume
Mini playlist: 3-5 tracks
```

**Dependencies:**

```
python-vlc>=3.0
httpx>=0.27
smbprotocol>=1.10       # for SMB
```

**Tests:**

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

## Module 12: `voice_core`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 256 MB
**CPU:** 0.5

### Purpose

SelenaCore voice subsystem. Includes: speech recognition (STT, Vosk), speech synthesis (TTS, Piper), wake-word detector (openWakeWord), speaker identification (resemblyzer), privacy mode (microphone disable via GPIO).

### 12.1 Speech recognition (STT)

```python
# Engine: Vosk (offline, supports Ukrainian and Russian)
# Model: configurable via VOSK_MODEL (default: vosk-model-small-uk)
# Sample rate: 16 kHz, mono
# WebSocket streaming: WS /api/ui/modules/voice_core/stream

# Real-time: audio from microphone -> Vosk -> text -> Intent Router
```

### 12.2 Speech synthesis (TTS)

```python
# Engine: Piper (neural network, local)
# Voices:
VOICES = {
    "uk_UA-ukrainian_tts-medium": "Ukrainian (medium quality)",
    "uk_UA-lada-medium":          "Ukrainian Lada",
    "ru_RU-irina-medium":         "Russian Irina",
    "ru_RU-ruslan-medium":        "Russian Ruslan",
    "en_US-amy-medium":           "English Amy",
    "en_US-ryan-high":            "English Ryan (HQ)",
}

# Endpoints (mounted at /api/ui/modules/voice_core/ via get_router()):
# GET  /tts/voices     -> voice list
# POST /tts/test       -> test synthesis (returns WAV)
```

### 12.3 Wake-word detector

```python
# Engine: openWakeWord (ONNX inference)
# Default wake-word: "hey_selena"
# Threshold: 0.1-1.0 (default 0.5, configurable)
# Background loop: continuous microphone listening via asyncio

# On detection -> publishes voice.wake_word event via EventBus.publish
# -> starts STT recording -> text -> Intent Router
```

### 12.4 Speaker identification (Speaker ID)

```python
# Engine: resemblyzer (voice embeddings)
# Storage: numpy arrays in /var/lib/selena/speaker_embeddings/
# Similarity threshold: 0.75 (configurable)

# Endpoints (mounted at /api/ui/modules/voice_core/ via get_router()):
# GET    /speakers                -> list of registered speakers
# DELETE /speakers/{user_id}      -> delete voice print
```

### 12.5 Privacy mode

```python
# GPIO button (pin 17, configurable) + voice command
# On activation:
#   - Full stop of STT/wake-word listening
#   - LED indicator (if connected)
#   - Publish voice.privacy_on event via EventBus.publish

# Endpoints (mounted at /api/ui/modules/voice_core/ via get_router()):
# GET  /privacy                -> current status
# POST /privacy/toggle         -> toggle
```

### 12.6 Voice query history

```python
# Storage: SQLite in /var/lib/selena/selena.db
# Table: voice_history(id, timestamp, user_id, wake_word,
#                         recognized_text, intent, response, duration_ms)
# Rotation: maximum 10,000 records

# Endpoint: GET /history?limit=50
```

### 12.7 Audio device management

```python
# Auto-detect: ALSA cards (/proc/asound/cards) + PulseAudio/PipeWire (Bluetooth)
# Input priority:  USB > I2S GPIO > Bluetooth > HDMI > built-in
# Output priority: USB > I2S GPIO > Bluetooth > HDMI > jack

# Endpoint: GET /audio/devices -> list of inputs and outputs
```

### 12.8 Module API

REST endpoints mounted at `/api/ui/modules/voice_core/` via `get_router()`:

```
GET  /config               -> STT/TTS/wake-word settings
POST /config               -> update settings
GET  /privacy              -> privacy mode status
POST /privacy/toggle       -> toggle privacy
GET  /audio/devices        -> audio device list
GET  /stt/status           -> STT status
WS   /stream               -> WebSocket audio streaming
GET  /tts/voices           -> TTS voice list
POST /tts/test             -> test synthesis
GET  /wakeword/status      -> wake-word detector status
GET  /speakers             -> list of registered voices
DELETE /speakers/{user_id} -> delete voice print
GET  /history?limit=50     -> query history
GET  /health               -> {"status": "ok"}
```

### 12.9 Events

**Published:**

```
voice.wake_word        { wake_word, score }
voice.recognized       { text, user_id, duration_ms }
voice.privacy_on       { privacy_mode: true }
voice.privacy_off      { privacy_mode: false }
voice.speak_done       { text }
```

**Listens to:**

```
voice.speak            { text, lang?, volume? }  -> TTS synthesis and playback
```

### widget.html (FULL, size 2x2)

```
Microphone indicator (active / privacy)
Last recognized text
STT/TTS/Wake-word status (green/red)
"Test TTS" button
"Privacy on/off" button
```

**Dependencies:**

```
vosk>=0.3
piper-tts>=1.0
openwakeword>=0.6
resemblyzer>=0.1
pyaudio>=0.2
RPi.GPIO>=0.7        # optional, Raspberry Pi only
```

**Tests:**

```python
# test: STT returns text from audio (mock Vosk)
# test: TTS generates WAV (mock Piper)
# test: wake-word detected when score > threshold (mock)
# test: speaker ID matches registered speaker (mock resemblyzer)
# test: privacy toggle publishes voice.privacy_on/off
# test: voice.speak event -> TTS -> playback
# test: history rotation at > 10,000 records
# test: audio devices endpoint returns correct structure
```

---

## Module 13: `llm_engine`

**Type:** SYSTEM
**ui_profile:** SETTINGS_ONLY
**Memory:** 512 MB - 2 GB (depends on model)
**CPU:** 1.0 - 2.0

### Purpose

LLM engine and intent router. Four-tier architecture: Fast Matcher (YAML keywords/regex, 0 ms) -> System Module Intents (in-process regex, microseconds) -> Module Bus Intents (user modules via WebSocket, ms) -> Ollama LLM (3-8 s). Automatic LLM disable when RAM is low.

### 13.1 Fast Matcher (Tier 1)

```python
# Config: /opt/selena-core/config/intent_rules.yaml
# Format: YAML rules with keywords, regex patterns, response templates, actions

# Example rule:
# - name: lights_on
#   keywords: ["turn on lights", "switch on lights"]
#   regex: "(turn on|switch on)\\s+(lights?|lamp)"
#   response: "Turning on the lights"
#   action: { type: "device_state", device_id: "@lights", state: { power: true } }

# Response time: < 1 ms (in-memory lookup)
# Reload: reload() updates rules from file on the fly
```

### 13.2 System Module Intents (Tier 1.5)

```python
# System modules running in-process can register regex-based intents
# Intent Router checks all registered system module intents after Fast Matcher
# Response time: microseconds (in-process Python regex matching)
# Registration: system modules call self.register_intent() during start()
# Example: media_player registers "play music", "pause", "next track"
```

### 13.3 Module Bus Intents (Tier 2)

```python
# User modules connected via WebSocket Module Bus can declare intents
# Intent Router queries matching modules via the WebSocket bus
# If module understands the request — returns result
# Response time: milliseconds (WebSocket round-trip)
```

### 13.4 Ollama LLM (Tier 3)

```python
# Endpoint: http://localhost:11434 (configurable via OLLAMA_URL)
# Default model: phi3:mini (configurable via OLLAMA_MODEL)

# Recommended models:
MODELS = {
    "phi3:mini":     {"params": "3.8B", "size": "2.2 GB", "note": "default, fast"},
    "gemma2:2b":     {"params": "2B",   "size": "1.6 GB", "note": "multilingual"},
    "qwen2.5:0.5b":  {"params": "0.5B", "size": "0.4 GB", "note": "ultra-lightweight"},
    "llama3.2:1b":   {"params": "1B",   "size": "0.7 GB", "note": "small English"},
}

# Auto-disable: if free RAM < 5 GB (configurable via OLLAMA_MIN_RAM_GB)
# Temperature: 0.7 (configurable)
# Max tokens: 512 (per request)
# API: /api/generate (streaming and non-streaming)
```

### 13.5 Model Manager

```python
# Ollama model management:
# - List of recommended models with installation status
# - Download models via Ollama pull
# - Switch active model (persistent)
# - Auto-detect invalid selection
```

### 13.6 Dynamic system prompt

```python
# When calling LLM — system prompt is automatically generated:
# - List of registered devices
# - List of available commands
# - Current time and date
# - Presence status (who is home)
# - Context of last 5 voice queries
```

### 13.7 Intent Router tier summary

```
Tier 1:   Fast Matcher           — YAML keywords/regex, 0 ms
Tier 1.5: System Module Intents  — in-process regex, microseconds
Tier 2:   Module Bus Intents     — user modules via WebSocket, ms
Tier 3:   Ollama LLM             — full LLM inference, 3-8 sec
```

Each tier is tried in order. If a tier returns a match, subsequent tiers are skipped.

### 13.8 Module API

REST endpoints mounted at `/api/ui/modules/llm_engine/` via `get_router()`:

```
POST /intent               -> { text: "turn on lights" } -> IntentResult
GET  /models               -> model list with statuses
POST /models/pull          -> { model: "phi3:mini" } -> start download
POST /models/switch        -> { model: "gemma2:2b" } -> switch
GET  /rules                -> current Fast Matcher rules
POST /rules/reload         -> reload rules from YAML
GET  /health               -> LLM status (available / disabled due to RAM)
```

### 13.9 Events

**Published:**

```
voice.intent           { intent, response, action, source, tier, latency_ms }
llm.model_switched     { model, previous }
llm.disabled           { reason: "low_ram", available_gb }
llm.enabled            { model }
```

**Listens to:**

```
voice.recognized       { text, user_id }  -> start Intent Router
```

### Settings

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=phi3:mini
OLLAMA_TIMEOUT=30              # seconds
OLLAMA_MIN_RAM_GB=5.0          # LLM disable threshold
FAST_MATCHER_RULES=/opt/selena-core/config/intent_rules.yaml
```

**Dependencies:**

```
httpx>=0.27
pyyaml>=6.0
psutil>=5.9
```

**Tests:**

```python
# test: Fast Matcher finds intent by keyword (Tier 1)
# test: Fast Matcher finds intent by regex (Tier 1)
# test: System Module Intent matched by regex (Tier 1.5)
# test: Module Bus Intent matched via WebSocket (Tier 2, mock)
# test: Fast Matcher miss -> fallback to Ollama (Tier 3, mock)
# test: Ollama disabled when RAM < 5 GB (mock psutil)
# test: model switch persists between restarts
# test: rules reload picks up changes from YAML
# test: IntentResult contains source, tier, latency
# test: dynamic system prompt contains device list
```

---

## Module 14: `secrets_vault`

**Type:** SYSTEM
**ui_profile:** SETTINGS_ONLY
**Memory:** 64 MB
**CPU:** 0.1

### Purpose

Secure storage for secrets and OAuth tokens. AES-256-GCM encryption. OAuth Device Authorization Grant (RFC 8628) with QR codes. API proxy for modules — modules NEVER see tokens.

### 14.1 Encrypted storage

```python
# Storage: /secure/tokens/<service>.enc
# Master key: /secure/vault_key (base64, 256 bits)
# Encryption: AES-256-GCM with random 96-bit nonce per secret
# Key is generated automatically on first launch

# Data model:
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
# Providers: Google, GitHub (extensible via KNOWN_PROVIDERS)
# Flow:
# 1. POST /api/v1/secrets/oauth/start -> session_id, user_code, verification_uri, QR
# 2. User scans QR or enters code on provider's website
# 3. Module polls GET /api/v1/secrets/oauth/status/{session_id}
# 4. On authorization -> tokens are encrypted and saved in vault
# QR code: generated on the fly (qrcode library)
# Session expiration: 30 minutes (configurable)
```

### 14.3 API proxy (Token Injection)

```python
# POST /api/v1/secrets/proxy
# Purpose: forward HTTP requests to external APIs with token injection
# Security:
#   - HTTPS URLs only (SSRF protection)
#   - Block private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 127.0.0.0/8)
#   - Tokens are NEVER returned to the module
#   - Maximum response size: 5 MB

# Request:
# { "service": "google", "method": "GET",
#   "url": "https://gmail.googleapis.com/...",
#   "extra_headers": {}, "json_body": null, "params": {} }

# Response: { "status": 200, "headers": {...}, "body": {...} }
```

### 14.4 Automatic token refresh

```python
# Background task: checks all tokens every 60 seconds
# Auto-refresh: 5 minutes before expiration via refresh_token
# PBKDF2: 600,000 iterations (RFC 8617)
```

### 14.5 Module API

REST endpoints mounted at `/api/ui/modules/secrets_vault/` via `get_router()`:

```
POST /api/v1/secrets/oauth/start          -> start OAuth flow
GET  /api/v1/secrets/oauth/status/{id}    -> session status
GET  /api/v1/secrets/oauth/qr/{id}        -> QR code (PNG)
POST /api/v1/secrets/proxy                -> API proxy request
GET  /api/v1/secrets/services             -> list of connected services
DELETE /api/v1/secrets/services/{name}    -> disconnect service
GET  /health                              -> {"status": "ok"}
```

### 14.6 Events

**Published:**

```
secrets.token_refreshed   { service, expires_at }
secrets.token_expired     { service, reason }
secrets.oauth_completed   { service, module }
```

### Directory structure

```
/secure/
  vault_key                    # Master key (permissions 600)
  tokens/
    google.enc                 # Encrypted tokens
    github.enc
    tuya.enc
```

**Dependencies:**

```
cryptography>=46.0
httpx>=0.27
qrcode>=7.4
```

**Tests:**

```python
# test: store/retrieve secret -> decryption is correct
# test: AES-256-GCM nonce is unique per secret
# test: OAuth start returns session_id and user_code
# test: OAuth status polling -> authorized after mock authorization
# test: proxy blocks HTTP URL (HTTPS only)
# test: proxy blocks private IPs (SSRF protection)
# test: auto-refresh 5 minutes before expiration (mock time)
# test: vault_key generated on first launch
```

---

## Module 15: `user_manager`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 128 MB
**CPU:** 0.2

### Purpose

SelenaCore user management. Flat model: first user = admin, others = resident (household members). CRUD profiles, PIN authentication with rate limiting, Face ID via face_recognition, voice biometrics via resemblyzer, action audit log. No role-based permissions — PIN/QR gate is the only security barrier.

### 15.1 User profiles

```python
# Storage: SQLite in /var/lib/selena/selena.db
# Roles: admin (first) | resident (all others)

# Table users:
# user_id TEXT PK, username TEXT UNIQUE, display_name TEXT,
# role TEXT DEFAULT 'resident', pin_hash TEXT,
# created_at REAL, last_seen REAL,
# face_enrolled INTEGER DEFAULT 0, voice_enrolled INTEGER DEFAULT 0,
# active INTEGER DEFAULT 1
```

### 15.2 PIN authentication

```python
# Algorithm: SHA-256 with salt "selena-pin-salt-v1"
# Brute-force protection:
#   - Maximum 5 failed attempts per user
#   - After 5 attempts -> lockout for 10 minutes (LOCK_DURATION_SEC = 600)
#   - Lock state: in memory (resets on restart)
```

### 15.3 Face ID

```python
# Engine: face_recognition (dlib backend)
# Registration: JPEG from browser webcam -> 128-dimensional face encoding
# Storage: numpy arrays in /var/lib/selena/face_encodings/
# Verification: comparison with all registered encodings
# Threshold: 0.5 (default, configurable via FACE_TOLERANCE, lower = stricter)

# Functions:
# enroll(user_id, jpeg_bytes) -> bool
# identify(jpeg_bytes) -> user_id | None
# list_enrolled() -> list[user_id]
```

### 15.4 Voice biometrics

```python
# Engine: resemblyzer (via voice_core)
# Registration: voice recording -> compute embedding -> save
# Identification: comparison with registered embeddings
# Threshold: 0.75 (default)
```

### 15.5 Audit log

```python
# Storage: SQLite table audit_log
# Fields: timestamp, user_id, action, resource, result
# Rotation: 10,000 records
# Actions: login, logout, pin_failed, face_enrolled, device_added, etc.
```

### 15.6 Module API

REST endpoints mounted at `/api/ui/modules/user_manager/` via `get_router()`:

```
GET    /users                      -> user list
POST   /users                      -> create user
GET    /users/{user_id}            -> profile
PUT    /users/{user_id}            -> update
DELETE /users/{user_id}            -> delete
POST   /auth/pin                   -> { user_id, pin } -> authentication
POST   /auth/face                  -> multipart JPEG -> identification
POST   /users/{id}/face/enroll     -> multipart JPEG -> Face ID registration
DELETE /users/{id}/face            -> delete Face ID
POST   /users/{id}/voice/enroll    -> audio -> voice registration
DELETE /users/{id}/voice           -> delete voice print
GET    /audit?limit=100            -> audit log
GET    /health                     -> {"status": "ok"}
```

### 15.7 Events

**Published:**

```
user.authenticated     { user_id, method: "pin"|"face"|"voice" }
user.login_failed      { user_id, method, reason }
user.lockout           { user_id, duration_sec: 600 }
user.created           { user_id, username }
user.deleted           { user_id }
```

### widget.html (FULL, size 2x1)

```
User list:
  Avatar * Name * Presence * Last login
  Badges: PIN | Face ID | Voice ID
"Add resident" button
```

**Dependencies:**

```
SQLAlchemy>=2.0
aiosqlite>=0.19
face_recognition>=1.3
numpy>=1.24
```

**Tests:**

```python
# test: user creation -> saved to DB
# test: PIN authentication -> success with correct PIN
# test: PIN authentication -> rejection with incorrect PIN
# test: 5 failed attempts -> 10-minute lockout
# test: Face ID enroll -> face_enrolled = 1
# test: Face ID identify -> correct user_id
# test: audit log records all actions
# test: audit log rotation at > 10,000 records
```

---

## Module 16: `hw_monitor`

**Type:** SYSTEM
**ui_profile:** ICON_SETTINGS
**Memory:** 32 MB
**CPU:** 0.05

### Purpose

Hardware resource monitoring: CPU temperature, RAM and disk usage. Alerts when thresholds are exceeded. Automatic degradation (stopping modules) under critical load.

### 16.1 Metrics collection

```python
# Data sources:
# CPU temperature: /sys/class/thermal/ or vcgencmd (Raspberry Pi)
# RAM: /proc/meminfo (percent, MB used, MB total)
# Disk: shutil.disk_usage() (percent, free GB)

@dataclass
class SystemMetrics:
    cpu_temp_c: float | None     # C
    ram_used_pct: float          # %
    ram_used_mb: float
    ram_total_mb: float
    disk_used_pct: float         # %
    disk_free_gb: float
```

### 16.2 Alert thresholds

```python
CPU_TEMP_WARN  = 70.0   # C
CPU_TEMP_CRIT  = 85.0   # C
RAM_WARN_PCT   = 80     # %
RAM_CRIT_PCT   = 92     # %
DISK_WARN_PCT  = 85     # %
DISK_CRIT_PCT  = 95     # %
MONITOR_INTERVAL = 30   # seconds
```

### 16.3 RAM degradation strategy

```python
# When RAM > 92%:
# 1. Send hw.ram_crit event via EventBus.publish
# 2. Stop optional modules in priority order (low -> high)
# 3. System modules (voice_core, llm_engine) — last
# throttle.py module manages the stop order
```

### 16.4 Module API

REST endpoints mounted at `/api/ui/modules/hw_monitor/` via `get_router()`:

```
GET /metrics              -> current metrics (CPU, RAM, disk)
GET /metrics/history      -> history for the last hour
GET /thresholds           -> current thresholds
POST /thresholds          -> update thresholds
GET /health               -> {"status": "ok"}
```

### 16.5 Events

**Published:**

```
hw.metrics_collected   { cpu_temp_c, ram_used_pct, ram_used_mb, ram_total_mb, disk_used_pct, disk_free_gb }
hw.cpu_temp_warn       { cpu_temp_c, threshold }
hw.cpu_temp_crit       { cpu_temp_c, threshold }
hw.ram_warn            { ram_used_pct, threshold }
hw.ram_crit            { ram_used_pct, threshold } -> may trigger degradation
hw.disk_warn           { disk_used_pct, threshold }
hw.disk_crit           { disk_used_pct, threshold }
```

### widget.html (ICON_SETTINGS)

```
Icon: thermometer (green < 70 C, yellow < 85 C, red > 85 C)
Badge: "62 C * 74% RAM"
```

**Dependencies:**

```
psutil>=5.9              # fallback for /proc/meminfo
```

**Tests:**

```python
# test: CPU temperature read from /sys/class/thermal (mock)
# test: RAM usage from /proc/meminfo (mock)
# test: hw.cpu_temp_warn when temperature > 70 C
# test: hw.ram_crit when usage > 92%
# test: metrics published every 30 seconds
# test: degradation stops modules in correct order
```

---

## Module 17: `network_scanner`

**Type:** SYSTEM
**ui_profile:** FULL
**Memory:** 64 MB
**CPU:** 0.3

### Purpose

Network scanner. Discovers devices via ARP sweep (Layer 2), mDNS/Bonjour, SSDP/UPnP. Auto-classification by OUI (manufacturer by MAC address). Results -> Device Registry (via DeviceRegistry methods).

### 17.1 ARP Scanner (Layer 2)

```python
# Preferred method: arp-scan --localnet (active L2 broadcast)
# Runs ONCE per scan cycle (not per-device)
# Result cached in set for O(1) lookup

# Passive mode: reading /proc/net/arp (no root required)
# Active mode: arping command (requires cap NET_RAW)
# Limitation: maximum /24 subnet (256 addresses)
# Concurrency: asyncio.Semaphore(20) for arping calls

# Scan time for entire /24: ~1.9 seconds
```

### 17.2 mDNS/Bonjour

```python
# Library: zeroconf (async-safe)
# Monitored services:
MDNS_SERVICES = [
    "_http._tcp.local.",         # HTTP devices
    "_https._tcp.local.",        # HTTPS devices
    "_hap._tcp.local.",          # HomeKit
    "_googlecast._tcp.local.",   # Chromecast
    "_airplay._tcp.local.",      # Apple AirPlay
    "_ipp._tcp.local.",          # Printers
    "_smartthings._tcp.local.",  # SmartThings
    "_home-assistant._tcp.local.", # Home Assistant
    "_esphomelib._tcp.local.",   # ESPHome
]
# Data: name, service type, hostname, IP, port, properties
```

### 17.3 SSDP/UPnP

```python
# Protocol: multicast UDP on 239.255.255.250:1900
# Passive: listens for NOTIFY and M-SEARCH responses
# Active: sends M-SEARCH probe (ST: ssdp:all), timeout 3 seconds
# Data: USN, LOCATION, SERVER, ST
```

### 17.4 OUI Lookup

```python
# IEEE OUI database: MAC prefix -> manufacturer
# Example: AA:BB:CC -> "Apple, Inc."
# Goal: auto-classify devices by type
```

### 17.5 Module API

REST endpoints mounted at `/api/ui/modules/network_scanner/` via `get_router()`:

```
GET  /scan/arp              -> run ARP scan, return results
GET  /scan/mdns             -> list of discovered mDNS services
GET  /scan/ssdp             -> list of discovered UPnP devices
POST /scan/full             -> full scan using all methods
GET  /devices               -> all discovered devices with classification
GET  /oui/{mac}             -> manufacturer by MAC address
GET  /health                -> {"status": "ok"}
```

### 17.6 Events

**Published:**

```
device.discovered          { name, ip, mac, protocol, manufacturer, service_type }
device.offline             { device_id, ip, mac }
device.online              { device_id, ip, mac }
network.scan_complete      { method, found: N, new: N, duration_ms }
```

### widget.html (FULL, size 2x1)

```
Network: 14 devices * Last scan: 2 min ago
New: 2 (show)
List: IP * MAC * Manufacturer * Type
"Scan Now" button
```

**Dependencies:**

```
zeroconf>=0.131
arp-scan               # system package
arping                 # system package
```

**Tests:**

```python
# test: ARP scan parses /proc/net/arp correctly
# test: mDNS discovers _googlecast service (mock zeroconf)
# test: SSDP discovers UPnP device (mock)
# test: OUI lookup returns manufacturer by MAC
# test: device.discovered event on new device
# test: full scan merges results from all methods
# test: arp-scan cache — one operation per cycle
```

---

## Module 18: `ui_core`

**Type:** SYSTEM
**ui_profile:** (is the UI server)
**Memory:** 96 MB
**CPU:** 0.2

### Purpose

User interface web server. Serves PWA (React SPA) on port :80. Reverse proxy to Core API :80. Onboarding Wizard (9 first-run steps). Auto-detect display mode.

### 18.1 FastAPI server

```python
# Port: 80 (UI_PORT)
# Content: PWA static files from /static/ (built via npx vite build)
# Proxy: /api/* -> Core API :80 (CoreApiProxyMiddleware)
# SSE: streaming support via pure ASGI (not BaseHTTPMiddleware)
```

### 18.2 CoreApiProxyMiddleware

```python
# Reverse proxy for /api/* requests to Core API :80
# X-Forwarded-For / X-Real-IP for client tracking
# SSE support (non-buffered, direct ASGI send)
# Automatic host/scheme detection
# Implementation: pure ASGI (avoid BaseHTTPMiddleware for zero-copy)
```

### 18.3 PWA (Progressive Web App)

```python
# Manifest: /manifest.json (name, icons, display mode)
# Service Worker: /sw.js (caching + offline page)
# Icons: 192x192 and 512x512
# Display mode: standalone (fullscreen, no address bar)
# Offline: cached shell + "No connection" fallback page
```

### 18.4 Onboarding Wizard (9 steps)

```python
# First-run steps (sequential):
WIZARD_STEPS = [
    "wifi",          # 1. Wi-Fi connection
    "language",      # 2. Language selection (en / uk)
    "device_name",   # 3. Device name (hostname)
    "timezone",      # 4. Timezone (TZ database)
    "stt_model",     # 5. STT model selection (Vosk)
    "tts_voice",     # 6. TTS voice selection (Piper)
    "admin_user",    # 7. Create admin user + PIN
    "platform",      # 8. Platform registration on SmartHome LK
    "import",        # 9. Device import (HA / Tuya / Hue)
]

# State storage: /var/lib/selena/wizard_state.json
# Validation: each step is validated before proceeding to the next

# Endpoints:
# GET  /api/ui/wizard/status    -> current step and progress
# POST /api/ui/wizard/step      -> { step, data } -> proceed to next
```

### 18.5 Display auto-detection

```python
# Possible modes:
# headless     -> no display (server-only)
# tty          -> text terminal (Textual TUI on TTY1)
# kiosk        -> Chromium in kiosk mode (Wayland cage)
# framebuffer  -> direct framebuffer output
```

### 18.6 AP Mode (first run)

```python
# When no Wi-Fi available — creates an access point:
# SSID: Selena-<hash>
# No password (open)
# Captive portal -> redirect to wizard
# QR code for connection (generated via qrcode)
```

### 18.7 Routing

```
/                    -> index.html (PWA entrypoint)
/manifest.json       -> PWA manifest
/sw.js               -> Service Worker
/icons/*             -> icons
/api/*               -> reverse proxy to :80 (Core API)
/api/ui/wizard/*     -> Wizard endpoints
/api/ui/modules/*    -> system module endpoints (mounted via get_router())
```

### Settings

```
CORE_API_BASE=http://127.0.0.1:80
UI_PORT=80
UI_HTTPS=true
STATIC_DIR=/opt/selena-core/system_modules/ui_core/static/
```

**Dependencies:**

```
FastAPI>=0.111
httpx>=0.27
zeroconf>=0.131       # mDNS for onboarding
qrcode>=7.4           # QR for AP mode
```

**Tests:**

```python
# test: GET / returns index.html
# test: /api/* proxied to :80 (mock httpx)
# test: wizard status returns current step
# test: wizard step validates data
# test: wizard step advancing saves state
# test: SSE streaming through proxy
# test: AP mode QR code generated
```

---

## Module 19: `backup_manager`

**Type:** SYSTEM
**ui_profile:** SETTINGS_ONLY
**Memory:** 96 MB
**CPU:** 0.3

### Purpose

Local and cloud backup. Local backups to USB/SD in .tar.gz. Cloud backups with E2E encryption (PBKDF2-HMAC-SHA256 + AES-256-GCM). QR secret transfer between devices.

### 19.1 Local backup

```python
# Directories: /var/lib/selena/ (registry, history) + /etc/selena/ (config)
# Exclusions: /secure/vault_key (NEVER backed up)
# Format: .tar.gz without encryption
# Name: selena_backup_{YYYYMMDDTHHMMSSZ}.tar.gz
# Retention: 5 most recent (configurable via MAX_LOCAL_BACKUPS)
# Directory: /var/lib/selena/backups/
# Permissions: 0o600 (owner only)
```

### 19.2 Cloud backup (E2E)

```python
# Encryption: PBKDF2-HMAC-SHA256 + AES-256-GCM
# PBKDF2: 600,000 iterations, random 16-byte salt per backup
# Nonce: random 12-byte per backup (in header)
# File format: salt(16) + nonce(12) + ciphertext

# Upload: POST to PLATFORM_BACKUP_URL
# Headers:
#   X-Selena-Device: {device_hash}
#   X-Archive-Hash: {SHA256 plaintext}
#   Content-Type: application/octet-stream
```

### 19.3 QR secret transfer

```python
# Encode secrets into QR code (compressed chunks)
# For transfer between devices
# Read via camera on the new device
```

### 19.4 Module API

REST endpoints mounted at `/api/ui/modules/backup_manager/` via `get_router()`:

```
POST /api/backup/local/create        -> create local backup
GET  /api/backup/local/list          -> list local backups
POST /api/backup/cloud/create        -> create and upload cloud backup
GET  /api/backup/cloud/list          -> list cloud backups
POST /api/backup/restore             -> restore from backup
GET  /api/backup/status              -> current operation status
GET  /health                         -> {"status": "ok"}
```

### 19.5 Events

**Published:**

```
backup.created_local   { path, size_mb, sha256 }
backup.created_cloud   { backup_id, size_mb, encrypted: true }
backup.restored        { source, restored_at }
backup.failed          { operation, error }
```

### Settings (settings.html)

```
Local backup:
  Directory: /var/lib/selena/backups
  Maximum copies: 5
  [Create Backup Now]

Cloud backup:
  Encryption password: [input]
  [Create E2E Backup]

Restore:
  File selection / upload
  [Restore]

QR transfer:
  [Generate Secrets QR]
```

**Dependencies:**

```
cryptography>=46.0
httpx>=0.27
qrcode>=7.4
```

**Tests:**

```python
# test: local backup creates .tar.gz with correct contents
# test: vault_key NOT included in backup
# test: cloud backup encrypts with AES-256-GCM
# test: decryption returns original data
# test: PBKDF2 uses 600,000 iterations
# test: retention — keeps only 5 most recent
# test: backup.failed on I/O error
```

---

## Module 20: `notify_push`

**Type:** SYSTEM
**ui_profile:** SETTINGS_ONLY
**Memory:** 32 MB
**CPU:** 0.1

### Purpose

Web Push notifications (RFC 8292, VAPID). VAPID key generation, browser subscription management, push notification delivery. Used by notification_router for the "push" channel.

### 20.1 VAPID keys

```python
# Standard: RFC 8292 (Voluntary Application Server Identification)
# Library: pywebpush
# Private key: /secure/vapid_private.pem (generated on first launch)
# Public key: exposed via API for browser subscription
# Claims: VAPID_CLAIMS_SUB (e.g., "mailto:admin@selena.local")
```

### 20.2 Subscription management

```python
# Storage: /var/lib/selena/push_subscriptions.json
# Model: PushSubscription { endpoint, keys: { auth, p256dh }, user_id }
# Registration: browser -> Service Worker API -> POST /subscribe
# Deletion: on unsubscribe or explicit DELETE
```

### 20.3 Delivery

```python
# Payload: JSON { title, body, icon, data }
# Delivery: HTTP POST to subscription endpoint with VAPID-Auth header
# Retry: up to 3 attempts with backoff
# Response handling:
#   201/204 -> success
#   410     -> endpoint expired -> delete subscription
#   413     -> payload too large -> reject
#   4xx/5xx -> retry with backoff
```

### 20.4 Module API

REST endpoints mounted at `/api/ui/modules/notify_push/` via `get_router()`:

```
GET    /api/push/vapid-public-key      -> public key for subscription
POST   /api/push/subscribe             -> register subscription
GET    /api/push/subscriptions         -> subscription list (admin)
DELETE /api/push/subscriptions/{id}    -> delete subscription
POST   /api/push/test/{user_id}       -> send test notification
GET    /health                         -> {"status": "ok"}
```

### 20.5 Events

**Published:**

```
notification.sent        { user_id, title }
notification.failed      { user_id, error }
notification.subscribed  { user_id, endpoint }
```

**Listens to:**

```
push.send               { title, body, icon, data, user_id? }
```

**Dependencies:**

```
pywebpush>=2.0
py-vapid>=1.9
```

**Tests:**

```python
# test: VAPID keys generated on first launch
# test: subscribe saves subscription in JSON
# test: push delivered via pywebpush (mock)
# test: 410 -> subscription deleted
# test: retry on network failure
# test: test endpoint sends test notification
```

---

## Module 21: `remote_access`

**Type:** SYSTEM
**ui_profile:** SETTINGS_ONLY
**Memory:** 32 MB
**CPU:** 0.15

### Purpose

Secure remote access via Tailscale VPN. Connection to WireGuard mesh network without opening ports or port forwarding. Managed through the settings UI.

### 21.1 Tailscale integration

```python
# Prerequisites: tailscaled (daemon) installed on host
# Authorization:
# 1. Generate auth key in Tailscale admin console
# 2. Set TAILSCALE_AUTH_KEY in env
# 3. connect() -> device joins the mesh network
# 4. Access via Tailscale IP from anywhere in the world

async def get_status() -> TailscaleStatus:
    # tailscale status --json
    # Returns: connected, tailscale_ip, hostname, version

async def connect(auth_key: str | None = None) -> bool:
    # tailscale up --auth-key {key} --accept-routes

async def disconnect() -> bool:
    # tailscale logout
```

### 21.2 Module API

REST endpoints mounted at `/api/ui/modules/remote_access/` via `get_router()`:

```
GET  /api/remote/status         -> Tailscale connection status
POST /api/remote/connect        -> connect (auth_key in body)
POST /api/remote/disconnect     -> disconnect
GET  /health                    -> {"status": "ok"}
```

### 21.3 Events

**Published:**

```
remote.connected       { tailscale_ip, hostname }
remote.disconnected    { reason }
```

### Settings (settings.html)

```
Status: * Connected / o Disconnected
Tailscale IP: 100.64.x.x
Auth Key: [input, masked]
[Connect] / [Disconnect]
Link: "Get Auth Key at admin.tailscale.com"
```

**Dependencies:**

```
tailscale              # system package on host
```

**Tests:**

```python
# test: get_status parses tailscale status --json (mock subprocess)
# test: connect calls tailscale up with auth key
# test: disconnect calls tailscale logout
# test: remote.connected event on successful connection
```

---

## Full system modules table

| # | Module | Type | ui_profile | Memory | CPU | Description |
|---|--------|------|------------|--------|-----|-------------|
| 1 | scheduler | SYSTEM | SETTINGS_ONLY | 64 MB | 0.15 | Scheduler: cron, interval, sunrise/sunset |
| 2 | device_watchdog | SYSTEM | ICON_SETTINGS | 64 MB | 0.1 | Device availability monitoring |
| 3 | protocol_bridge | SYSTEM | FULL | 256 MB | 0.3 | MQTT / Zigbee / Z-Wave / HTTP gateway |
| 4 | automation_engine | SYSTEM | FULL | 128 MB | 0.3 | Automation engine (if X -> then Y) |
| 5 | presence_detection | SYSTEM | FULL | 64 MB | 0.15 | ARP/BT/GPS presence detection |
| 6 | weather_service | SYSTEM | FULL | 64 MB | 0.1 | Weather (open-meteo, no API key) |
| 7 | energy_monitor | SYSTEM | FULL | 64 MB | 0.1 | Energy consumption monitoring |
| 8 | notification_router | SYSTEM | SETTINGS_ONLY | 64 MB | 0.1 | Notification router |
| 9 | update_manager | SYSTEM | FULL | 64 MB | 0.1 | OTA updates with SHA256 verification |
| 10 | device_control | SYSTEM | FULL | 64 MB | 0.1 | Smart device manager + Tuya Smart Life wizard (owns `device.on/off`) |
| 11 | media_player | SYSTEM | FULL | 128 MB | 0.5 | Media player: radio, USB, SMB |
| 12 | voice_core | SYSTEM | FULL | 256 MB | 0.5 | STT (Vosk) / TTS / Wake-word / Speaker ID |
| 13 | llm_engine | SYSTEM | SETTINGS_ONLY | 512+ MB | 1.0+ | 4-tier Intent Router + Ollama LLM |
| 14 | secrets_vault | SYSTEM | SETTINGS_ONLY | 64 MB | 0.1 | AES-256-GCM vault + OAuth + proxy |
| 15 | user_manager | SYSTEM | FULL | 128 MB | 0.2 | Profiles / PIN / Face ID / Voice ID |
| 16 | hw_monitor | SYSTEM | ICON_SETTINGS | 32 MB | 0.05 | CPU / RAM / Disk monitoring |
| 17 | network_scanner | SYSTEM | FULL | 64 MB | 0.3 | ARP / mDNS / SSDP scanner |
| 18 | ui_core | SYSTEM | -- | 96 MB | 0.2 | PWA server :80 + Wizard + proxy |
| 19 | backup_manager | SYSTEM | SETTINGS_ONLY | 96 MB | 0.3 | Local + E2E cloud backup |
| 20 | notify_push | SYSTEM | SETTINGS_ONLY | 32 MB | 0.1 | Web Push VAPID notifications |
| 21 | remote_access | SYSTEM | SETTINGS_ONLY | 32 MB | 0.15 | Tailscale VPN remote access |

**Total RAM consumption (all 21 modules):** ~1.8 GB (without LLM model) / ~4 GB (with LLM phi3:mini)

---

## Related documents

```
docs/architecture.md              <- core components
docs/module-bus-protocol.md       <- module bus, lifecycle, WebSocket protocol
docs/module-development.md        <- SDK, manifest, permissions
docs/deployment.md                <- Raspberry Pi deployment
.github/CONTRIBUTING.md           <- code standards
```
