# SmartHome LK Core — Technical Specification v0.3-beta
**Date:** 2026-03-20 | **License:** Open Source (MIT) | **Status:** DRAFT

---

## Table of Contents

1. Introduction and Concept
2. Architecture: 2-Container Scheme
3. Module Classification
4. Core System Modules
5. First Launch — Onboarding Wizard
6. OS and UI Modes
7. Voice Assistant and LLM
8. Audio Subsystem
9. Users, Authorization, Audit
10. Network, Security, Remote Access
11. Notifications
12. Import from Existing Systems
13. Resource Monitoring and Degradation
14. Python SDK for Module Developers
15. Offline Mode
16. Definition of Done
17. Out of Scope

---

## 1. Introduction and Concept

SmartHome LK Core is an open source (MIT) local smart home hub. It is installed on a Raspberry Pi 4/5 or any SBC running Linux. No subscription is required for basic operation. It integrates with the SmartHome LK platform for cloud features, module marketplace, and remote management.

### 1.1 Three Fundamental Principles

**The core is immutable** — core files are protected by a SHA256 reference hash and the `chattr +i` flag. Modification is impossible without an explicit update through the official platform channel.

**Modules are isolated** — all third-party logic runs in isolated Python threads inside the modules container. Communication with the core is only through the Core API (HTTP, localhost:7070). Direct access to core data and the `/secure` partition is prohibited.

**The agent watches** — an independent `IntegrityAgent` process continuously verifies SHA256 hashes of core files and responds through a chain: stop modules → notify platform → rollback from backup → SAFE MODE.

### 1.2 Open Source

The project is distributed under the MIT license. UPS/backup power, custom hardware configurations — at the community's discretion. This specification defines the base core functionality.

---

## 2. Architecture: 2-Container Scheme

Instead of a separate Docker container per module, a minimal scheme is used.

| Container | RAM | Purpose |
|---|---|---|
| `smarthome-core` | ~420 MB | Core: FastAPI, Device Registry, Event Bus, Module Loader, Cloud Sync, Voice Core, LLM Engine, UI Core |
| `smarthome-modules` | 180–350 MB | All user modules in a single Python process via Plugin Manager |
| `smarthome-sandbox` | 96–256 MB, `--rm` | Temporary: test a new module before installation. Auto-deleted. |

### 2.1 Plugin Manager

Plugin Manager is a core component that loads modules as Python classes via `importlib` into an isolated namespace with a dedicated thread (Thread).

- Module crash (Exception) → caught, logged, only that module is restarted
- OOM / segfault → the entire `smarthome-modules` container crashes → systemd automatically restarts it
- Hot-reload: module update via `importlib.reload()` without container restart
- Per-module memory limit: `resource.setrlimit` + `tracemalloc` monitoring inside the thread

### 2.2 Watchdog — Two-Level Protection

- **Level 1 — systemd**: `smarthome-core.service` and `smarthome-modules.service` with `Restart=always`, `RestartSec=5s`
- **Level 2 — Docker**: `--restart=unless-stopped` on both containers
- **Integrity Agent**: separate `smarthome-agent.service`, independent of both containers

### 2.3 Memory Savings vs Separate Containers

| Configuration | RAM (typical load) |
|---|---|
| One container per module (8 modules) | ~1,200 MB |
| 2-container scheme (same 8 modules) | ~620 MB |
| Savings | ~580 MB (−48%) |

---

## 3. Module Classification

### 3.1 Module Types

| Type | Removable? | Description |
|---|---|---|
| `SYSTEM` | No | Shipped with the core. Extended privileges. Runs in the core process. |
| `UI` | Yes | Icon in menu + widget on dashboard + settings page. iframe sandbox. |
| `INTEGRATION` | Yes | External services via OAuth/API. Tokens in the core, module cannot see them directly. |
| `DRIVER` | Yes | Protocol driver: Zigbee, Z-Wave, MQTT, HTTP devices. |
| `AUTOMATION` | Yes | Scenarios without UI. Event listeners + scheduler. Lightest (~40 MB). |
| `IMPORT_SOURCE` | Yes | Import from Home Assistant, Tuya, Philips Hue, and other systems. |

### 3.2 UI Profiles

| Profile | Components | Example |
|---|---|---|
| `HEADLESS` | No UI | Night mode, alarm |
| `SETTINGS_ONLY` | Settings page | System module voice-core |
| `ICON_SETTINGS` | Icon + settings | Gmail integration |
| `FULL` | Icon + widget + settings | Climate module, lighting module |

