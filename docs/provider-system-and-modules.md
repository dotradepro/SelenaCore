# Provider System, Auto-routing & Lights-switches Module

> SelenaCore's post-Gree architectural cleanup. Turns device-control into a
> runtime-pluggable provider system, introduces a `lights-switches` SYSTEM
> module mirroring `climate`, unifies the energy-monitor settings page,
> and ships several widget UX fixes (compact rows, Swift toggles,
> click-reliability).

## 1. Goals

After the Gree / Climate work landed (commits `7b52286..75e5782`), the
device-control module pre-baked every smart-device library into the
container image. The next refactor (commits `9eb2b61..6ec879e`) addresses
six pain points at once:

1. **Each customer installs only the providers they need.** Tuya, Gree,
   Hue, ESPHome, Zigbee, MQTT — opt-in via a Providers tab in
   device-control settings. No rebuild, no container restart.
2. **Adding a device is one click.** When imported, it auto-routes to
   the right high-level module (climate / lights-switches) by
   `entity_type` AND auto-registers as an energy-monitor source. No
   manual wiring.
3. **Restart resilience.** Power loss, hard restart, container recreate
   all preserve provider state via the registry SQLite DB.
4. **The device-control widget disappears** from the dashboard. Its
   device-list view moves into energy-monitor (read-only stats with
   filters/sorting/click-to-modal).
5. **A new `lights-switches` SYSTEM module** mirrors the climate module
   for entity_types `light` / `switch` / `outlet`, with full control
   (on/off + brightness + colour temperature + RGB).
6. **The energy-monitor settings page** collapses 3 separate sections
   (Споживання, Порогові, Огляд) into one filterable, sortable table.

## 2. Provider system

### 2.1 Architecture

```
                        ┌──────────────────────────────────┐
                        │  device-control (SystemModule)   │
                        │                                  │
   user clicks Install  │  ProviderLoader                  │
   on "Philips Hue"  ─► │  ├─ install(provider_id)         │
                        │  │   └─ subprocess pip install   │
                        │  │   └─ importlib.import_module  │
                        │  │   └─ INSERT row in DB         │
                        │  │   └─ DRIVERS[id] = cls        │  ◄─ hot-load,
                        │  │                                │     no restart
                        │  ├─ load_enabled() (on startup)  │
                        │  │   └─ walk DB rows             │
                        │  │   └─ importlib.import_module  │
                        │  │   └─ ImportError → last_error │  ◄─ graceful
                        │  │                                │
                        │  └─ uninstall(provider_id)       │
                        │      └─ flip enabled=False or    │
                        │          pip uninstall + DELETE  │
                        │                                  │
                        │  drivers/registry.py             │
                        │  ├─ DRIVERS dict (mutated by     │
                        │  │  loader, never replaced)      │
                        │  └─ get_driver() lookup          │
                        └──────────────────────────────────┘
                                      │
                            ┌─────────┴─────────┐
                            ▼                   ▼
                  /var/lib/selena/      /usr/local/lib/python3.11/
                  registry.db            site-packages/
                  (DriverProvider        (greeclimate, tinytuya,
                   table, persistent      phue, aioesphomeapi…)
                   across reboots)
```

Both the registry DB and the pip site-packages live OUTSIDE the
integrity agent's watch glob (`/opt/selena-core/core/**/*.py`), so
installing a new provider never triggers an integrity violation.

### 2.2 Storage layer

`core/registry/models.py` adds the `DriverProvider` ORM class:

| Column          | Type      | Meaning |
| --------------- | --------- | ------- |
| `id`            | str (PK)  | Catalog id, e.g. `gree` |
| `package`       | str?      | pip package name (or null for stub providers) |
| `version`       | str?      | Version spec, e.g. `>=2.1` |
| `enabled`       | bool      | If false, loader skips this provider |
| `auto_detected` | bool      | True for built-ins seeded on first start |
| `installed_at`  | datetime  | UTC timestamp |
| `last_error`    | text?     | ImportError message if the last load failed |

The table is created by SQLAlchemy `Base.metadata.create_all` on first
startup. No migration script needed.

### 2.3 Catalog

`system_modules/device_control/providers/catalog.py` is the **single
source of truth** for known providers. Each entry is a `ProviderSpec`
with these fields:

