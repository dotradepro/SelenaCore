# Climate Control & Gree / Pular Air Conditioner Support

> Local-only WiFi A/C control for Gree-protocol units (Pular GWH12AGB-I-R32, Gree, Cooper&Hunter, EWT, Ewpe Smart family) plus a high-level **Climate** UI module that renders and controls climate devices grouped by room.
>
> No cloud account, no Home Assistant dependency, no httpx between system modules — pure in-process Python and the EventBus.
>
> **➡ See also:** [provider-system-and-modules.md](provider-system-and-modules.md) — the post-Gree refactor that turned device-control into a runtime-pluggable provider system, added the `lights-switches` SYSTEM module, unified the energy-monitor settings into one filterable table, and reworked the climate widget for compact dashboard tiles. The Gree driver and Climate module described below are still the canonical implementation; this document describes their initial design.

## 1. Overview

This feature adds two layers:

| Layer | Purpose | Module |
| --- | --- | --- |
| **Driver** | Speak the Gree LAN protocol (UDP/7000, AES-ECB) to one A/C unit | `device-control` (new `gree` driver) |
| **UI module** | Render every climate device grouped by room, dispatch commands | New `climate` SYSTEM module |

The two layers are deliberately decoupled:

- **device-control owns voice intents and the device registry.** New AC-specific intents (`device.set_temperature`, `device.set_mode`, `device.set_fan_speed`) live alongside the existing `device.on` / `device.off` family. They share one resolver and are scoped by `entity_type` filters so a "set temperature" command cannot accidentally hit a light bulb.
- **climate module is presentation-only.** It does **not** own any voice intent, does **not** poll the device, and does **not** speak HTTP. It subscribes to `device.state_changed` events for cache freshness and forwards user actions to `DeviceControlModule.execute_command()` via a direct in-process Python call (cross-module communication is allowed because both modules run inside the same `selena-core` container).
- **Energy/power data is owned by `energy_monitor`**, not climate. The Gree driver intentionally does not implement `consume_metering()`.

## 2. Architecture

```
                            ┌─────────────────────────┐
                            │   selena-core (single   │
                            │   Python process)       │
                            │                         │
        Voice intent ─────► │   device-control        │
        device.set_         │   ├─ _on_voice_intent   │
        temperature/mode    │   ├─ _resolve_device    │  ─┐
                            │   │  (entity_filter)    │   │
                            │   ├─ execute_command    │   │
                            │   └─ _watch_device      │   │
                            │       │                 │   │
                            │       ▼                 │   │
                            │   GreeDriver (gree.py)  │   │
                            │       │ greeclimate     │   │
                            │       ▼ UDP/7000 AES    │   │
                            │   ┌─────────────┐       │   │
                            │   │ Pular AC    │       │   │
                            │   └─────────────┘       │   │
                            │                         │   │
                            │   climate module        │   │
                            │   ├─ widget /rooms      │   │
                            │   ├─ apply_command()    ───┘
                            │   │  (in-process call)  │
                            │   └─ _on_state_event    │ ◄─ device.state_changed
                            │       (cache)           │   on EventBus
                            └─────────────────────────┘
```

Key invariants:

1. **One driver instance per device, one watcher coroutine per device.** Reconnect with exponential backoff on `DriverError`.
2. **Driver mutates `self.meta` in place** (Gree learns its per-device AES key on first `bind()`); `_persist_driver_meta()` writes the diff to the DB after every `connect()` so reboots skip the binding handshake.
3. **Cross-module call uses `get_sandbox().get_in_process_module("device-control")`** — no HTTP, no httpx, no port. The climate module caches its `device-control` reference lazily.
4. **Climate module never owns voice intents.** All voice routing is centralised in `device-control._on_voice_intent`.

## 3. Files added & modified

### Added