### 3.3 Runtime Modes (manifest.json)

- `always_on` — running constantly. UI modules, drivers, Telegram notifications.
- `on_demand` — thread starts in ~50 ms, performs the task, stops. AUTOMATION.
- `scheduled` — cron string in manifest. Example: `"*/5 * * * *"` to check Gmail every 5 minutes.

### 3.4 manifest.json — Structure

```yaml
name:          my-module          # unique name (snake_case)
version:       1.0.0              # semver
type:          INTEGRATION        # module type
ui_profile:    ICON_SETTINGS      # UI profile
api_version:   "1.0"
runtime_mode:  scheduled
schedule:      "*/5 * * * *"
permissions:
  - device.read
  - events.subscribe
port:          8100               # module HTTP server port

# If ui_profile != HEADLESS:
ui:
  icon:     icon.svg
  widget:
    file:   widget.html
    size:   "2x1"                 # 1x1 | 2x1 | 2x2 | 4x1
  settings: settings.html

# If type is INTEGRATION:
oauth:
  provider: google                # google | telegram | custom
  scopes:
    - gmail.readonly
```

### 3.5 UI Component Security

- All widgets and settings pages are rendered in `<iframe sandbox>` — the module has no access to the core DOM
- Communication only through `window.postMessage` with a whitelist of allowed message types
- CSP header: `default-src 'self'` — inline scripts are prohibited

---

## 4. Core System Modules

| Module | UI Profile | Function |
|---|---|---|
| `voice-core` | SETTINGS_ONLY | STT (Whisper.cpp), TTS (Piper), wake-word, speaker ID, privacy mode |
| `llm-engine` | SETTINGS_ONLY | Ollama, Intent Router (Fast Matcher + LLM), model selection and download |
| `network-scanner` | SETTINGS_ONLY | ARP sweep, mDNS, SSDP/UPnP, Zigbee/Z-Wave, OUI classification |
| `user-manager` | SETTINGS_ONLY | Profiles, roles, voice prints, video authorization, audit log |
| `secrets-vault` | HEADLESS | AES-256-GCM OAuth token storage, proxy for modules |
| `backup-manager` | SETTINGS_ONLY | Local backup (USB/SD) + E2E cloud, QR secrets transfer |
| `remote-access` | HEADLESS | Tailscale VPN client: auto-connect, tunnel status |
| `hw-monitor` | HEADLESS | CPU temperature, RAM, disk. Alert + automatic load reduction on overheating |
| `notify-push` | HEADLESS | Web Push VAPID — phone notifications when browser is closed |
| `ui-core` | FULL | PWA · smarthome.local:80 · TTY1/kiosk · first launch wizard |

---

## 5. First Launch — Onboarding Wizard

Goal: a user without technical knowledge sets up the system in 5–10 minutes using only a phone.

### 5.1 Step 0 — Before Powering On: Writing the Image to SD

- A ready-made `.img` image (SmartHome LK OS Lite) is downloaded from the platform website
- Written via Raspberry Pi Imager or balenaEtcher — no additional configuration required
- Image: Raspberry Pi OS Lite + Docker + smarthome-core pre-installed

### 5.2 Step 1 — First Power-On: Access Point + QR

On first start (or if Wi-Fi is not configured) the core creates an access point:

```
SSID:     SmartHome-Setup
Password: smarthome
```

**If an HDMI display is connected:**
→ QR code is displayed on TTY1
→ Scan → opens the wizard in the phone browser

**If there is no display (headless):**
→ Connect to SmartHome-Setup from the phone
→ Open browser → `192.168.4.1`
→ Same wizard

mDNS fallback: `http://smarthome-setup.local`

### 5.3 Wizard — 9 Steps in the Phone Browser