```python
{
    "id":             "gree",
    "name":           "Gree / Pular WiFi A/C",
    "description":    "Local control of Gree-protocol air conditioners…",
    "package":        "greeclimate",
    "version":        ">=2.1",
    "driver_module":  "system_modules.device_control.drivers.gree",
    "driver_class":   "GreeDriver",
    "entity_types":   ["air_conditioner"],
    "needs_cloud":    False,
    "builtin":        True,
    "icon":           "❄️",
    "homepage":       "https://github.com/cmroche/greeclimate",
}
```

**Built-ins** (ship pre-installed in `requirements.txt`, auto-detected
on first start): `tuya_local`, `tuya_cloud`, `gree`, `mqtt`.

**Opt-in extras** (require explicit Install via the UI): `philips_hue`,
`esphome`, `zigbee2mqtt`.

To add a new provider:
1. Create the driver class in `system_modules/device_control/drivers/`.
2. Add a `ProviderSpec` entry to `PROVIDERS` in `catalog.py`.

That's it. The Providers tab auto-discovers the new entry, the loader
imports it on demand, the integrity agent ignores it.

### 2.4 Loader

`system_modules/device_control/providers/loader.py`:

| Method                 | Purpose |
| ---------------------- | ------- |
| `bootstrap_builtins()` | On first start, import each built-in driver module to verify it's available, then INSERT a row marked `auto_detected=True, enabled=<importable>`. |
| `load_enabled()`       | Walk the DB table, `importlib.import_module()` every enabled provider's driver module, populate `self.drivers`. ImportError → `last_error` written, provider skipped. Returns the populated dict. |
| `install(id)`          | `subprocess.run(["pip", "install", spec])` in a thread. On success: `importlib.import_module()` to verify, `UPSERT` DB row, mutate `drivers/registry.DRIVERS` in place. |
| `uninstall(id, …)`     | Remove from `drivers/registry.DRIVERS` + DELETE row. Optionally `pip uninstall -y` (off by default — keeps package on disk). Built-ins refuse `remove_package=True`. |
| `list_state()`         | Join the catalog with each provider's DB row → `[{installed, loaded, last_error, …}]` for the Providers tab UI. |

The DB row is committed **only after** `pip install` returns a zero
exit code. If pip dies mid-install (power loss), no row is committed —
no half-state. The next install retry is safe.

### 2.5 Hot-reload contract

After `install()` succeeds, the loader **mutates `drivers.registry.DRIVERS`
in place** rather than replacing it. Existing watchers (which hold
references to driver instances, not the DRIVERS dict) keep running
unaffected. NEW devices use the freshly imported driver class
immediately. No container restart required.

After `uninstall()`, existing watchers continue with their cached
driver instances until next reconnect, when they fail gracefully with
`DriverError("Provider not installed")` and the device shows offline.

### 2.6 Restart resilience

| Failure mode                           | Outcome |
| -------------------------------------- | ------- |
| Container restart                      | `bootstrap_builtins()` is no-op for already-seeded rows; `load_enabled()` re-imports every enabled driver. Devices reconnect via existing watcher logic. |
| Power loss mid-`pip install`           | No DB row committed → next start sees only previously-installed providers. Partially-extracted package on disk is harmless. |
| `pip uninstall` race with watchers     | Watchers using the driver get `DriverError` on next reconnect, devices marked offline. UI shows red badge. |
| User wipes `/var/lib/selena/`          | DB recreated empty → `bootstrap_builtins()` re-seeds the four built-ins → device list lost (this is the documented "factory reset" path). |
| Provider package becomes un-importable | `load_enabled()` writes the ImportError to `last_error`, skips the provider, device-control still starts. UI shows red badge with the message and a Reinstall button. |

### 2.7 Integrity agent compatibility

The agent in `agent/integrity_agent.py` watches **only**
`/opt/selena-core/core/**/*.py` (see [agent/manifest.py](../agent/manifest.py)).

The provider system places everything OUTSIDE that scope:
- Catalog + loader: `/opt/selena-core/system_modules/device_control/providers/`
- Driver classes: `/opt/selena-core/system_modules/device_control/drivers/`
- pip site-packages: `/usr/local/lib/python3.11/site-packages/`
- DB row: `/var/lib/selena/registry.db`

