# Dashboard Recraft & Widget Templating Engine

> Visual and architectural recraft of the SelenaCore dashboard. Replaces the uniform 5×4 iframe grid with a hero panel, scene shortcuts, room tabs, and a bento grid of mixed sizes. Lifts the widget abstraction from "render this HTML in an iframe" to "declare what to show — core renders it." Keeps the iframe path as a fallback for custom UIs.
>
> **➡ See also:** [widget-development.md](widget-development.md) — current widget guide. This document supersedes much of it once the template engine ships. [Українська версія](uk/dashboard-recraft.md).

---

## 1. Overview

### 1.1 Why a recraft

The current dashboard reads like an admin panel, not a smart-home surface. Five concrete problems:

1. **No context.** Opening the dashboard shows a grid of equal-weight tiles. No greeting, no time, no outdoor weather, no system status — nothing that frames the moment.
2. **Uniform grid.** Every widget occupies an identical cell in a fixed 5×4 grid. No visual hierarchy: living-room climate looks the same as a CPU temperature dot. Wrong aesthetic for a home control surface.
3. **20 widgets, 20 designs.** Every module author writes their own HTML, CSS, fetch logic, error states, loading states, and theme hooks. Result: visual cacophony even when the data is similar.
4. **An iframe per widget.** N iframes on a Pi means N browser contexts, N HTTP fetchers, N event loops. Tooltips, hovers, focus, and animations cannot be shared. Cross-widget interactions are impossible.
5. **Wasted potential.** Framer Motion (12.x), lucide-react, Tailwind 4 with `@theme`, and a full design-token system in `index.css` are already installed and barely used. The recraft is mostly composition, not new dependencies.

### 1.2 Goals

Three goals in priority order:

- **Make the dashboard read as a home, not as an admin panel.** Hero, scenes, rooms, mixed widget sizes.
- **Unify widget appearance and behavior.** One chrome, one set of states (loading / error / stale), one motion language. Module authors stop writing CSS.
- **Preserve backward compatibility.** Modules with `widget.html` keep working. The new system layers on top.

### 1.3 Non-goals

The recraft does **not** touch:

- Module Bus, EventBus, or core API contracts.
- Kiosk safety mechanism ([useConnectionHealth](../src/hooks/useConnectionHealth.ts), 5-minute auto-reload on stale connection).
- Widget grid sizing (`WxH` in manifest) — sizes are reused.
- PWA, Service Worker, HTTPS proxy.
- Auth, ACLs, permissions.

---

## 2. Visual layer

### 2.1 Page structure

The dashboard becomes a vertical stack of four regions:

```
┌──────────────────────────────────────────────────────────────┐
│  Hero — greeting, time, status pill, outdoor weather          │  ~96 px
├──────────────────────────────────────────────────────────────┤
│  Scenes — 4 horizontal scene shortcut chips                   │  ~52 px
├──────────────────────────────────────────────────────────────┤
│  Rooms — All / Living / Bedroom / Kitchen / System            │  ~36 px
├──────────────────────────────────────────────────────────────┤
│                                                                │
│  Bento grid — mixed-size widgets, grid-auto-flow dense         │  fills
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

Total non-grid chrome: ~184 px. On 1080p kiosk that leaves ~880 px for widgets — comfortable for two rows of 4×2 tiles. On phone (375×667) chrome compresses to ~140 px and the grid collapses to a single column.

### 2.2 Hero panel

| Region        | Content                                              |
|---------------|------------------------------------------------------|
| Top-left line | Day, date, time (e.g. "Monday, April 27 · 14:32")    |
| Greeting      | "Good {morning, afternoon, evening}, {firstName}"    |
| Status pill   | Aggregated system state — see §2.2.1                 |
| Top-right     | Outdoor temperature + condition (if `weather` module installed) |

**Time-of-day background.** Soft radial gradient whose tint rotates with local hour: cool blue 22:00–06:00, warm amber 06:00–10:00, neutral white-blue 10:00–18:00, golden 18:00–22:00. `radial-gradient(ellipse at 20% 0%, var(--hero-tint) 0%, transparent 60%)` over `var(--bg)`. `--hero-tint` set on `<html data-tod="...">` and refreshed every 15 minutes.

#### 2.2.1 Status pill states

| State    | Color       | Text                                       | Trigger                                    |
|----------|-------------|--------------------------------------------|--------------------------------------------|
| OK       | `--gr`      | "All systems nominal · {N} modules active" | All modules `RUNNING`, integrity OK        |
| Warning  | `--am`      | "Checking integrity..."                    | Integrity check in progress                |
| Degraded | `--am`      | "Module {name} in error state"             | Any module in `ERROR`, core healthy        |
| Safe     | `--rd`      | "SAFE MODE — {reason}"                     | Core entered SAFE MODE (rollback / freeze) |

Pill is non-clickable in OK state, links to `/settings/system-info` otherwise.

### 2.3 Scene shortcuts

Four scene chips sit below hero in a single horizontal row. Each is a button with icon + label. Click sends `POST /api/v1/scenes/{id}/activate` to core (added in Phase 0). Default scenes:

- **Good morning** — warm-low lights, climate to comfort, morning TTS summary
- **Leaving** — all lights off, climate to away, security armed
- **Movie** — living lights to 15 %, TV/media on, AC quiet
- **Good night** — all lights off, bedroom climate to sleep, security armed

If the scenes API returns no scenes (fresh install), the row is hidden entirely. No empty placeholders.

### 2.4 Room tabs

Horizontal scrolling room filter. Tabs derived at runtime from the `room` field of registered devices and modules. First tab is always "All", last tab is always "System" (modules with `room: "system"` — `cloud-sync`, `integrity`, `device-watchdog` — diagnostic surfaces for the homeowner without exposing them to guests).

Tab state in client `useState` only — does not persist across reloads. Opening the dashboard always starts on "All". Matches the smart-home mental model: a surface for the current moment, not for resuming.

### 2.5 Fixed 5×4 grid

After field testing of the bento auto-flow, the V2 dashboard reverted to V1's fixed grid: **5 columns × 4 rows on desktop / 1080p kiosk**, **4 columns on tablet (480–900 px)**, **single column with vertical scroll on phone (<480 px)**. Per-widget `grid-column: span W; grid-row: span H` is computed from the manifest `WxH`; the anchor cell comes from `widgetLayout.positions[name]` (V1's spatial map is reused — `slot = (col-1) + (row-1)*5`).

Cell size adapts to viewport: `cellHeight = (availableH − gaps) / 4`, `cellWidth = (availableW − gaps) / 5`. Edit mode renders empty slots as `+` drop targets and overlays a soft dotted gridline pattern so the user sees exactly which cell their drag will land in.

`grid-auto-flow: dense` is still set on the container so an out-of-bounds widget (after a manifest size change, say) doesn't leave a gap, but the typical case is fully explicit placement.

### 2.6 Design tokens

All needed base tokens already exist in [src/index.css](../src/index.css). The recraft adds:

| Token              | Dark value                            | Light value                       | Purpose                                  |
|--------------------|----------------------------------------|------------------------------------|-------------------------------------------|
| `--hero-tint`      | dynamic (see §2.2)                     | dynamic (lighter)                  | Time-of-day hero background               |
| `--widget-glow-on` | `0 0 0 1px var(--ac), 0 8px 24px rgba(90,150,255,.12)` | `0 0 0 1px var(--ac), 0 4px 16px rgba(59,122,232,.10)` | Active toggle highlight |
| `--motion-spring`  | `cubic-bezier(.5, 1.4, .5, 1)`         | (same)                             | Toggle / mode-switch animation curve      |
| `--skeleton-bg`    | `linear-gradient(90deg, var(--sf2) 0%, var(--sf3) 50%, var(--sf2) 100%)` | (similar) | Pulsing loading background              |

Existing tokens (`--bg`, `--sf`, `--ac`, `--gr`, `--am`, `--rd`, `--tx`, `--tx2`, `--tx3`, etc.) are unchanged.

---

## 3. Widget templating engine

### 3.1 Concept

Today a widget is "an HTML file rendered in an iframe." The recraft splits this into two kinds:

- **`kind: "template"`** — Module declares **what** to show by returning a JSON payload matching one of five built-in shapes. Core renders the UI. ~85 % of real widgets fit here.
- **`kind: "custom"`** — Module ships an HTML file rendered in an iframe — exactly like today. Used for d3 visualizations, canvas UIs, room plan editors, games — anything off-template.

Both kinds share the same outer chrome (status dot, header, menu) drawn by core. Both share the same lifecycle (skeleton → data → refresh → error). Both honor `room` filtering and `WxH` sizing.

### 3.2 Manifest schema

Pydantic schema lives at [`core/module_loader/manifest_schema.py`](../core/module_loader/manifest_schema.py). The `ui.widget` block gains:

```json
{
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "kind": "template",
            "template": "control-panel",
            "data_endpoints": {
                "state": {"path": "/widget/data/state", "cache_ttl_s": 5}
            },
            "actions": {
                "set_mode": {"path": "/widget/action/mode"},
                "set_temp": {"path": "/widget/action/temp"}
            },
            "size": "4x2",
            "max_size": "4x2",
            "refresh": {
                "events": ["device.state_changed"],
                "poll_interval_s": 30
            }
        },
        "settings": "settings.html"
    }
}
```

| Field                              | Type                      | Required              | Description                                                                                              |
|------------------------------------|---------------------------|-----------------------|----------------------------------------------------------------------------------------------------------|
| `widget.kind`                      | `"template" \| "custom"`  | No, default `custom`  | Core renders templates; iframe handles customs.                                                          |
| `widget.template`                  | enum (see §3.3)           | If `kind="template"`  | Which built-in template to render.                                                                       |
| `widget.data_endpoints[k]`         | `{path, cache_ttl_s?}`    | No                    | Path on the module's HTTP surface (proxied via Module Bus). Dashboard hits `GET /api/v1/modules/{name}/data/{k}`. |
| `widget.actions[k]`                | `{path}`                  | No                    | Write-action path. Dashboard hits `POST /api/v1/modules/{name}/action/{k}`.                              |
| `widget.refresh.events`            | `string[]`                | No                    | EventBus topics that trigger a refetch.                                                                  |
| `widget.refresh.poll_interval_s`   | int (≥1)                  | No                    | Polling fallback interval.                                                                               |
| `widget.file`                      | string                    | If `kind="custom"`    | HTML file for the iframe. Ignored when `kind="template"`.                                                |

Existing `widget.size` and `widget.max_size` are unchanged. The Pydantic schema validates `size` against `template`: `metric` cannot be `4x4`; `control-panel` cannot be `1x1`; `status` cannot exceed `4x2`.

**Required `room`.** Phase 0 adds a **mandatory** top-level `room: str` field. All 18 system modules now declare either `"room": "system"` (diagnostic) or `"room": "home"` (cross-room user-facing aggregator).

### 3.3 Templates

The shipped set is **8 templates**: 5 generic primitives (3.3.1–3.3.5) and 3 specialized layouts for common rich use-cases (3.3.6–3.3.8). Each template specifies: purpose, recommended sizes, payload schema, action contract, render guarantees.

All templates accept icon names as plain strings. The frontend [`Icon`](#37-icon-system) helper resolves them against an emoji map (☀️ 🌧️ 💡 🔌 ⚡ 🎵 🎙️ 📡 🛡️ ...) — no SVG icon library is bundled for dashboard widgets. Modules ship lucide-style names (`cloud`, `droplets`, `lightbulb`) and the helper picks the right glyph; unknown names fall back to the supplied `fallback` string or the raw name itself.

#### 3.3.1 `metric`

Single primary number with optional trend indicator and unit suffix. **Sizes:** `1x1`, `2x1` (preferred), never larger than `2x2`.

```json
{
    "label": "Devices",
    "value": "14",
    "unit": null,
    "trend": {"direction": "up", "magnitude": "+2", "period": "this week"},
    "tone": "neutral"
}
```

| Field | Description |
|-------|-------------|
| `label`, `value`, `unit` | Header, value, suffix |
| `trend.direction` | `"up" \| "down" \| "flat"` — drives icon and color |
| `trend.magnitude`, `trend.period` | Pre-formatted strings |
| `tone` | `"neutral" \| "info" \| "ok" \| "warn" \| "alert"` |

**Optional Phase 6 fields:** `icon` (lucide-style name rendered top-right). Used by `device-watchdog` (`activity`), `clock` (`alarm-clock`), `automation-engine` (`workflow`), `satellite-manager` (`satellite`).

**Actions:** none.

#### 3.3.2 `sparkline`

Primary value plus a small line chart of recent N points. **Sizes:** `2x1`, `2x2` (preferred), `4x2`.

```json
{
    "label": "Energy", "value": "1.24", "unit": "kW",
    "footnote": "today · 8.7 kWh",
    "series": [0.8, 0.9, 0.85, 1.1, 1.0, 1.3, 1.15, 1.5, 1.2, 1.4, 1.1, 1.24],
    "series_window_s": 3600,
    "tone": "info"
}
```

`series` ≤ 60 points for visual clarity. Sparkline auto-scales, Y-bounds `[min, max]` with 8 % padding, no gridlines. Endpoint dot at "now" anchors the latest reading; gradient fill keyed off `tone` darkens toward zero.

**Optional Phase 6 fields:**
- `icon` — leading glyph next to the value
- `breakdown: CardSpec[]` — top-N contributor cards rendered below the chart (used by `energy-monitor` to show the top-3 power-hungry devices alongside whole-house total)

#### 3.3.3 `toggle-list`

Named toggleables with on/off state and optional secondary metric. **Sizes:** `2x2`, `4x2` (preferred), `4x4`.

```json
{
    "label": "Lights",
    "summary": "3 of 7 on",
    "items": [
        {"id": "living-1", "name": "Living", "state": "on", "secondary": "80 %"},
        {"id": "kitchen-1", "name": "Kitchen", "state": "on", "secondary": "100 %"},
        {"id": "hall-1", "name": "Hallway", "state": "off", "secondary": null}
    ]
}
```

**Actions:** `toggle` (required) accepts `{"id": "<item_id>"}`. Optional `set_secondary` for long-press affordance.

**Optional Phase 6 fields:** `items[].icon` — per-item glyph. `lights-switches` maps `entity_type → icon`: light → `lightbulb`, switch → `power`, outlet → `zap`. Inactive items render the icon dimmed; active items render in accent colour.

#### 3.3.4 `control-panel`

Primary value, segmented mode selector, optional stepper row. **Sizes:** `4x2` (preferred), never smaller than `2x2`.

```json
{
    "label": "Climate · Living",
    "primary": {"value": "22.5", "unit": "°", "secondary": "→ set 23.0°"},
    "modes": {
        "current": "auto",
        "options": [
            {"id": "auto", "label": "Auto"},
            {"id": "cool", "label": "Cool"},
            {"id": "heat", "label": "Heat"},
            {"id": "dry",  "label": "Dry"}
        ]
    },
    "steppers": [
        {"id": "temp", "label": "Temp", "value": "23.0", "unit": "°", "min": 16, "max": 30, "step": 0.5}
    ]
}
```

**Actions:** `set_mode` accepts `{"id": "<mode_id>"}`. `step` accepts `{"id": "<stepper_id>", "value": <number>}`.

**Optional Phase 6 fields:** `secondary_pills: IconStripItem[]` — extra readings inline below the primary value. `climate` populates this with humidity / fan speed / estimated wattage so a single widget covers what V1 spread across two iframes.

#### 3.3.5 `status`

Health pill on top, then 1–4 key-value rows. **Sizes:** `2x1`, `2x2` (preferred), max `4x2`.

```json
{
    "label": "Cloud sync",
    "pill": {"tone": "ok", "text": "Synced", "icon": "check"},
    "rows": [
        {"label": "Heartbeat", "value": "18s ago"},
        {"label": "Backoff",   "value": "5s"}
    ]
}
```

`pill.tone` ∈ {ok, info, warn, alert, neutral}. `pill.icon` accepts any [Icon](#37-icon-system) name (lucide-style strings; legacy short codes `check / clock / alert / x / refresh` still work). Up to 4 rows. **Actions:** optional `refresh`.

**Optional Phase 6 fields:**
- `rows[].icon` — leading glyph for each row (e.g. `protocol-bridge` shows a server icon next to the broker host)
- `strip: IconStripItem[]` — compact icon + value horizontal row; used by `protocol-bridge` for MQTT/Zigbee/Z-Wave health
- `cards: CardSpec[]` — mini-card row at the bottom; used by `notification-router` for recent notification previews
- `actions: ActionSpec[]` — inline buttons in the header; `update-manager` exposes `Check` to trigger an upstream check via `POST /api/v1/modules/{name}/action/check_now`

#### 3.3.6 `weather` (specialized)

Hero condition + telemetry pills + 3-day forecast. **Sizes:** `4x2` (preferred), min `2x2`.

```json
{
    "location": "Kyiv",
    "current": {
        "icon": "cloud-rain",
        "emoji": "🌧️",
        "temperature": 14,
        "unit": "°C",
        "condition": "Light rain",
        "feels_like": 12
    },
    "pills": [
        {"icon": "droplets", "value": "82%"},
        {"icon": "wind",     "value": "9 km/h"},
        {"icon": "cloud-rain", "value": "1.2 mm"}
    ],
    "forecast": [
        {"day": "Tue", "icon": "sun",        "high": 22, "low": 12, "unit": "°C"},
        {"day": "Wed", "icon": "cloud",      "high": 18, "low": 10, "unit": "°C"},
        {"day": "Thu", "icon": "cloud-rain", "high": 15, "low":  8, "unit": "°C"}
    ]
}
```

Renders an Apple-Weather-style hero (big emoji + 38 px temperature + condition · feels-like), an icon strip for telemetry, and 3 forecast cards (high in `--am`, low in `--ac`). The hero gets a soft radial gradient tinted by `current.icon` (sun → amber, cloud → cool grey, cloud-rain → blue, zap → purple). `current.emoji` is the fallback when `current.icon` is unknown.

**Actions:** none.

#### 3.3.7 `media` (specialized)

Cover art + track meta + transport row + volume slider. **Sizes:** `4x2` (preferred), min `2x2`.

```json
{
    "state": "play",
    "track": {
        "title": "Hit FM",
        "artist": "Now playing",
        "album": null,
        "cover_url": "https://.../cover.jpg",
        "source_type": "radio",
        "duration_sec": null
    },
    "volume": 35,
    "position_sec": 12.5,
    "shuffle": false
}
```

Renders a 64 px cover (rotates slowly while playing), title + artist + source-type badge, four round transport buttons (`previous` / `play` / `pause` / `next` — the `play` button is bigger and accent-glowed when active), and a horizontal volume slider with speaker icon.

`track` is `null` when nothing's loaded — the template shows the cover placeholder, "Nothing playing" text, and dims all transport buttons except `play`.

**Actions:**
- `set_mode` accepts `{"id": "play" | "pause" | "stop" | "previous" | "next"}` — dispatches the matching player call.
- `step` accepts `{"id": "volume", "value": <0..100>}`.

#### 3.3.8 `presence` (specialized)

Header pill + grid of user-cards. **Sizes:** `2x1` (compact summary), `2x2` (preferred), max `4x2`.

```json
{
    "summary": {"tone": "info", "text": "2/3 home", "icon": "home"},
    "users": [
        {"id": "u1", "name": "Alice", "state": "home",    "last_seen": "just now"},
        {"id": "u2", "name": "Bob",   "state": "away",    "last_seen": "23m ago"},
        {"id": "u3", "name": "Eve",   "state": "unknown", "last_seen": null,
         "icon": "user-check", "badge": "guest"}
    ],
    "empty_text": "No users registered"
}
```

Each user renders as a card with a gradient avatar circle (initial letter or `icon`), a status dot overlay (green/amber/grey), name, and tone-coloured `last_seen` (or `Home`/`Away`/`—` fallback). Optional `badge` shows as a small uppercase chip. Empty state renders a centred 👤 emoji + `empty_text`.

**Actions:** none in the base template; PIN-gated edit flows live in the module's settings page.

### 3.4 Custom kind

When `kind: "custom"`, the engine renders the existing iframe path with three additions:

1. **Auto-injected design tokens.** Engine writes `<style id="__selena_tokens">` into the iframe `<head>` with all `--bg`, `--sf`, `--ac`, `--gr`, `--am`, `--rd`, `--tx{,2,3}` values for the current theme. Author writes `color: var(--tx)` and inherits theming. Engine re-injects on theme change.
2. **Shared chrome wrapper.** iframe lives inside the same `<WidgetChrome>` used by templates. Authors stop writing their own headers.
3. **Typed postMessage protocol:**

```ts
type WidgetMessage =
    | { type: "ready" }
    | { type: "modal_open"; module: string; width?: number; height?: number }
    | { type: "modal_close"; module: string }
    | { type: "modal_resize"; width: number; height: number }
    | { type: "open_settings"; module: string }
    | { type: "request_refresh" }
    | { type: "theme_changed"; theme: "dark" | "light" };