| # | Step | Details |
|---|---|---|
| 1 | **Interface language** | Choose: ru / uk / en. Affects all text and TTS voices. |
| 2 | **Wi-Fi network** | List of discovered networks. Enter password. Pi connects and checks internet. |
| 3 | **Device name** | E.g. "Smart Home — Kitchen". Displayed on the platform and in voice responses. |
| 4 | **Timezone** | Choose from a list or auto-detect by IP. |
| 5 | **STT voice model** | Whisper tiny (fast, Pi 4) / base (balanced) / small (quality, Pi 5). Downloaded. |
| 6 | **TTS voice (Piper)** | Voice list for the selected language. "Listen" button. Downloads ~50 MB. |
| 7 | **First user** | Admin name, 4–8 digit PIN. Optional: voice print (5 phrases). |
| 8 | **Platform registration** | QR or link. Optional — can be skipped, works fully locally. |
| 9 | **Import (optional)** | Home Assistant / Tuya / Philips Hue. OAuth via link. Can be skipped. |

### 5.4 "What's Next" Screen After the Wizard

- Connect devices → `/discovery` (network scanner)
- Install modules → `/modules/install` (marketplace)
- Configure voice assistant → `/settings/voice`
- Add the app to your home screen → "Install PWA" button
- Documentation and videos → `docs.smarthome-lk.com`

---

## 6. OS and UI Modes

### 6.1 Recommended Operating Systems

| OS | RAM idle | Recommendation |
|---|---|---|
| **Raspberry Pi OS Lite** | ~150 MB | ✅ Recommended. Official, best Pi hardware support. |
| **DietPi** | ~90 MB | ✅ Recommended. Minimalist, built-in Docker installer. |
| Armbian | ~170 MB | For third-party SBCs (Orange Pi, NanoPi, Rock Pi). |
| Ubuntu Server 24.04 | ~240 MB | Alternative if the Ubuntu ecosystem is needed. |
| Raspberry Pi OS Desktop | ~500 MB | ⚠️ Only if a desktop is needed. ~350 MB lost. |

### 6.2 UI Mode Auto-Detection at Startup

The `:80` web server runs in all modes at all times. The local display is an additional client.

| Mode | Condition | Description |
|---|---|---|
| `HEADLESS` | No HDMI | Web server only. Access: smarthome.local:80 + Tailscale. |
| `KIOSK` | X11/Wayland + HDMI | `chromium --kiosk http://localhost:80` on top of the desktop. |
| `FRAMEBUFFER` | Lite OS + HDMI + Chromium | `chromium --ozone-platform=drm` without X11, directly to framebuffer. |
| `TTY` | Lite OS + HDMI, no Chromium | Python Textual TUI (~15 MB) on TTY1. Status + navigation. |

Auto-detection algorithm (`core/ui_detector.py`):

```python
def detect_display_mode() -> str:
    # 1. Is X11/Wayland available?
    if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'):
        return 'kiosk'
    # 2. Is HDMI connected?
    hdmi = Path('/sys/class/drm').glob('*/status')
    if any('connected' in p.read_text() for p in hdmi):
        if shutil.which('chromium-browser'):
            return 'framebuffer'
        return 'tty'
    # 3. No display
    return 'headless'
```

### 6.3 PWA (Progressive Web App)

- `manifest.json` + Service Worker: ui-core supports installation as a PWA
- **Offline page**: when there is no connection to the Pi, the last state from cache is shown
- **Icon** on the phone's home screen: native look without browser chrome
- **Web Push VAPID**: phone notifications even when the browser is closed (via `notify-push`)

### 6.4 UI Configuration (core.yaml)

```yaml
ui:
  web_port: 80
  display_mode: auto        # auto | headless | kiosk | framebuffer | tty
  mdns_announce: true       # smarthome.local
  tty_device: /dev/tty1
  framebuffer: /dev/fb0
  https: true               # self-signed certificate
```

---

## 7. Voice Assistant and LLM

### 7.1 voice-core Components

| Component | Stack | Characteristics |
|---|---|---|
| Wake-word | openWakeWord | < 5% CPU, always in background, customizable wake word |
| STT | Whisper.cpp tiny/base/small | Selected in wizard. Local only, no internet required. |
| TTS | Piper neural | Voice selection in wizard with preview. Offline. Latency ~300ms. |
| Speaker ID | resemblyzer | Enrollment: 5 phrases → 256-float d-vector in `/secure/biometrics/` |
| Privacy mode | GPIO + voice | Physical GPIO button **OR** command "Home, quiet" → microphone disabled |

### 7.2 Voice Request Pipeline