**No code changes to the agent are needed.** Installing a new provider
is invisible to it.

### 2.8 Providers tab UI

`system_modules/device_control/settings.html` adds a new **«Provider'и»**
tab next to Devices / Tuya Cloud Wizard / Gree-Pular. Layout:

- Help banner explaining the system.
- Card grid (`auto-fill, minmax(280px, 1fr)`).
- Each card shows:
  - Icon emoji + name + status badge (Installed / Not installed)
  - Description
  - `package version` in monospace
  - Sub-badges: built-in, loaded, cloud, external service
  - Last error in red box if `last_error` is set
  - Action button: Install (if not installed) or Disable (if installed)

REST endpoints:
- `GET /api/ui/modules/device-control/providers` → list with state
- `POST /providers/{id}/install` → returns `{ok, message, restart_needed: false}`
- `POST /providers/{id}/uninstall` → body `{remove_package?: bool}`

Full EN/UK i18n via the inline `var L = {en, uk}` dictionary per
CLAUDE.md §3.1.

## 3. Auto-routing on `device.registered`

### 3.1 Enriched event payload

Every code path that creates a Device (manual POST, Tuya import, Gree
import) now publishes `device.registered` with the FULL payload:

```python
{
    "device_id":     "73ccd8c3-...",
    "name":          "Вітальня",
    "entity_type":   "air_conditioner",
    "location":      "living room",
    "protocol":      "gree",
    "capabilities":  ["on", "off", "set_temperature", "set_mode", "set_fan_speed", "set_swing"],
}
```

`device.removed` is enriched too, so subscribers know which `entity_type`
the disappearing device belonged to.

### 3.2 Tuya entity_type classifier

The Tuya cloud import previously assigned `entity_type="switch"` to
**every** imported device. New helper `_classify_tuya_entity_type()` in
`routes.py` makes a best-effort guess from `category` + `product_name`
+ `name`:

| Tuya signal                                        | entity_type      |
| -------------------------------------------------- | ---------------- |
| `category="dj"` OR keyword `light/lamp/bulb/led/лампа/світло` | `light` |
| `category="cz"` OR keyword `socket/outlet/plug/розетка`         | `outlet` |
| `category="fs"` OR keyword `fan/вентилятор`        | `fan` |
| anything else                                      | `switch` (fallback) |

Lights additionally get `brightness` / `colour_temp` capabilities if
the Tuya status payload contains the matching DPS codes
(`bright_value*`, `temp_value*`, `colour_data`).

The user can always override via `PATCH /devices/{id}` —
classification is just the initial guess.

### 3.3 Subscribers

Three modules subscribe to `device.registered` / `device.removed`:

| Module             | Action on `device.registered` | Action on `device.removed` |
| ------------------ | ----------------------------- | -------------------------- |
| **energy-monitor** | If no source exists for this device_id, `add_source(type="device_registry", config={device_id})`. Auto-tracks every new device. | Find source by device_id → `delete_source()`. |
| **climate**        | (DB query happens on next `GET /rooms` anyway — no pre-fetch.) | Drop cache entry for `device_id` if `entity_type` is air_conditioner / thermostat. |
| **lights-switches**| Same — DB-driven on next request. | Drop cache entry if `entity_type` is light/switch/outlet. |

This is the only "routing layer" — there's no central capability router.
Each consumer module owns its own filter list and decides for itself
whether a given event is relevant.

## 4. Lights-switches SYSTEM module

`system_modules/lights_switches/` mirrors the climate module's pattern
exactly, but for `entity_type ∈ {light, switch, outlet}`.

### 4.1 Files

| File             | Purpose |
| ---------------- | ------- |
| `__init__.py`    | Exports `module_class = LightsSwitchesModule` |
| `manifest.json`  | type=SYSTEM, no port, `entities: ["light","switch","outlet"]`, widget 2x2 |
| `icon.svg`       | Bulb glyph |
| `module.py`      | `LightsSwitchesModule(SystemModule)` — caches state + watts, subscribes to state/power/lifecycle events, uses `get_sandbox().get_in_process_module("device-control")` for cross-module command dispatch |
| `routes.py`      | `GET /devices`, `GET /rooms`, `GET /device/{id}`, `POST /device/{id}/command`. Validates `ALLOWED_STATE_KEYS = {on, brightness, colour_temp, rgb_color}`. |
| `widget.html`    | Dual-mode (compact rows on dashboard, full-control modal cards). |
| `settings.html`  | Read-only diagnostic table grouped by room. |