| Path | Purpose |
| --- | --- |
| [system_modules/device_control/drivers/gree.py](../system_modules/device_control/drivers/gree.py) | `GreeDriver(DeviceDriver)` — async wrapper around `greeclimate` |
| [system_modules/climate/__init__.py](../system_modules/climate/__init__.py) | Exports `module_class = ClimateModule` |
| [system_modules/climate/manifest.json](../system_modules/climate/manifest.json) | SYSTEM module manifest, no port |
| [system_modules/climate/module.py](../system_modules/climate/module.py) | `ClimateModule(SystemModule)` |
| [system_modules/climate/routes.py](../system_modules/climate/routes.py) | `/devices`, `/rooms`, `/device/{id}/command` |
| [system_modules/climate/widget.html](../system_modules/climate/widget.html) | 2x2 grid of per-room A/C cards |
| [system_modules/climate/settings.html](../system_modules/climate/settings.html) | Read-only diagnostic table |
| [system_modules/climate/icon.svg](../system_modules/climate/icon.svg) | Module icon |
| [tests/test_gree_driver.py](../tests/test_gree_driver.py) | 15 unit tests for the mappers |

### Modified

| Path | Change |
| --- | --- |
| [requirements.txt](../requirements.txt) | `greeclimate>=2.1` |
| [system_modules/device_control/drivers/registry.py](../system_modules/device_control/drivers/registry.py) | Register `"gree": GreeDriver`; entry in `list_driver_types()` |
| [system_modules/device_control/routes.py](../system_modules/device_control/routes.py) | `POST /gree/discover`, `POST /gree/import`; allow `gree` in `add_device` |
| [system_modules/device_control/settings.html](../system_modules/device_control/settings.html) | New "Gree / Pular" tab with Scan + Import flow; `air_conditioner` entity_type; full EN/UK i18n |
| [system_modules/device_control/module.py](../system_modules/device_control/module.py) | Climate intents declared in `_OWNED_INTENT_META`, `_intent_to_state()`, `_resolve_device(entity_filter=)` with composite tier-0 disambiguation, `_persist_driver_meta()`, `_claim_intent_ownership()` (inserts/claims rows on every start) |
| [system_modules/llm_engine/pattern_generator.py](../system_modules/llm_engine/pattern_generator.py) | `rebuild_composite_device_patterns()` produces a composite `device.set_temperature` regex with `(?P<name>...)` alternation of all climate devices |

## 4. Gree driver

### 4.1 Logical state schema

The driver translates between SelenaCore's logical key/value dict (stored in `Device.state` JSON) and the `greeclimate.device.Device` object.

| Key | Type | Range | Description |
| --- | --- | --- | --- |
| `on` | bool | — | Power |
| `mode` | str | `auto` / `cool` / `dry` / `fan` / `heat` | Operating mode |
| `target_temp` | int | 16–30 | Target temperature in °C (clamped) |
| `current_temp` | int | — | Current room temperature (read-only) |
| `fan_speed` | str | `auto` / `low` / `medium_low` / `medium` / `medium_high` / `high` | Fan speed |
| `swing_v` | str | `off` / `full` / `fixed_top` / `fixed_middle_top` / `fixed_middle` / `fixed_middle_bottom` / `fixed_bottom` / `swing_bottom` / `swing_middle` / `swing_top` | Vertical louver |
| `swing_h` | str | `off` / `full` / `left` / `left_center` / `center` / `right_center` / `right` | Horizontal louver |
| `sleep` | bool | — | Sleep mode |
| `turbo` | bool | — | Turbo mode |
| `light` | bool | — | Indoor unit display LED |
| `eco` | bool | — | Steady-heat / eco mode |
| `health` | bool | — | Anion / health mode |
| `quiet` | bool | — | Quiet mode |

### 4.2 `device.meta["gree"]` schema

```json
{
  "gree": {
    "ip": "192.168.1.50",
    "mac": "aa:bb:cc:dd:ee:ff",
    "name": "Bedroom AC",
    "port": 7000,
    "key": null,
    "brand": "gree",
    "model": "GWH12AGB"
  }
}
```

`key` is `null` until the first successful `bind()` — the driver writes the negotiated AES key back into this field, and `DeviceControlModule._persist_driver_meta()` flushes it to the DB so reboots skip the binding handshake.

### 4.3 Lifecycle