```
openWakeWord → hears wake-word
      ↓
Audio recording (until 1.5 sec pause)
      ↓
Whisper.cpp → query text                ~0.8–2 sec
      ↓
Speaker ID: who is speaking?            ~200 ms
      ↓
Intent Router — Level 1: Fast Matcher   ~50 ms
      ↓ not found
Intent Router — Level 2: LLM (Pi 5)    ~3–8 sec
      ↓
Found module → Core API → execution
Not found   → TTS: "No such module. Search the marketplace?"
      ↓
Piper TTS → response playback          ~300 ms
      ↓
Record in dialog history (SQLite)
```

### 7.3 Intent Router — Two Levels

**Level 1 — Fast Matcher (< 50ms, works on both Pi 4 and Pi 5)**
- Keyword/regex rules for frequent commands
- Configured in YAML: `"turn on the light" → lights.on`
- No LLM — instant

**Level 2 — LLM Intent (3–8 sec, Pi 5 with 8GB RAM only)**
- Ollama with phi-3-mini model (3.8B int4) or gemma-2b
- System prompt contains a dynamic registry of installed modules
- Registry is rebuilt on each module install/remove
- Returns JSON: `{ intent, module, params, confidence }`
- If `confidence < 0.7` → asks to repeat
- Auto-disables when free RAM < 5GB

### 7.4 Voice Input via Client Browser

- `getUserMedia()` → WebSocket → Pi: audio is streamed in 100ms chunks
- Pi: `ffmpeg` → WAV 16kHz → Whisper.cpp → Intent Router → Piper TTS → WAV response
- Nothing goes to the cloud — the entire pipeline runs locally on the Pi
- Client microphone auto-detection: `enumerateDevices()` — if none, the PTT button is hidden

### 7.5 Language Settings

- Interface language and TTS voice language are selected independently
- Supported beta languages: `ru`, `uk`, `en`
- Adding a language = downloading a Piper language pack (~50 MB) via `/settings/voice`
- LLM system prompt is sent in the active user's language

### 7.6 Biometrics — Absolute Restriction

> **Voice prints (d-vector) and face embeddings are stored ONLY in `/secure/biometrics/` on the device. Cloud synchronization is blocked at the core level. This restriction is not configurable and cannot be lifted by any platform command.**

---

## 8. Audio Subsystem

### 8.1 Microphone Sources (Auto-Detection Priority)

| Type | Interface | Notes |
|---|---|---|
| USB microphone | USB | Plug & play. Priority 1. |
| ReSpeaker HAT | I2C/SPI | Multi-channel. Requires `seeed-voicecard`. Priority 2. |
| I2S GPIO (INMP441, SPH0645) | GPIO 18–21 | `dtoverlay` in `/boot/config.txt`. Priority 3. |
| Bluetooth | PulseAudio + bluez | Latency ~150ms. Pairing via ui-core. Priority 4. |
| HDMI (ARC) | HDMI | Rarely used. Priority 5. |

### 8.2 Speaker Sources

| Type | Interface | Notes |
|---|---|---|
| USB sound card | USB | Plug & play. Best quality. Priority 1. |
| I2S DAC HAT (HiFiBerry etc.) | GPIO | `dtoverlay`. High quality. Priority 2. |
| Bluetooth speaker | BT | Pairing via ui-core. MAC is saved for auto-reconnect. Priority 3. |
| HDMI (monitor speakers) | HDMI | Auto-detect. Priority 4. |
| 3.5mm jack | Analog | Built into Pi. Medium quality. Priority 5. |

### 8.3 Configuration (core.yaml)

```yaml
audio:
  input_priority:  [usb, i2s_gpio, bluetooth, hdmi, builtin]
  output_priority: [usb, i2s_gpio, bluetooth, hdmi, jack]
  force_input:  null          # or "hw:2,0" to override
  force_output: null          # or "bluez_sink.AA_BB_CC"
  i2s_overlay:  null          # "googlevoicehat" | "hifiberry-dacplus" | ...
  bluetooth_sink: null        # BT speaker MAC address after pairing
```

### 8.4 /settings/audio Page in ui-core

- List of discovered devices with real-time signal level
- "Test microphone" button — 3 sec recording + playback
- "Test speaker" button — Piper speaks a test phrase
- Bluetooth: "Add device" → 30 sec scan → choose from list → pairing
- Bluetooth pairing flow: `bluetoothctl pair MAC → trust MAC → connect MAC`

---

## 9. Users, Authorization, Audit

### 9.1 Roles and Permissions