### 4.2 Modal controls

The modal exposes capability-aware controls:

- **Power**: Swift-style sliding toggle (replaces the old emoji button)
- **Brightness slider**: 0–100, debounced 250ms, only if `brightness` in capabilities
- **Colour temperature slider**: 0–100, only if `colour_temp` in capabilities
- **RGB picker**: HTML5 `<input type="color">`, only if `rgb_color` in capabilities

Sliders update their label live; commands fire after 250ms idle so
dragging doesn't spam the device with 100 commands.

### 4.3 Voice intents

Lights-switches **does not own any voice intent**. The existing
`device.on` / `device.off` intents in device-control already handle
lights and switches via the entity_filter mechanism. If brightness
control via voice is added later, it goes into device-control as
`device.set_brightness` (mirroring the climate intent pattern in
[climate-and-gree.md](climate-and-gree.md) §7).

## 5. Energy-monitor refactor

### 5.1 Settings page — three sections → one table

The previous layout had three separate sections (Огляд / Споживання /
Порогові). The new layout collapses them into:

1. **KPI strip** (kept): Status / Tracked / Total Power / Today kWh
2. **Filter bar**: search input + Type dropdown + Room dropdown + Status dropdown
3. **Sortable unified table**: Name | Room | Type | State | Power (W) | Today (kWh)
   - Click any TH to sort (toggles asc/desc)
   - Filters update the table instantly
   - Auto-populates the Room filter from data
4. **Compact thresholds panel** (kept): Alert / Low / Report interval

### 5.2 New endpoint

`GET /api/ui/modules/energy-monitor/energy/devices/full` — joins the
Device registry with current power + today's kWh + source state into
one ready-to-render list. Returns:

```json
{
  "devices": [
    {
      "device_id": "73ccd8c3-...",
      "name":      "Вітальня",
      "location":  "living room",
      "entity_type": "air_conditioner",
      "protocol":  "gree",
      "enabled":   true,
      "state":     {"on": true, "mode": "auto", ...},
      "watts":     607.8,
      "kwh_today": 0.143,
      "source":    {"id": "cb60f6d2", "enabled": true, "last_reading_ts": "..."}
    },
    ...
  ]
}
```

`module.py._join_devices()` does the join. No new SQL — uses existing
`EnergyMonitor.get_current_power()` + `get_daily_kwh()` + `get_sources()`.

### 5.3 Widget — clickable, opens device-list modal

The dashboard 1×1 tile (Total Power + Active count + Today kWh) is now
**click-through**:

- Whole body has `cursor: pointer` + `pointer-events: none` on children
- `pointerdown` + `click` triggers (defends against cross-iframe focus)
- On click → `postMessage({type:'openWidgetModal', module:'energy-monitor', width:760, height:580})`

The widget's modal mode (`?modal=1`) renders a different layout:
- 4 KPI cards on top
- Filter bar
- Same sortable unified table

`Esc` or close button → `closeWidgetModal` postMessage. This becomes
the de-facto "all my devices" view, replacing the old device-control
widget.

## 6. Widget UX evolution

After live-testing, several rounds of UX fixes shipped:

### 6.1 Compact dashboard rows

Both the climate widget and the lights-switches widget were redesigned
for narrow dashboard tiles:

| Widget          | Compact row contents |
| --------------- | -------------------- |
| **climate**     | `● [name] [target_temp]°` |
| **lights-switches** | `● [name]` (just status dot + name) |

Mode/fan/swing/current temp/brightness/colour all live in the modal —
the row is purely informational ("is it on, what's it set to"). Control
happens via voice OR click→modal.

### 6.2 Removed room headers in compact mode

Earlier versions wrapped each row group in a `[ROOM NAME]` header. On
narrow tiles this wasted vertical space and the user couldn't fit
device names. Compact mode now hides room headers entirely
(`.room-title { display: none }`); modal mode keeps them since it has
horizontal room.