| Method | Behaviour |
| --- | --- |
| `connect()` | Build `Device(DeviceInfo(ip, port, mac, name))`, `await bind(key=...)`, persist new `device_key`, `update_state()`, return logical state. Wraps any exception in `DriverError`. |
| `set_state(state)` | Under `asyncio.Lock`, translate logical keys onto greeclimate attributes, `push_state_update()`. Clamps `target_temp` to 16–30 °C. Raises `DriverError` on unknown mode/fan/swing. |
| `get_state()` | Lock + `update_state()` + `_to_logical()`. |
| `stream_events()` | Gree devices do not push events. Loops with `POLL_INTERVAL_SECONDS = 5` and yields only when state actually changes (diffed against `_last_state`). Network failures raise `DriverError` to trigger watcher reconnect. |
| `disconnect()` | Idempotent — drops the `_device` reference (greeclimate holds no persistent socket). |
| `consume_metering()` | **Not overridden** — energy is `energy_monitor`'s responsibility. |

### 4.4 Driver registration

```python
# system_modules/device_control/drivers/registry.py
DRIVERS = {
    "tuya_local": TuyaLocalDriver,
    "tuya_cloud": TuyaCloudDriver,
    "mqtt": MqttBridgeDriver,
    "gree": GreeDriver,         # ← new
}
```

```python
# list_driver_types() — for the "Add device" UI dropdown
{
    "id": "gree",
    "name": "Gree / Pular WiFi A/C",
    "needs_cloud": False,
    "fields": ["gree.ip", "gree.mac", "gree.name"],
}
```

## 5. Discovery & onboarding

### 5.1 REST API

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| `POST` | `/api/ui/modules/device-control/gree/discover` | `{"timeout": 10}` (optional) | `{"devices": [{ip, mac, name, brand, model, version}, ...]}` |
| `POST` | `/api/ui/modules/device-control/gree/import` | `{"devices": [{ip, mac, name, location}, ...]}` | `{"created": [...], "skipped": [...]}` |

`/gree/discover` runs `greeclimate.Discovery().scan(timeout=10)` (with a fallback for the 1.x API where the keyword is positional). Best-effort: returns an empty list if `greeclimate` is not installed or the scan throws.

`/gree/import` creates `Device` rows with:

- `protocol = "gree"`
- `entity_type = "air_conditioner"`
- `capabilities = AC_CAPABILITIES` (`["on","off","set_temperature","set_mode","set_fan_speed","set_swing"]`)
- `enabled = True`
- `meta.gree = {ip, mac, name, port:7000, key:null, brand:"gree"}`

After insertion the watcher is spawned via `add_device_watcher()`, which performs the first `connect()` (and key negotiation). The new key is persisted by `_persist_driver_meta()` on the same code path.

### 5.2 UI flow

`device-control/settings.html` exposes a third tab **"Gree / Pular"** next to *Devices* and *Tuya Cloud Wizard*:

1. Click **Scan** → `POST /gree/discover` → spinner for 10 seconds.
2. Results table shows IP, MAC, brand/model. Each row has an **Import** checkbox (checked by default), an editable **Name** field, and an editable **Room** field.
3. Click **Import selected** → `POST /gree/import` with the picks → toast confirmation → tab switches to *Devices* showing the new entries.

All UI strings have full EN/UK translations under the standard `var L = {en:{}, uk:{}}` dictionary.

### 5.3 Manual entry

Manual entry through the existing `POST /devices` flow also works — set `protocol="gree"`, `entity_type="air_conditioner"`, and `meta={"gree": {"ip": ..., "mac": ..., "name": ...}}`. The watcher will bind on first connect.

## 6. Climate module

### 6.1 Manifest

```json
{
  "name": "climate",
  "type": "SYSTEM",
  "runtime_mode": "always_on",
  "permissions": ["device.read", "device.write", "events.subscribe", "events.publish"],
  "ui": {
    "icon": "icon.svg",
    "widget": {"file": "widget.html", "size": "2x2"},
    "settings": "settings.html"
  }
}
```

No `port`. SYSTEM modules run in-process inside `selena-core`.

### 6.2 Cross-module call

```python
# system_modules/climate/module.py
from core.module_loader.sandbox import get_sandbox

self._dc = get_sandbox().get_in_process_module("device-control")
await self._dc.execute_command(device_id, state)
```