| Action | admin | resident | guest |
|---|---|---|---|
| Device management | Full | Full | Read only |
| Install/remove modules | Yes | No | No |
| Core settings and wizard | Yes | No | No |
| Voice commands | All | All | Limited |
| View audit log | Yes (all) | Own only | No |
| OAuth authorization for integrations | Yes | No | No |
| Tailscale management | Yes | No | No |

### 9.2 Authorization Methods in ui-core

- **PIN** (4–8 digits) — always available
- **Face ID** — if enrolled and the client has a camera. Browser captures a JPEG frame → POST → Pi face_recognition → JWT session. The photo is not saved.
- **Voice print** — identification during voice requests (command personalization, not UI login)

> **HTTPS is required** for `getUserMedia()`. Without it, the browser does not grant access to the camera and microphone. A self-signed certificate is generated automatically during initialization.

### 9.3 User Model (SQLite)

```sql
user_id        TEXT PRIMARY KEY   -- uuid4
name           TEXT               -- display name
role           TEXT               -- admin | resident | guest
pin_hash       TEXT               -- SHA256 PIN
voice_enrolled BOOLEAN
face_enrolled  BOOLEAN
lang           TEXT               -- ru | uk | en
created_at     REAL               -- unix timestamp
```

### 9.4 Audit Log

- Stored locally in SQLite. Accessible only by `admin`.
- What is logged: login/logout, voice commands (query text), settings changes, module install/remove, device management.
- Rotation: last 10,000 records.
- Page `/settings/audit` in ui-core: table with filters by user, action, date.

---

## 10. Network, Security, Remote Access

### 10.1 Tailscale — Remote Access from the Internet

Tailscale is installed as the `remote-access` system module. It creates an encrypted WireGuard tunnel without open ports on the router.

- Setup: in the wizard (step 8) or `/settings/remote` — QR code → `tailscale.com` → authorization
- After connecting, the Pi is accessible at `100.x.x.x` or via MagicDNS (`smarthome-kitchen.ts.net`)
- Free Tailscale plan: up to 100 devices, no traffic limits
- Status: `/settings/remote` → "Connected / Disconnected / Error"

### 10.2 Firewall — iptables Rules

```bash
# Core API — localhost and core_net only
iptables -A INPUT -p tcp --dport 7070 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 7070 -j DROP

# Web interface — local network + Tailscale
iptables -A INPUT -p tcp --dport 80 -s 192.168.0.0/16 -j ACCEPT
iptables -A INPUT -p tcp --dport 80 -s 100.0.0.0/8 -j ACCEPT  # Tailscale
iptables -A INPUT -p tcp --dport 80 -j DROP
```

The `/secure` partition is not accessible to modules — there is no `/secure` volume mount in the `smarthome-modules` container.

### 10.3 Rate Limiting

| Action | Limit | Consequence |
|---|---|---|
| Incorrect PIN | 5 attempts / 60 sec | 10-minute lockout, audit log entry |
| Core API requests | 100 / sec per token | HTTP 429 |
| WebSocket audio (STT) | 1 session per user | New connection rejected |

### 10.4 HTTPS and Certificates

- A self-signed certificate (mkcert) is automatically generated during initialization
- Issued for `smarthome.local`, `smarthome-setup.local`, and the device IP address
- The user can upload a custom certificate via `/settings/security`
- Without HTTPS — `getUserMedia()` is unavailable. This is a blocking requirement for voice and Face ID.

---

## 11. Notifications

### 11.1 Delivery Channels

| Channel | When it works | Implementation |
|---|---|---|
| TTS voice | Always (Pi at home) | Piper → ALSA/BT. Priority: critical alerts. |
| SSE in browser | While browser is open | EventSource in ui-core. Real-time status. |
| Web Push VAPID | Browser closed, phone online | Service Worker on the phone. `notify-push` module. |
| Telegram bot | Telegram installed | `INTEGRATION` module. Authorization via Bot API. |

### 11.2 Notification Priorities

| Level | Examples | Channels |
|---|---|---|
| `CRITICAL` | Fire sensor, leak, intrusion | TTS immediately + Push + Telegram |
| `HIGH` | Battery < 10%, Pi overheating | Push + Telegram |
| `NORMAL` | Task completed, module updated | SSE in browser |
| `INFO` | Light turned off, door closed | History only (no push) |

---

## 12. Import from Existing Systems

### 12.1 Supported Systems (Beta)