```

Existing messages (`openWidgetModal`, `closeWidgetModal`, `openSettings`, `modal_resize`) get aliased for one minor version, then removed.

### 3.5 Skeleton & error states

Every widget — template or custom — moves through three states: `loading → data → refreshing... ↘ error ↙`.

**Loading.** While the first response from `data_endpoints[k]` is in flight (or while a custom iframe hasn't sent `ready`), the widget renders a skeleton matching the template's structural shape. Skeletons live in `templates/Skeleton.tsx`.

**Error.** If `data_endpoints[k]` returns non-2xx, times out (>5 s), or the iframe doesn't `ready` within 10 s, the widget renders error chrome: red status dot, "Unavailable" body with retry button. Chrome menu gets "Show details" with the error and request URL.

### 3.6 Refresh model

**Event-driven (preferred).** Manifest declares `refresh.events`. Engine subscribes to those topics on the [`SyncManager`](../core/api/sync_manager.py) WebSocket. On match, engine refetches `data_endpoints[k]`. Latency ~50 ms.

**Polling (fallback).** Manifest declares `refresh.poll_interval_s`. Catches drift, missed-after-reconnect events, and modules that don't publish state events.

Both can coexist. Recommendation: events for write-triggered changes, poll at 30 s for sensor-style data. For custom widgets, matched events are forwarded as `theme_changed` / `request_refresh` postMessage.

### 3.7 Icon system

Modules ship icons by name. The [`Icon`](../src/components/dashboard/templates/Icon.tsx) helper resolves the name against an emoji table and renders a `<span>` styled with the system emoji font (`Apple Color Emoji` / `Segoe UI Emoji` / `Noto Color Emoji` / `Twemoji Mozilla`). No SVG icon library is bundled for dashboard widgets.

```tsx
<Icon name="cloud-rain" size={20} />          // → 🌧️
<Icon name="lightbulb" size={14} />           // → 💡
<Icon name="unknown-key" fallback="✦" />      // → ✦  (graceful fallback)
<Icon name={null} fallback="?" />             // → ?
```

Curated set (~50 names) covers the categories used today: weather (`cloud`, `cloud-rain`, `cloud-snow`, `sun`, `moon`, `wind`, `droplets`, `snowflake`, `thermometer`, `zap`), devices (`lightbulb`, `power`, `tv`, `radio`, `music`, `volume-2`, `mic`), system (`cpu`, `server`, `globe`, `network`, `wifi`, `bluetooth`, `satellite`, `settings`), people (`user`, `user-check`, `user-x`, `home`), status (`check`, `check-circle`, `clock`, `alert-triangle`, `x`, `refresh-cw`, `shield`, `sparkles`, `bell`, `eye`, `activity`), other (`alarm-clock`, `calendar`, `workflow`, `chevron-right`).

Adding new icons: extend `EMOJI_MAP` in `Icon.tsx`. Names follow lucide-react convention (kebab-case) so future migration to vector icons stays mechanical.

### 3.8 Block primitives

Reusable React components in [`templates/blocks/`](../src/components/dashboard/templates/blocks/) shared between specialized and generic templates. Module authors don't import these directly — they ship payload fields that the templates render via these primitives.

| Primitive       | Used by                                              | Payload field name | Shape                                                                       |
|-----------------|------------------------------------------------------|--------------------|-----------------------------------------------------------------------------|
| `Pill`          | Status, Weather (forecast tone), Presence (summary) | implicit           | `{tone: PillTone, text: string, icon?: string}`                             |
| `IconStrip`     | Weather, Status, ControlPanel.secondary_pills        | `strip`, `pills`, `secondary_pills` | `{icon?, value: string, label?, tone?}[]`                                   |
| `CardRow`       | Weather (forecast), Sparkline (breakdown), Status (cards) | `cards`, `breakdown`, `forecast` | `{title?, value: string, secondary?, icon?, tone?}[]` — equal-width grid    |
| `ActionButton`  | Status (header)                                      | `actions`          | `{id: string, label: string, icon?, body?, tone?}` — POSTs to action proxy |

`PillTone`, `IconStripItem`, `CardSpec`, `ActionSpec` are exported types — see file headers for the precise interfaces.

---

## 4. Native vs iframe — decision matrix

**Decision: render template widgets as React components in the parent SPA.**

| Consideration                  | iframe per widget              | React in parent                         |
|--------------------------------|--------------------------------|-----------------------------------------|
| RAM on Pi (20 widgets)         | ~140 MB (7 MB × 20)            | ~25 MB                                  |
| First-paint latency            | ~120 ms × N                    | ~30 ms total                            |
| Animation consistency          | impossible — separate timelines| shared Framer Motion timeline           |
| Hover / focus across widgets   | impossible                     | trivial                                 |
| Theme switching                | re-inject tokens into N iframes| CSS variable cascade                    |
| Isolation guarantee            | strong (cross-origin sandbox)  | weaker — relies on trust review         |

Isolation matters only for **untrusted third-party code**. Templates accept JSON from the module's own backend — no third-party HTML/CSS/JS is rendered. Custom widgets keep iframe isolation because they ship arbitrary HTML/CSS/JS, including marketplace.

---

## 5. Migration plan

### 5.1 Phase 0 — preparation (✅ done)

- ✅ Pydantic schema [`core/module_loader/manifest_schema.py`](../core/module_loader/manifest_schema.py).
- ✅ [`core/module_loader/validator.py`](../core/module_loader/validator.py) delegates to Pydantic.
- ✅ Required `room: str` field added to all 18 system manifests.
- ✅ `POST /api/v1/scenes/{id}/activate` endpoint + EventBus `scene.activate`/`activated`/`failed`.
- ✅ `scene.*` whitelisted in [`core/api/sync_bridge.py`](../core/api/sync_bridge.py).
- ✅ Stub `core/api/routes/module_data.py` for `/api/v1/modules/{name}/data/{key}` and `/action/{key}`.
- ✅ Tests: `tests/test_manifest_schema.py`, `tests/test_scenes_activate.py`.

**Exit criterion:** existing modules continue to start; no UI changes.

### 5.2 Phase 1 — visual layer (5–7 days)

**Behind feature flag `dashboardV2Enabled`. V1 remains default.**

- New components: `Hero.tsx`, `SceneRow.tsx`, `RoomTabs.tsx`, `BentoGrid.tsx`, `WidgetChrome.tsx`, `WidgetFrame.tsx`, `DashboardV2.tsx`.
- Refactor: lift drag/drop/resize/wobble logic from [Dashboard.tsx](../src/components/Dashboard.tsx) into `src/hooks/useBentoEdit.ts`.
- Add tokens `--hero-tint`, `--widget-glow-on`, `--motion-spring`, `--skeleton-bg` to [index.css](../src/index.css).
- `useTimeOfDay()` hook — updates `<html data-tod="...">` every 15 min.
- All existing iframe widgets keep working.
- Bilingual text for hero greeting, scene labels, room tabs.

**Exit criterion:** with `?dashboardV2=1` the dashboard matches §2 mockup; all existing widgets still load.

### 5.3 Phase 2 — template engine + 2 templates (5–7 days)

- `WidgetEngine.tsx`, `templates/Skeleton.tsx`, `templates/registry.ts`.
- Templates: `Metric`, `ToggleList`.
- Full `core/api/routes/module_data.py` (Module Bus dispatch, TTL cache, 800 ms timeout, stale-while-revalidate).
- `useWidgetData()` hook — fetch + EventBus subscribe + poll fallback.
- Migrate: `device-watchdog` → `metric`, `lights-switches` → `toggle-list`.

**Exit criterion:** two real modules render via templates; bento contains template + iframe mix.

### 5.4 Phase 3 — remaining templates + 4 modules (5–7 days)

`Sparkline`, `ControlPanel`, `Status` + migrate `energy-monitor`, `climate`, `cloud-sync`, `integrity-agent`. Pydantic enforces `size` against `template`.

### 5.5 Phase 4 — custom-widget polish (3–4 days)

- Auto-inject design tokens into custom iframes.
- Shared chrome wrapper.
- Typed postMessage contract; deprecation warnings for old names.
- In-place `widgetLayout` v1 → v2 migration; `Reset layout` in Settings.
- **Flip `dashboardV2Enabled` default to `true`.**

### 5.6 Phase 5 — V1 removal (✅ done)

- Delete `widget.html` files from all 13 migrated system modules.
- Delete WidgetShell + DashboardV1 logic from [Dashboard.tsx](../src/components/Dashboard.tsx); file becomes a 9-line wrapper that mounts `DashboardV2`.
- Remove the `dashboardV2Enabled` opt-in (V2 is the only path).
- Aggressive `Cache-Control: no-store` on `/sw.js` and `/manifest.json` via raw ASGI middleware [`NoCacheForPaths`](../core/api/middleware.py); `index.html` ships an inline service-worker unregistration + cache purge so legacy SWs from the `/join` invite flow stop intercepting kiosk reloads.

### 5.7 Phase 6 — specialized templates + emoji-first Icon (✅ done)

- 3 new specialized templates: `weather`, `media`, `presence` — registered in [`templates/registry.ts`](../src/components/dashboard/templates/registry.ts).
- [`Icon.tsx`](../src/components/dashboard/templates/Icon.tsx) rewritten emoji-first (lucide imports dropped from the dashboard bundle, ~45-glyph map covers all current modules).
- Block primitives in [`templates/blocks/`](../src/components/dashboard/templates/blocks/): `Pill`, `IconStrip`, `CardRow`, `ActionButton`.
- Generic templates pick up optional rich slots — `Metric.icon`, `Status.{cards, strip, actions, rows[].icon, pill.icon as lucide name}`, `Sparkline.{icon, breakdown}`, `ControlPanel.secondary_pills`, `ToggleList.items[].icon`.
- 5×4 grid restored from the bento auto-flow tested in Phase 1; explicit `widgetLayout.positions` placement + visible dotted gridlines in edit mode.
- All 14 in-tree widget endpoints now emit Phase-6-shape payloads. Pydantic schema accepts new template names; size envelopes added: `weather`/`media` ≥ 2×2, `presence` ≥ 2×1.
- `media-player` returns to template (the V1 widget.html reintroduced briefly in Phase 5 is removed again — the new `media` template covers cover art + transport + volume scrubber natively).

### 5.8 Backward compatibility guarantees

- Manifests without `widget.kind` keep working as `custom` indefinitely.
- Existing `widget.html` files load and render exactly as before in `kind: "custom"` modules.
- All Phase 6 fields on generic templates are optional — older payloads still render fine (icon slot just renders nothing if absent).
- Status `pill.icon` accepts both legacy short codes (`check`, `clock`, `alert`, `x`, `refresh`) and any [Icon](#37-icon-system) name.
- Removed: pre-Phase-4 postMessage names (`openWidgetModal`, `closeWidgetModal`, `openSettings`, `refresh`) — Phase 5 dropped the aliases. Custom modules must use the canonical `WidgetMessage` types from [`src/lib/widgetMessages.ts`](../src/lib/widgetMessages.ts).

---

## 6. Component map

```
src/
├── components/
│   ├── Dashboard.tsx                  (9-line wrapper — mounts DashboardV2)
│   └── dashboard/
│       ├── DashboardV2.tsx            (composition root)
│       ├── Hero.tsx                   (greeting + clock + status pill + weather)
│       ├── SceneRow.tsx               (chips → POST /scenes/{id}/activate)
│       ├── RoomTabs.tsx               (derived from manifest.room)
│       ├── BentoGrid.tsx              (fixed 5×4 with explicit positioning)
│       ├── WidgetChrome.tsx           (status dot + edit bar + resize handle)
│       ├── WidgetFrame.tsx            (template registry router, iframe fallback)
│       ├── AddWidgetDrawer.tsx        (bottom-sheet for pinning new widgets)
│       └── templates/
│           ├── Icon.tsx               (emoji-first; ~50-name lucide-style map)
│           ├── registry.ts            (8 templates: 5 generic + 3 specialized)
│           ├── Skeleton.tsx           (one Skeleton variant per template)
│           ├── Metric.tsx             (generic — primary value + tone)
│           ├── Sparkline.tsx          (generic — value + chart + breakdown)
│           ├── ToggleList.tsx         (generic — Apple-Home tile grid)
│           ├── ControlPanel.tsx       (generic — primary + modes + steppers)
│           ├── Status.tsx             (generic — pill + rows + cards/actions)
│           ├── Weather.tsx            (specialized — hero + pills + forecast)
│           ├── Media.tsx              (specialized — cover + transport + volume)
│           ├── Presence.tsx           (specialized — avatar circles + state dot)
│           └── blocks/
│               ├── Pill.tsx           (tone + text + icon)
│               ├── IconStrip.tsx      (icon + value horizontal row)
│               ├── CardRow.tsx        (equal-width grid of mini-cards)
│               └── ActionButton.tsx   (POSTs /modules/{name}/action/{id})
├── store/
│   └── useStore.ts                    (Module.room, widgetLayout.version, swapWidgets V2)
├── hooks/
│   ├── useBentoEdit.ts                (drag-to-empty + drag-to-swap + resize)
│   ├── useWidgetData.ts               (fetch + EventBus subscribe + poll)
│   └── useTimeOfDay.ts                (data-tod attribute every 15 min)
├── lib/
│   └── widgetMessages.ts              (typed postMessage protocol)
└── index.css                          (--hero-tint, --widget-glow-on, --motion-spring, --skeleton-bg)