The reference is cached lazily after the first lookup. If `device-control` is not loaded yet (startup race), `apply_command()` raises `RuntimeError` and the route returns `503`.

### 6.3 REST API

Mounted at `/api/ui/modules/climate/`:

| Method | Path | Returns |
| --- | --- | --- |
| `GET` | `/health` | `{status, module, cached_devices}` |
| `GET` | `/devices` | Flat list of every device with `entity_type` ∈ `{air_conditioner, thermostat}` |
| `GET` | `/rooms` | Same data grouped by `location` (`unassigned` bucket for blank locations) |
| `GET` | `/device/{id}` | Single device detail |
| `POST` | `/device/{id}/command` | Body `{state: {...}}`. Validates allowed keys (`ALLOWED_STATE_KEYS`) and forwards to `DeviceControlModule.execute_command()`. |

Allowed `state` keys: `on`, `mode`, `target_temp`, `fan_speed`, `swing_v`, `swing_h`, `sleep`, `turbo`, `light`, `eco`, `health`, `quiet`.

### 6.4 EventBus subscription

The module subscribes to `device.state_changed` and caches `payload["new_state"]` in `self._latest[device_id]`. `list_climate_devices()` merges DB state with the cached delta so widget reads are O(1) after the first load.

### 6.5 Widget

`widget.html` is a 2x2 dashboard tile:

- Devices grouped by room (location).
- Each card shows: device name, power button, current temperature, target temperature with `+`/`−` buttons (clamped to 16–30), mode chips (`auto`/`cool`/`dry`/`fan`/`heat`), fan-speed chips (`auto`/`low`/`medium`/`high`).
- Polls `GET /rooms` every 10 s + on `window.focus`.
- Reacts to the global `lang_changed` postMessage by re-rendering with the new locale.
- Full EN/UK i18n via `var L = {en:{}, uk:{}}`.

### 6.6 Settings page

`settings.html` is intentionally minimal — a read-only diagnostic table listing every detected climate device with room, name, type, protocol, on/off badge, and raw JSON state. There is **no** source-selection dropdown: every climate device is auto-listed by entity_type.

## 7. Voice commands

> Voice intents live in **device-control**, not climate. This avoids any pattern crossover with light/switch intents.

### 7.1 New intents

| Intent | Params | Example (EN) | Example (UK) |
| --- | --- | --- | --- |
| `device.set_temperature` | `level: int`, `location?: str` | "set temperature to 22 in bedroom" | "встанови температуру на 22 в спальні" |
| `device.set_mode` | `mode: enum(auto,cool,dry,fan,heat)`, `location?: str` | "switch bedroom to cool mode" | "перемкни спальню в режим охолодження" |
| `device.set_fan_speed` | `level: enum(auto,low,medium,high,min,max,...)`, `location?: str` | "set fan to high in bedroom" | "встанови вентилятор на високу в спальні" |

Aliases handled by the parser: `min/minimum → low`, `max/maximum → high`, `mid/middle → medium`, `cooling → cool`, `heating → heat`.