| System | Authorization | What is imported |
|---|---|---|
| **Home Assistant** | OAuth2 + server URL | Devices, rooms, automations (simple), scenes |
| **Tuya / SmartLife** | OAuth2 via link → QR in app | Devices, rooms, DP command codes |
| **Philips Hue** | Press button on Bridge (local) | Lights, groups, scenes. No internet required. |
| Samsung SmartThings | OAuth2 smartthings.com | Devices, rooms |
| IKEA TRÅDFRI | PSK auto-generation | Lights, groups, blinds |
| MQTT Broker | host + login + password | Topics as devices |

### 12.2 Import Process (4 Steps in ui-core)

1. **Choose source** — cards with logos, "Popular" badge on the first three
2. **Authorization** — OAuth: button → redirect → callback. Bridge button: 30 sec timer. PSK: form.
3. **Preview** — checkboxes by group: Lighting / Climate / Security / Automations
4. **Progress** — SSE stream: "Importing 12/20...", "Converting automations 3/8 (5 need manual adjustment)"

### 12.3 Bridge Modules

After import, devices are managed through a bridge module — it translates Core API commands back to the original system with bidirectional state synchronization.

- `ha-bridge` — WebSocket sync with Home Assistant in real time
- `tuya-bridge` — Tuya Open API + push via Tuya MQTT

> **Automations:** simple ones (`if X → Y`) are fully converted. Complex ones (Jinja2 templates, scripts) → drafts marked "needs manual adjustment".

### 12.4 Conversion Format → Device Registry

```json
{
  "device_id": "uuid-auto",
  "name": "Living Room Light",
  "type": "actuator",
  "protocol": "home_assistant",
  "state": { "on": true, "brightness": 80 },
  "capabilities": ["turn_on", "turn_off", "set_brightness"],
  "meta": {
    "import_source": "home_assistant",
    "ha_entity_id": "light.living_room",
    "ha_area": "Living Room",
    "imported_at": "2026-03-20T10:00:00Z"
  },
  "module_id": "ha-bridge"
}
```

---

## 13. Resource Monitoring and Degradation

### 13.1 hw-monitor — System Module

- Every 30 sec: CPU temperature (`/sys/class/thermal`), RAM (`free`), disk (`df`), uptime
- Data is included in heartbeat pings to the SmartHome LK platform
- Charts for the last 24 hours on the `/settings/system` page in ui-core

### 13.2 Thresholds and Automatic Responses

| Metric | Threshold | Action |
|---|---|---|
| CPU temperature | > 80°C | ⚠️ WARN alert to user + platform notification |
| CPU temperature | > 90°C | 🔴 Stop LLM Engine + CRITICAL alert |
| Free RAM | < 300 MB | Block installation of new modules |
| Free RAM | < 150 MB | Stop AUTOMATION → stop INTEGRATION → warning |
| Free disk | < 500 MB | Warning |
| Free disk | < 100 MB | Stop backup |

### 13.3 RAM Shortage Degradation Strategy

1. Warn user in ui-core + block installation of new modules
2. When RAM < 150 MB: auto-stop by priority — AUTOMATION first, then INTEGRATION
3. UI modules and DRIVER modules — only with explicit user permission
4. SYSTEM modules are never stopped (exception: LLM Engine on CPU overheating > 90°C)

---

## 14. Python SDK for Module Developers

### 14.1 Installation

```bash
pip install smarthome-sdk
```

### 14.2 Base Module Class

```python
from smarthome_sdk import SmartHomeModule, on_event, schedule

class MyModule(SmartHomeModule):
    name = "my-climate-module"
    version = "1.0.0"

    async def on_start(self):
        self.logger.info("Module started")

    @on_event("device.state_changed")
    async def handle_state(self, event):
        device = await self.devices.get(event.device_id)
        if device.state.get("temperature") > 25:
            await self.devices.set_state(device.id, {"fan": True})

    @schedule("*/5 * * * *")
    async def periodic_check(self):
        devices = await self.devices.list(type="sensor")

    async def on_stop(self):
        pass  # graceful shutdown
```

### 14.3 CLI Commands

```bash
smarthome new-module my-integration   # create module structure
smarthome dev                         # start mock Core API on :7070
smarthome test my-module.zip          # sandbox test
smarthome publish                     # submit to marketplace
```

### 14.4 New Module Structure (Scaffold)