core/
├── module_loader/
│   ├── manifest_schema.py             (Pydantic — 8 template names + size envelopes)
│   └── validator.py                   (delegates to Pydantic)
└── api/
    ├── middleware.py                  (NoCacheForPaths ASGI for /sw.js + /manifest.json)
    ├── routes/
    │   ├── module_data.py             (proxy /api/v1/modules/{name}/data|action/{key})
    │   ├── modules.py                 (ModuleResponse exposes room)
    │   └── scenes.py                  (POST /{id}/activate + scene.* events)
    └── sync_bridge.py                 (whitelist scene.activate / activated / failed)

system_modules/                        (all 18 manifests declare room; 13 ship template payloads)
```

---

## 7. Rejected alternatives

**A. Pure-React widgets, no iframe at all.** Considered and rejected. Marketplace widgets ship third-party JS/CSS — sandboxing matters.

**B. Web Components in Shadow DOM.** Considered. Better than iframe for performance; weaker for JS isolation (Shadow DOM only isolates CSS — third-party JS still has `window` access). Cross-browser quirks on older WebKit (Pi browser kiosk).

**C. Custom widgets via HTMX.** Considered. SPA already on React; introducing a second render paradigm adds complexity. JSON-contract templates work for web AND a future native iOS/Android client without rework.

**D. Glass morphism on light theme.** Contrast ratios fail WCAG AA at 13 px text with `backdrop-filter: blur()`. Light theme uses solid surfaces.

**E. Per-widget user-configurable backgrounds.** Customization at this level pulls focus from data. The dashboard's job is legibility, not personalization.

---

## 8. Open questions

1. **Multi-room widgets.** A `control-panel` for climate could plausibly show three rooms in a `4x4`. Add `siblings: ControlPanelPayload[]` to control-panel, or introduce `multi-control-panel`? Leaning toward the former.
2. **Widget composition.** A scene-preview widget might want three small toggles + a temperature reading. Template-of-templates, or always custom? Custom for now.
3. **Long-press affordance.** Natural on kiosk touchscreen; awkward on mouse. Right-click? Modifier-click? Dedicated `⋯` per item? Decide in Phase 2.
4. **Payload-label i18n.** Via `Accept-Language` header or explicit `?lang=`? Header is cleaner.
5. **Scene management UI.** "+ Add scene" in edit mode? Out of scope, but flagged.
6. **Mobile breakpoint behavior.** Compress hero + wrap scene chips into two rows, or scroll? Compression.

---

## 9. References

- [`widget-development.md`](widget-development.md) — current widget guide. Rewritten in Phase 4.
- [`architecture.md`](architecture.md) — SelenaCore architecture overview.
- [`ui-sync-architecture.md`](ui-sync-architecture.md) — WebSocket sync protocol.
- [`provider-system-and-modules.md`](provider-system-and-modules.md) — analogous architectural recraft document this is structurally modeled on.
- [`module-development.md`](module-development.md) — SDK reference for module authors.
