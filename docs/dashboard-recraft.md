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

### 2.5 Bento grid

`display: grid; grid-template-columns: repeat(N, minmax(0, 1fr)); grid-auto-flow: dense; gap: 10px`, where N depends on screen (3 tablet, 4 desktop, 6 1080p kiosk, 1 phone). Per-widget `grid-column: span W; grid-row: span H` from the manifest's `WxH`.

`dense` flow is intentional: the browser fills early gaps with later small widgets, producing a denser bento layout without manual placement.

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

Each template specifies: purpose, recommended sizes, payload schema, action contract, render guarantees.

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

`series` ≤ 60 points for visual clarity. Sparkline auto-scales, Y-bounds `[min, max]` with 8 % padding, no gridlines.

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

`pill.tone` ∈ {ok, info, warn, alert, neutral}. `pill.icon` ∈ {check, clock, alert, x, refresh}. Up to 4 rows. **Actions:** optional `refresh`.

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

### 5.6 Phase 5 — V1 removal (ongoing)

- Delete `widget.html` files from all system modules.
- Delete WidgetShell logic from [Dashboard.tsx](../src/components/Dashboard.tsx); file becomes thin DashboardV2 wrapper or is removed.
- Delete iframe path from `WidgetFrame.tsx`. Marketplace modules continue via `kind: "custom"` with full iframe chrome.
- Delete old postMessage aliases.

### 5.7 Backward compatibility guarantees

- Manifests without `widget.kind` keep working as `custom` indefinitely.
- Existing `widget.html` files load and render exactly as before.
- Old postMessage names work in custom mode with a deprecation warning for one major version.
- Fixed 5×4 grid replaced by `auto-flow: dense`, but widget sizes (`WxH`) are honored exactly.

---

## 6. Component map

```
src/
├── components/
│   ├── Dashboard.tsx               (refactor: feature-flag branch)
│   └── dashboard/
│       ├── DashboardV2.tsx         (new)
│       ├── Hero.tsx                (new)
│       ├── SceneRow.tsx            (new)
│       ├── RoomTabs.tsx            (new)
│       ├── BentoGrid.tsx           (new)
│       ├── WidgetChrome.tsx        (new)
│       ├── WidgetFrame.tsx         (new — iframe + template router)
│       └── templates/
│           ├── Metric.tsx          (new)
│           ├── Sparkline.tsx       (new)
│           ├── ToggleList.tsx      (new)
│           ├── ControlPanel.tsx    (new)
│           ├── Status.tsx          (new)
│           ├── Skeleton.tsx        (new)
│           └── registry.ts         (new)
├── store/
│   └── useStore.ts                 (extend: dashboardV2Enabled, widgetLayout.version)
├── hooks/
│   ├── useBentoEdit.ts             (new — lifted drag/drop/resize)
│   ├── useWidgetData.ts            (new)
│   └── useTimeOfDay.ts             (new)
└── index.css                       (new tokens from §2.6)

core/
├── module_loader/
│   ├── manifest_schema.py          ✅ Phase 0
│   └── validator.py                ✅ Phase 0
└── api/
    ├── routes/
    │   ├── module_data.py          ✅ Phase 0 (stub → Phase 2 full)
    │   └── scenes.py               ✅ Phase 0 (+ /activate)
    └── sync_bridge.py              ✅ Phase 0 (+ scene.* whitelist)

system_modules/                     ✅ all 18 manifests now declare room
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