```
my-integration/
  manifest.json
  main.py
  test_module.py
  widget.html          # if ui_profile != HEADLESS
  settings.html        # if ui_profile != HEADLESS
  icon.svg
  Dockerfile
  README.md
```

### 14.5 Mock Core API for Local Development

```bash
smarthome dev
# Starts a mock server on localhost:7070
# Supports all Core API v1 endpoints
# Pre-populated with test devices
# Logs all requests to the console
```

### 14.6 API Documentation

- Swagger UI: `http://smarthome.local:7070/docs` (auto-generated by FastAPI)
- Public documentation: `docs.smarthome-lk.com/module-sdk`

---

## 15. Offline Mode

> **The base scenario "managing the home by voice and through the UI" works fully without the internet. The cloud is an optional extension, not a mandatory dependency.**

| Feature | Without internet | Notes |
|---|---|---|
| Voice assistant (STT/TTS) | ✅ Yes | Whisper + Piper — fully local |
| LLM Intent Router | ✅ Yes | Ollama locally on Pi 5 |
| Device Registry | ✅ Yes | Local SQLite |
| Automations | ✅ Yes | Local devices |
| Web interface :80 | ✅ Yes | Local network |
| Dialog history | ✅ Yes | Local SQLite |
| Tailscale (remote access) | ❌ No | Requires internet for the tunnel |
| Cloud Sync with platform | ⚠️ Partial | Buffers, sends on reconnect |
| OAuth integrations (Gmail, Tuya) | ❌ No | Cloud-dependent services |
| Module updates from marketplace | ❌ No | Requires internet |
| Web Push notifications | ❌ No | FCM requires internet |

---

## 16. Definition of Done v0.3

### 16.1 Onboarding

- [ ] A ready-made .img image can be written to SD and boots without additional configuration
- [ ] Pi creates AP `SmartHome-Setup` on first start. QR on HDMI if connected.
- [ ] Wizard completes all 9 steps in the phone browser without errors
- [ ] After the wizard, a "What's Next" screen with three recommendations is shown

### 16.2 Core and Modules

- [ ] 2-container scheme works. Sandbox container auto-deletes after testing.
- [ ] Crash of one module does not stop the others (test: `kill -9` on the module thread)
- [ ] Watchdog: systemd + Docker automatically restart crashed containers
- [ ] Integrity Agent detects core file changes within ≤ 30 sec

### 16.3 Voice and LLM

- [ ] STT works without internet (test: `ip link set eth0 down` → command is recognized)
- [ ] TTS speaks the response locally via Piper
- [ ] Privacy mode: GPIO button AND voice command disable the microphone
- [ ] Fast Matcher processes registered commands in < 50ms
- [ ] Biometrics are absent from any outgoing HTTP requests (test via `tcpdump`)

### 16.4 UI and Access

- [ ] PWA installs to the phone's home screen. Offline page shows cache.
- [ ] Tailscale tunnel is configured via ui-core. Pi is accessible via MagicDNS.
- [ ] All 4 UI modes (HEADLESS/KIOSK/FRAMEBUFFER/TTY) work correctly
- [ ] HTTPS: self-signed certificate, `getUserMedia()` is available

### 16.5 Security

- [ ] Core API :7070 is not accessible from outside localhost (test via external IP)
- [ ] 5 incorrect PINs → 10-minute lockout, audit log entry
- [ ] Audit log stores actions. Accessible only by `admin`.
- [ ] RAM degradation: AUTOMATION stops when free RAM < 150 MB

### 16.6 SDK and Import

- [ ] `smarthome new-module` creates a working structure
- [ ] `smarthome dev` starts the mock Core API locally
- [ ] Import from Home Assistant: devices and simple automations
- [ ] OAuth QR flow completes successfully for Tuya and Home Assistant

---

## 17. Out of Scope — Beyond the Beta

| Not included | Planned |
|---|---|
| GPG signing of core image | v0.4 |
| Multi-hub (cluster of several Pis) | v0.5 |
| Built-in Video Doorbell | v0.4 |
| OTA updates on schedule without platform command | v0.5 |
| UPS / backup power | Community module |
| Prometheus/Grafana monitoring | Community module |
| Z-Wave natively in core | v0.4 (only via DRIVER module) |
| Apple HomeKit natively | v0.5 |
| Mobile app (iOS/Android native) | v1.0 |

---

*SmartHome LK · Core TZ v0.3.0-beta · 2026-03-20 · Open Source / MIT*