### 6.3 Click-reliability fixes

Three subtle bugs fixed across both widgets:

**Bug 1: First click on the row didn't fire (had to click directly on text).**
The CSS rule `.row > * { pointer-events: none }` only affected DIRECT
children, but climate's row had `<span class="row-temp"><span class="now">22°</span></span>`
— `.now` is a grandchild that still received pointer events and
swallowed them. Fix: `.row * { pointer-events: none }` (all descendants).

**Bug 2: Inside modal, first click on chip/button didn't work.**
Plain `onclick` requires the iframe to have focus. First click handed
focus, second click triggered. Fix: `tap()` helper that registers BOTH
`pointerdown` AND `click` with a 250ms dedup guard, plus `window.focus()`
in `bindFullCards()` so the iframe grabs focus on first render.

**Bug 3: Name truncated to "2 letters and ellipsis" on narrow tiles.**
The flex item `.row-name` had `flex: 1` + `white-space: nowrap` +
`text-overflow: ellipsis` but **no `min-width: 0`**. Without it, flex
children default to `min-width: auto` which means they refuse to
shrink below their intrinsic content width. Fix: `min-width: 0` on
`.row` AND `.row-name`. Now the name shrinks gracefully and ellipsis
kicks in only when there's truly no room.

### 6.4 Swift-style power toggle

The old `<button>⏻</button>` rendered as a broken/missing glyph on
systems without that emoji font. Replaced with a CSS sliding toggle:

```html
<label class="toggle">
  <input type="checkbox" checked>
  <span class="slider"></span>
</label>
```

```css
.toggle           { width: 52px; height: 30px; }
.toggle .slider   { background: var(--b); border-radius: 30px; }
.toggle .slider::before { width: 24px; height: 24px; background: #fff; transition: transform .22s ease; }
.toggle input:checked + .slider { background: var(--gr); }
.toggle input:checked + .slider::before { transform: translateX(22px); }
```

Pure CSS animation, no JS, no images, no emoji fonts. Climate uses
green (`--gr`); lights uses amber (`--am`) for `entity_type="light"`
and green for switches/outlets.

### 6.5 Modal sizing protocol

`Dashboard.tsx` accepts `modal_resize { width, height }` postMessage
events from any widget. When opening a modal, the widget can also pass
an `openWidgetModal { module, width, height }` payload to set the
initial size up front (avoids the "open big → flash → resize small"
flicker). The panel transitions smoothly (`transition: width .18s
ease-out, height .18s ease-out`).

Climate widget passes `width:480, height:560` initially; the
`reportModalSize()` helper measures the rendered content with
`requestAnimationFrame` and refines the size via `modal_resize`. Same
pattern in lights-switches.

## 7. Files changed

### Created

| Path                                                                                       | Purpose |
| ------------------------------------------------------------------------------------------ | ------- |
| `system_modules/device_control/providers/__init__.py`                                      | Package exports |
| `system_modules/device_control/providers/catalog.py`                                       | Static provider catalog |
| `system_modules/device_control/providers/loader.py`                                        | ProviderLoader |
| `system_modules/lights_switches/__init__.py`                                               | Package |
| `system_modules/lights_switches/manifest.json`                                             | Module manifest |
| `system_modules/lights_switches/icon.svg`                                                  | Icon |
| `system_modules/lights_switches/module.py`                                                 | LightsSwitchesModule |
| `system_modules/lights_switches/routes.py`                                                 | REST router |
| `system_modules/lights_switches/widget.html`                                               | Compact + modal widget |
| `system_modules/lights_switches/settings.html`                                             | Diagnostic settings page |
| `tests/test_provider_system.py`                                                            | Catalog + classifier tests |
| `docs/provider-system-and-modules.md`                                                      | This document |
| `docs/uk/provider-system-and-modules.md`                                                   | Ukrainian translation |

### Modified

