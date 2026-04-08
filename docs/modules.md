# SelenaCore System Modules Reference

SelenaCore ships with **24 built-in SYSTEM modules**. They run in-process inside the unified Core (`:80`) — no separate containers, no extra ports — and communicate exclusively through the EventBus.

This document is a quick reference. For deep dives, see the linked specialized docs.

## Index

| Module                 | Widget | Voice intents | Doc |
|------------------------|--------|---------------|-----|
| [voice_core](#voice_core) | 1×1 | privacy mode | [voice-settings.md](voice-settings.md) |
| [llm_engine](#llm_engine) | — | — | [architecture.md](architecture.md#intent-system) |
| [ui_core](#ui_core) | — | — | [ui-sync-architecture.md](ui-sync-architecture.md) |
| [device_control](#device_control) | — | `device.on`, `device.off`, `device.set_temperature`, `device.set_mode`, `device.set_fan_speed` | [provider-system-and-modules.md](provider-system-and-modules.md) |
| [climate](#climate) | 2×2 | — (delegates to device-control) | [climate-and-gree.md](climate-and-gree.md) |
| [lights_switches](#lights_switches) | 2×2 | — (delegates to device-control) | [provider-system-and-modules.md](provider-system-and-modules.md) |
| [energy_monitor](#energy_monitor) | 1×1 | 2 intents | — |
| [automation_engine](#automation_engine) | 2×1 | 4 intents | — |
| [update_manager](#update_manager) | 2×1 | — | — |
| [scheduler](#scheduler) | — | — | — |
| [user_manager](#user_manager) | — | — | [user-manager-auth.md](user-manager-auth.md) |
| [secrets_vault](#secrets_vault) | — | — | — |
| [hw_monitor](#hw_monitor) | — | — | — |
| [media_player](#media_player) | 2×1 | 14 intents | — |
| [protocol_bridge](#protocol_bridge) | 2×1 | — | — |
| [weather_service](#weather_service) | 2×2 | 3 intents | — |
| [presence_detection](#presence_detection) | 2×1 | 3 intents | — |
| [device_watchdog](#device_watchdog) | 1×1 | 2 intents | — |
| [notification_router](#notification_router) | 2×1 | — | — |
| [notify_push](#notify_push) | — | — | — |
| [network_scanner](#network_scanner) | — | — | — |
| [clock](#clock) | 1×1 | — | — |
| [backup_manager](#backup_manager) | — | — | — |
| [remote_access](#remote_access) | — | — | — |

---

## voice_core

**Type:** SYSTEM · **Widget:** 1×1

Voice subsystem: STT (Vosk), TTS (Piper), wake-word detection, speaker ID (resemblyzer), privacy mode (GPIO + voice command).

- **Subscribes:** `voice.speak`
- **Publishes:** `voice.wake_word`, `voice.recognized`, `voice.response`, `voice.speak_done`, `voice.privacy_on`, `voice.privacy_off`
- **Voice intents:** `privacy_on`, `privacy_off`

---

## llm_engine

**Type:** SYSTEM

Local LLM (Ollama) plus the 6-tier Intent Router. Hosts the Fast Matcher (YAML), the IntentCompiler, the IntentCache, and cloud LLM providers (OpenAI / Anthropic / Google / Groq).

- **Provides:** `IntentRouter` singleton consumed by every voice-aware module
- **Auto-disables** local LLM when free RAM < 5 GB

---

## ui_core

**Type:** SYSTEM

Serves the React SPA, the PWA manifest, the service worker, the onboarding wizard endpoints, and the framebuffer/TTY status display. The legacy `server.py` is now a stub — the SPA is mounted directly on the unified Core process at `:80`.

---

## device_control

**Type:** SYSTEM

Universal smart device manager. Owns the device CRUD endpoints and dispatches commands through a runtime-pluggable provider system.

- **Voice intents:** `device.on`, `device.off`, `device.set_temperature`, `device.set_mode`, `device.set_fan_speed`
- **Built-in providers:** `tuya_local`, `tuya_cloud`, `gree`, `mqtt`
- **Opt-in providers:** `philips_hue`, `esphome` (install from UI)
- **Publishes:** `device.registered`, `device.state_changed`
- **REST:** `/api/ui/modules/device-control/...` and `/api/v1/devices/*`

See [docs/providers.md](providers.md) for the user-facing intro and [docs/provider-system-and-modules.md](provider-system-and-modules.md) for internals.

---

## climate

**Type:** SYSTEM · **Widget:** 2×2

Presentation-only module: renders climate devices (A/C, thermostats) grouped by room. Does **not** own voice intents — commands are forwarded to `device-control` via in-process call. Subscribes to `device.state_changed` for cache invalidation.

- **Subscribes:** `device.registered` (with `entity_type=air_conditioner|thermostat`), `device.state_changed`

---

## lights_switches

**Type:** SYSTEM · **Widget:** 2×2

Mirrors the climate architecture for `entity_type ∈ {light, switch, outlet}`. Compact rows on the dashboard, full-control modal with Swift-style toggle, brightness slider (debounced 250 ms), colour temperature, RGB picker. No voice intents of its own.

- **Subscribes:** `device.registered`, `device.state_changed`

---

## energy_monitor

**Type:** SYSTEM · **Widget:** 1×1 (clickable)

Per-device power and kWh tracking. The settings page is a single filterable, sortable table; clicking the dashboard widget opens a full-screen modal with the same table.

- **Subscribes:** `device.registered` (auto-creates an energy source)
- **REST:** `GET /energy/devices/full` (Device join with power + today's kWh)

---

## automation_engine

**Type:** SYSTEM · **Widget:** 2×1

YAML rule engine. Triggers: time, event, device, presence. Actions: device commands, scenes, notifications.

---

## update_manager

**Type:** SYSTEM · **Widget:** 2×1

OTA updates from GitHub Releases. Checks daily at 03:00 (via `scheduler`), downloads the archive, verifies SHA256, snapshots the current version into `/secure/core_backup/`, applies via atomic rename, restarts through systemd.

---

## scheduler

**Type:** SYSTEM

Central task scheduler: cron, interval, sunrise/sunset triggers. Used by other modules (e.g. `update_manager`, `automation_engine`).

---

## user_manager

**Type:** SYSTEM

User profiles, device-token authentication, PIN/QR elevation gate, audit log. See [user-manager-auth.md](user-manager-auth.md).

---

## secrets_vault

**Type:** SYSTEM

AES-256-GCM secrets storage in `/secure/tokens/`. OAuth Device Authorization Grant flow (RFC 8628). API proxy that injects tokens server-side so modules never see raw credentials. Auto-refresh 5 minutes before expiry.

---

## hw_monitor

**Type:** SYSTEM

CPU temperature, RAM, disk, uptime — 30-second polling with throttle hooks for automatic load reduction.

---

## media_player

**Type:** SYSTEM · **Widget:** 2×1

Internet radio, USB, SMB, Internet Archive playback with cover art. The most voice-active module — 14 intents covering play/pause/stop/next/previous, volume, genre, station name, free-text query.

- **Publishes:** `media.state_changed`, `voice.speak`

---

## protocol_bridge

**Type:** SYSTEM · **Widget:** 2×1

Protocol gateway between MQTT / Zigbee / Z-Wave / HTTP and the Device Registry.

---

## weather_service

**Type:** SYSTEM · **Widget:** 2×2

Local weather conditions and forecast via Open-Meteo (no API key required). 3 voice intents (current, today, forecast).

---

## presence_detection

**Type:** SYSTEM · **Widget:** 2×1

Home/away detection via active L2 ARP scan (preferred), Bluetooth, and Wi-Fi MAC tracking. 5-minute "away" threshold protects against phone Deep Sleep false positives. See AGENTS.md §18 for the algorithm.

---

## device_watchdog

**Type:** SYSTEM · **Widget:** 1×1

Per-device availability monitoring: ICMP ping, MQTT/Zigbee heartbeat. Publishes `device.online` / `device.offline` events.

---

## notification_router

**Type:** SYSTEM · **Widget:** 2×1

Routes notifications to channels: TTS voice, Telegram, Web Push, HTTP webhook.

---

## notify_push

**Type:** SYSTEM

Web Push (VAPID) implementation. Used by `notification_router` for browser notifications.

---

## network_scanner

**Type:** SYSTEM

Active and passive device discovery: ARP sweep, mDNS / Bonjour, SSDP / UPnP, Zigbee via USB dongle. Includes OUI database lookup and auto-classification.

---

## clock

**Type:** SYSTEM · **Widget:** 1×1

Clock app: alarms, timers, reminders, world clock, stopwatch with voice control.

---

## backup_manager

**Type:** SYSTEM

Local USB / SD backup, E2E cloud backup (PBKDF2 + AES-256-GCM), QR-code secret transfer between devices.

---

## remote_access

**Type:** SYSTEM

Tailscale VPN client integration for secure remote access to the hub.

---

## See also

- [Architecture overview](architecture.md)
- [Provider system (user-facing)](providers.md)
- [Provider system & lights-switches (internals)](provider-system-and-modules.md)
- [Module development guide](module-development.md)
- [System module development](system-module-development.md)
- [UI Sync architecture](ui-sync-architecture.md)