Patterns are no longer seeded by an external script. `device-control` declares these intents in `_OWNED_INTENT_META` and inserts/claims `intent_definitions` rows on every `start()` via `_claim_intent_ownership()`. The composite FastMatcher patterns (one regex per device verb, with `(?P<name>...)` alternation of every climate device's `meta.name_en`) are rebuilt by `PatternGenerator.rebuild_composite_device_patterns()` on every device CRUD. See [intent-routing.md §2](intent-routing.md#2-where-intents-come-from) for the full design.

### 7.2 Resolution

`DeviceControlModule._resolve_device(params, entity_filter=...)` selects exactly one target device. The resolver uses several tiers in order:

0. **Composite fast path** — if FastMatcher captured a unique `name_en` for an unambiguous device, the resolver loads it directly by `device_id`.
1. **Tier 0 disambiguation** — if FastMatcher captured a `name_en` that is shared by 2+ devices (same name in different rooms), the resolver matches `meta.name_en AND location` simultaneously.
2. **Strict (entity_type AND location)**
3. **Location-only** (`location` matches `device.location`, `device.name`, `meta.name_en`, or `meta.location_en`)
4. **Entity-only**
5. **Single-device fallback** (when there's exactly one device under management)

Climate intents pass `entity_filter=("air_conditioner","thermostat")` (or `("air_conditioner","fan")` for `device.set_fan_speed`) so the resolver narrows the candidate set before tier matching. This is what guarantees that "set temperature to 22" cannot accidentally route to a smart bulb or switch.

### 7.3 Intent ownership

`DeviceControlModule._claim_intent_ownership()` updates every row in `intent_definitions` listed in `OWNED_INTENTS` (`device.on`, `device.off`, `device.set_temperature`, `device.set_mode`, `device.set_fan_speed`, `device.query_temperature`, `device.lock`, `device.unlock`), setting `module="device-control"`. It then inserts any missing rows using defaults from `_OWNED_INTENT_META`. Idempotent — runs on every module start, no external seed script needed.

## 8. Meta persistence (Gree key)

Drivers that learn credentials during `connect()` (Gree's per-device AES key) mutate `self.meta` in place. The watcher loop calls `_persist_driver_meta(device_id, drv)` immediately after each successful `connect()`:

```python
async def _persist_driver_meta(self, device_id: str, drv: Any) -> None:
    new_json = json.dumps(drv.meta, sort_keys=True)
    async with self._db_session() as session:
        async with session.begin():
            d = await session.get(Device, device_id)
            current_json = json.dumps(json.loads(d.meta) if d.meta else {}, sort_keys=True)
            if current_json == new_json:
                return       # no-op when nothing changed
            d.set_meta(drv.meta)
```

The diff guard prevents needless DB writes on every reconnect cycle.

## 9. Verification

### 9.1 Unit tests

```bash
pytest tests/test_gree_driver.py -v
```

15 tests cover: capability list, temperature clamping (`_clamp_temp`), bidirectional enum maps round-trip, `_to_logical()` against a `MagicMock` greeclimate.Device, unknown-mode rejection, eco/health/quiet/light translation, and meta initialisation.

The tests stub `greeclimate` in `sys.modules` before importing the driver, so they pass even when the real package is not installed (e.g. in CI).

### 9.2 Regression tests

```bash
pytest tests/test_device_watchdog.py tests/test_energy_monitor.py -q
# 47 passed
```

### 9.3 End-to-end on hardware

1. `docker compose up -d --build` — rebuild because `requirements.txt` changed.
2. **Intent rows are auto-claimed by the module on first start** — no seed script needed.
3. **Discover**: Device Control → Gree / Pular → Scan → confirm Pular shows up → Import.
4. **Watcher**: `docker compose logs -f core` → expect `device.online` then `device.state_changed` every ~5 s.
5. **Direct control**: Device Control widget toggle on/off → physical AC reacts.
6. **Climate UI**: open Climate widget → card appears in correct room → +/−, mode, fan, power all reflected on the AC.
7. **Voice (uk)**: "встанови температуру на 24" → `voice.intent` → device-control resolves AC → physical change.
8. **Voice (en)**: "switch bedroom to cool mode" → resolves to bedroom AC only → mode changes.
9. **Restart persistence**: `docker compose restart core` → AC reconnects without re-binding (`meta.gree.key` survived).

## 10. Limitations & future work

- **Single climate source per command** — multi-room voice commands ("set every AC to 24") would need a "broadcast" path; out of scope for v1.
- **No scheduling / comfort profiles** — Climate module is presentation-only. Schedules belong in `automation-engine`.
- **No history view in v1** — `GET /history` is not implemented; raw data is in `state_history` and can be exposed later.
- **Pular firmware variants** — if discovery returns empty, capture LAN traffic with `tcpdump -i any udp port 7000` during the Gree+/Ewpe Smart app handshake to identify dialect drift. Manual entry via `POST /devices` always works as a fallback.
- **`greeclimate` API drift** — the driver targets the v2.x API. The OEM-specific attributes (`steady_heat`, `anion`, `quiet`) may differ on rebadged units; verify on hardware and adjust the `_to_logical` / `_apply_logical` mapping if needed.