| Path                                                                                       | Change |
| ------------------------------------------------------------------------------------------ | ------ |
| `core/registry/models.py`                                                                  | New `DriverProvider` ORM table |
| `system_modules/device_control/drivers/registry.py`                                        | DRIVERS now starts empty, populated by ProviderLoader |
| `system_modules/device_control/module.py`                                                  | Initialise ProviderLoader in `start()` |
| `system_modules/device_control/routes.py`                                                  | New `/providers/*` endpoints + Tuya entity classifier + enriched device.registered/removed payloads |
| `system_modules/device_control/settings.html`                                              | New «Provider'и» tab with card grid |
| `system_modules/device_control/manifest.json`                                              | Drop `ui.widget` block |
| `system_modules/device_control/widget.html`                                                | DELETED |
| `system_modules/energy_monitor/module.py`                                                  | Subscribe to device lifecycle events + `_join_devices()` + `/devices/full` endpoint |
| `system_modules/energy_monitor/settings.html`                                              | Three sections → one unified filterable table |
| `system_modules/energy_monitor/widget.html`                                                | Click-through + modal mode with full table |
| `system_modules/climate/module.py`                                                         | Subscribe to device.registered/removed for cache invalidation |
| `system_modules/climate/widget.html`                                                       | Slim compact rows, room labels (`roomLabel()`), Swift toggle, click reliability fixes, sized modal |

## 8. Verification

### Functional
1. `pytest tests/test_provider_system.py tests/test_gree_driver.py
   tests/test_device_watchdog.py tests/test_energy_monitor.py -q` →
   72 passed.
2. `docker compose restart core` → providers loaded on startup, all
   built-ins green in the Providers tab.
3. **Auto-routing**: import a Pular AC via Gree wizard → climate
   widget shows it within 5 s AND energy-monitor sources tab shows
   the new auto-created entry.
4. **Tuya light import**: import a Tuya bulb → lands in lights-switches
   widget with brightness slider, NOT in climate.
5. **Energy unified table**: open energy-monitor settings → all
   devices in one filterable table → filter by room → only matching
   devices visible → sort by Power desc → AC at top.
6. **Energy widget click**: dashboard energy tile → fullscreen modal
   with same table → close with Esc.
7. **Climate compact row**: shows just `● [name] [target temp]°`
   without room headers, full name visible.
8. **Climate modal**: chip/button clicks fire on the FIRST tap, no
   "click twice" issue. Power toggle slides smoothly on/off.
9. **Lights compact row**: shows just `● [name]`, full width.
10. **Lights modal**: brightness slider + colour temp + RGB picker,
    Swift toggle for power.

### Restart resilience
11. `docker compose down && docker compose up -d` → all providers and
    devices reconnect.
12. Power-loss simulation: `docker kill selena-core` mid-`pip install`
    → restart → no half-row in DB.

### Integrity agent
13. After installing a new provider → wait 60 s → no
    `core.integrity_violation` event in `docker logs selena-agent`.

## 9. Critical files reference

- [system_modules/device_control/providers/catalog.py](../system_modules/device_control/providers/catalog.py) — add a new provider here
- [system_modules/device_control/providers/loader.py](../system_modules/device_control/providers/loader.py) — install/load/uninstall logic
- [system_modules/device_control/drivers/registry.py](../system_modules/device_control/drivers/registry.py) — runtime DRIVERS dict
- [system_modules/lights_switches/module.py](../system_modules/lights_switches/module.py) — lights/switches/outlets controller
- [system_modules/energy_monitor/module.py](../system_modules/energy_monitor/module.py) — auto-source + `_join_devices()` + `/energy/devices/full`
- [system_modules/climate/widget.html](../system_modules/climate/widget.html) — compact + modal climate widget
- [core/registry/models.py](../core/registry/models.py) — `DriverProvider` ORM
- [agent/manifest.py](../agent/manifest.py) — integrity-agent watch glob (do NOT extend)

## 10. Known limitations

- **Pip install can be slow on Jetson** for packages without arm64 wheels.
  The install runs in a background thread; UI shows a spinner. Default
  timeout 5 minutes.
- **Tuya entity_type classification has false positives** (a Tuya
  outlet labelled "Kitchen Light" by the user will be marked as light).
  PATCH /devices/{id} fixes it manually.
- **`importlib.reload()` after install** does not always update class
  references in already-running watchers — only NEW devices use the
  freshly imported driver.
- **Lights-switches v1 only handles Tuya / generic devices.** Native
  Hue / ESPHome support depends on the user installing those providers
  via the Providers tab; the driver classes themselves are stubs that
  need implementation when those packages land in the catalog.
