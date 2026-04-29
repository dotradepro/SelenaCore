# Widget Development Guide

This guide covers how to build UI widgets, settings pages, and icons for SelenaCore modules.

> **Template engine first.** As of the dashboard recraft (Phase 5/6 shipped),
> the primary path for new widgets is the **template engine**: declare a
> payload shape in your manifest and let the dashboard render it. Only fall
> back to custom HTML in an iframe when none of the 8 built-in templates
> fits. See [dashboard-recraft.md](dashboard-recraft.md) §3 for full payload
> schemas — **5 generic templates** (`metric`, `sparkline`, `toggle-list`,
> `control-panel`, `status`) and **3 specialized layouts** (`weather`,
> `media`, `presence`) — plus the `data_endpoints` / `actions` contract,
> the emoji-first [Icon system](dashboard-recraft.md#37-icon-system) and
> the reusable [block primitives](dashboard-recraft.md#38-block-primitives)
> (Pill / IconStrip / CardRow / ActionButton).
>
> This document covers manifest setup, settings pages, icons, and **`kind:
> "custom"` iframe widgets** for the rare case where a template doesn't
> fit. Phase 5 removed the legacy postMessage names (`openWidgetModal`,
> `closeWidgetModal`, `openSettings`, `refresh`) — use the canonical names
> documented in [`src/lib/widgetMessages.ts`](../src/lib/widgetMessages.ts).
> Modules and configuration interfaces are served through the core at
> `/api/ui/modules/{module_name}/`.

---

## Table of Contents

1. [UI Profiles](#ui-profiles)
2. [manifest.json UI Section](#manifestjson-ui-section)
3. [Grid Sizes](#grid-sizes)
4. [Shared Component Library](#shared-component-library)
5. [Widget HTML Structure](#widget-html-structure)
6. [Settings Page](#settings-page)
7. [Communication Patterns](#communication-patterns)
8. [Icon Requirements](#icon-requirements)
9. [Complete Module UI Example](#complete-module-ui-example)
10. [Best Practices](#best-practices)

---

## UI Profiles

Every module declares its UI presence via the `ui_profile` field in `manifest.json`. Choose the profile that matches your module's needs:

| Profile          | Icon | Widget | Settings Page |
|------------------|------|--------|---------------|
| `HEADLESS`       | No   | No     | No            |
| `SETTINGS_ONLY`  | No   | No     | Yes           |
| `ICON_SETTINGS`  | Yes  | No     | Yes           |
| `FULL`           | Yes  | Yes    | Yes           |

A background service with no user-facing controls should use `HEADLESS`. A module that needs configuration but has no dashboard presence should use `SETTINGS_ONLY`. Most interactive modules will use `FULL`.

---

## manifest.json UI Section

Every manifest declares a top-level `room` field (required since Phase 0 — `"system"` for diagnostic modules, `"home"` for cross-room user-facing aggregators, or any custom room name) and an optional `ui` block.

### Template widget (preferred — 13/14 in-tree modules)

```json
{
    "room": "home",
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "kind": "template",
            "template": "control-panel",
            "size": "4x2",
            "max_size": "4x2",
            "data_endpoints": {
                "state": {"path": "/widget/data/state", "cache_ttl_s": 5}
            },
            "actions": {
                "set_mode": {"path": "/widget/action/mode"},
                "step":     {"path": "/widget/action/temp"}
            },
            "refresh": {
                "events": ["device.state_changed"],
                "poll_interval_s": 30
            }
        },
        "settings": "settings.html"
    }
}
```

The dashboard renders the React component matching `template`. Pick from the 8 built-in names: `metric`, `sparkline`, `toggle-list`, `control-panel`, `status`, `weather`, `media`, `presence`. Each has a payload schema documented in [dashboard-recraft.md §3.3](dashboard-recraft.md#33-templates).

### Custom (iframe) widget — fallback

Use `kind: "custom"` only when none of the 8 templates fits (e.g. canvas visualizations, room-plan editors, embedded games):

```json
{
    "room": "home",
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "kind": "custom",
            "file": "widget.html",
            "size": "2x2",
            "max_size": "4x4"
        },
        "settings": "settings.html"
    }
}
```

### Field Reference

| Field                     | Type    | Required           | Description                                                                                  |
|---------------------------|---------|--------------------|----------------------------------------------------------------------------------------------|
| `room`                    | string  | Yes                | Room tag — derives the dashboard's room filter. Use `"system"` for non-user-facing diagnostics. |
| `ui.icon`                 | string  | No                 | Path to the SVG icon file (relative to module root).                                         |
| `ui.widget.kind`          | enum    | No, default custom | `"template"` to use the engine; `"custom"` to ship `widget.html` in an iframe.               |
| `ui.widget.template`      | enum    | When kind=template | One of the 8 built-in template names. See dashboard-recraft.md §3.3.                         |
| `ui.widget.data_endpoints[k]` | `{path, cache_ttl_s?}` | No | Path on the module's HTTP surface; dashboard hits `GET /api/v1/modules/{name}/data/{k}`.     |
| `ui.widget.actions[k]`    | `{path}`| No                 | Path for write actions; dashboard hits `POST /api/v1/modules/{name}/action/{k}`.             |
| `ui.widget.refresh.events` | string[] | No                | EventBus topics that trigger a dashboard refetch (e.g. `device.state_changed`).              |
| `ui.widget.refresh.poll_interval_s` | int (≥1) | No        | Polling fallback interval in seconds.                                                        |
| `ui.widget.file`          | string  | When kind=custom   | HTML file for the iframe. Ignored for `kind: "template"`.                                    |
| `ui.widget.size`          | string  | No                 | Default grid size (`"WxH"`, e.g. `"4x2"`).                                                   |
| `ui.widget.max_size`      | string  | No                 | Maximum grid size the user can resize to (V2 dashboard uses fixed 5×4 — span clamped).       |
| `ui.settings`             | string  | No                 | HTML file for the module settings page.                                                      |

All file paths are relative to the module's root directory.

---

## Grid Sizes

The dashboard uses a grid layout. Widgets occupy cells defined by `WidthxHeight`:

| Size  | Description          | Use Case                          |
|-------|----------------------|-----------------------------------|
| `1x1` | Small square         | Single value display, toggle      |
| `2x1` | Wide small           | Value with label, compact status  |
| `1x2` | Tall small           | Vertical list, small chart        |
| `2x2` | Medium square        | Primary widget size (default)     |
| `4x2` | Wide large           | Graphs, multi-value dashboards    |
| `4x4` | Full large           | Complex controls, camera feeds    |

Set `size` to the default and `max_size` to the largest the widget can scale to. The dashboard will not allow users to resize beyond `max_size`.

---

## Shared Component Library

Every widget and settings page runs inside an iframe and loads two shared assets from the core. **Always include them in your `<head>`:**

```html
<link rel="stylesheet" href="/api/shared/theme.css">
<script src="/api/shared/widget-common.js"></script>
```

This gives you a full component library — theme tokens, cards, buttons, forms, badges, toasts, modals, toggles, chips, status dots — plus JS helpers for `fetch`, toast notifications, loading states, tabs, and localization. **In most cases, your module should not need a `<style>` block at all.**

Canonical starters: [`docs/module-ui-template/widget.template.html`](module-ui-template/widget.template.html) and [`docs/module-ui-template/settings.template.html`](module-ui-template/settings.template.html). Copy one and edit.

### Automatic body layout

`widget-common.js` automatically tags `<body>` based on filename:

| File | Auto-applied class | Effect |
|---|---|---|
| `widget.html` | `body.sc-widget` | Transparent background, no scroll — blends into the dashboard tile. |
| `settings.html` | `body.sc-settings` | Padded (`20px`), scrollable, `800px` max-width, centered. |

If your module needs a different layout, set one of those classes on `<body>` yourself — the auto-applier will leave your choice alone. To opt out entirely (your module has a custom full-viewport body with its own background, padding, or sticky header that neither preset fits), use `<body class="sc-custom">`.

### Component cheat sheet

| Use case | Class or element |
|---|---|
| Card container | `.card` (main), `.card-inner` (nested) |
| Section heading | `h2` + `.subtitle`, or `.section-title` + `.section-sub` |
| Small label / hint | `.label-sm`, `.label-xs` |
| Primary action button | `.btn .btn-primary` (aliased: `.btn-blue`) |
| Secondary button | `.btn .btn-secondary` or `.btn .btn-outline` |
| Destructive button | `.btn .btn-danger` (soft) / `.btn-danger-solid` (solid) |
| Success button | `.btn .btn-green` |
| Ghost / link button | `.btn .btn-ghost`, `.btn-link` |
| Icon-only button | `.icon-btn` (+ `.icon-btn-sm` / `.icon-btn-lg`) |
| Form field (label + input) | `.field` wrapping `<label>` + `<input>` |
| Two-column form row | `.field-row` wrapping two `.field`s |
| Text inputs | `input[type="text|number|password"]`, `textarea`, `select` — already styled |
| Slider | `input[type="range"]` + `.slider-row` / `.slider-header` |
| Toggle switch | `.toggle` (contains `<input type="checkbox">` + `.slider`) |
| Chip picker | `.chip-picker` + `.chip` (with `.on` / `.active`) |
| Status pill | `.badge` + `.badge-ok` / `-err` / `-warn` / `-info` / `-pr` |
| Status dot | `.status-dot` + `.ok` / `.warn` / `.err` / `.info` |
| Tab strip | `.settings-tabs` + `.settings-tab` + `.tab-panel` (call `initTabs()`) |
| Data table | `<table>` (already styled — no class needed) |
| Progress bar | `.progress-bar` + `.progress-bar .fill` |
| Toast notification | Call `showToast(msg, 'success'|'error'|'info')` |
| Modal dialog | `.modal-overlay` > `.modal` |
| Bottom sheet editor | `.sheet-overlay` > `.sheet` + `.sheet-actions` |
| List of rows | `.list` > `.list-row` (+ `.clickable` / `.off`) |
| Empty state | `.empty-state` + `.es-title` |
| Floating action button | `.fab` |
| Fixed stat grid (2-col) | `.stat-grid` + `.stat-card` + `.num` + `.desc` |
| Generic grid | `.grid-2`, `.grid-3`, `.grid-4`, `.grid-auto` |
| KPI hero (big number + caption) | `.kpi` > `.kpi-val` (+ `-accent` / `-success` / `-warn` / `-danger`) + `.kpi-lbl` |
| Vertical rhythm | `.stack` / `.stack-sm` / `.stack-lg` on a container |
| Horizontal row | `.row`, `.flex`, `.flex-col`, `.wrap`, `.flex1` |
| Spacing utilities | `.gap4` … `.gap16`, `.mb4` … `.mb16`, `.mt4` … `.mt16` |
| Spinner / skeleton | `.spinner`, `.skeleton`, `.pulse` |
| Divider | `.divider-dashed` |
| Hide element | `.hidden` |
| Monospaced text | `.mono` |

### JS helpers (from `widget-common.js`)

```js
// Fetch — BASE is auto-computed, auth headers auto-included
apiGet('/status').then(data => { … });
apiPost('/settings', { city: 'Kyiv' }).then(() => { … });
apiDelete('/items/42');
apiPatch('/config', { enabled: true });

// Toast (bridges to parent dashboard too)
showToast('Saved', 'success');
showToast('Connection failed', 'error');
showToast('Restarting…', 'info');

// Button loading state — disables the button, shows a spinner,
// catches errors and toasts them automatically
withLoading(btnElement, () => apiPost('/action'));

// DOM helpers
$('my-id');        // document.getElementById
show('my-id');     // removes .hidden
hide('my-id');
esc(userString);   // HTML-escape before setting innerHTML

// Tab switching — buttons with [data-tab="x"] activate #tabX panel
initTabs();
```

### Localization

Define `L = { en: {...}, uk: {...} }`, then tag markup with i18n attributes:

```html
<h2 data-i18n="title"></h2>
<input data-placeholder-i18n="ph_name">
<button data-i18n="save" data-i18n-title="save_tip"></button>
<span data-i18n-aria-label="lbl_status"></span>
```

Call `applyLang()` once on load. The current language is `LANG` (auto-read from `localStorage['selena-lang']`), and `t(key)` returns the translated string. When the user switches language in the parent dashboard, a `lang_changed` postMessage re-runs `applyLang()` and calls your `refresh()` / `load()` / `loadStatus()` function if present.

### Theme tokens (for module-specific styles only)

If you genuinely need custom CSS for a specialized visual, use these CSS custom properties so your styles track the active theme:

| Token | Purpose |
|---|---|
| `--bg` / `--sf` / `--sf2` / `--sf3` | Background layers (app → surface → elevated → deepest) |
| `--b` / `--b2` | Borders (subtle → strong) |
| `--tx` / `--tx2` / `--tx3` | Text (primary → secondary → tertiary) |
| `--ac` | Accent (blue) |
| `--gr` / `--am` / `--rd` | Semantic colors (success / warning / danger) |
| `--on-accent` / `--on-success` / `--on-warning` / `--on-danger` | WCAG AA-paired text colors for use on top of the saturated fills above |
| `--shadow` / `--shadow-lg` | Soft / pronounced drop shadows |

All tokens flip automatically between light/dark and adapt to `has-wallpaper` mode. Never hardcode hex colors in module CSS.

---

## Widget HTML Structure

> **Applies only to `kind: "custom"`.** For template widgets (`kind: "template"`)
> the dashboard renders a React component from your JSON payload — no HTML
> file is needed. See [dashboard-recraft.md §3.3](dashboard-recraft.md#33-templates)
> for payload schemas. The rest of this section describes the iframe path
> for the rare cases where templates don't fit.

Custom widgets are embedded as iframes in the dashboard. Each widget is a self-contained HTML file that loads the shared component library (see previous section) and adds module-specific markup + script.

### Minimal Example

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: transparent;
            color: var(--text-color, #333);
            padding: 12px;
        }
        .widget-title { font-size: 14px; font-weight: 600; margin-bottom: 8px; }
        .widget-value { font-size: 32px; font-weight: 700; }
        .widget-label { font-size: 12px; color: var(--text-secondary, #666); }
    </style>
</head>
<body>
    <div class="widget-title">Weather</div>
    <div class="widget-value" id="temp">--°C</div>
    <div class="widget-label" id="condition">Loading...</div>

    <script>
        // Communicate with module backend via parent window postMessage
        // or fetch from module API endpoint
        async function loadData() {
            try {
                const res = await fetch('/api/ui/modules/weather-module/current');
                const data = await res.json();
                document.getElementById('temp').textContent = data.temperature + '°C';
                document.getElementById('condition').textContent = data.condition;
            } catch (e) {
                document.getElementById('condition').textContent = 'Error loading data';
            }
        }
        loadData();
        setInterval(loadData, 60000); // Refresh every minute
    </script>
</body>
</html>
```

### Key Points

- **Background must be `transparent`** so the widget blends into the dashboard tile.
- **Inline all CSS and JS.** External stylesheets and scripts add latency and complexity.
- **Set the viewport meta tag** to ensure correct scaling on all devices.
- **Use the reset block** (`* { margin: 0; padding: 0; box-sizing: border-box; }`) to avoid browser default inconsistencies inside the iframe.

---

## Settings Page

Settings pages let users configure module behavior. They are rendered in a larger viewport than widgets and can use standard form elements.

### Example

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        body { font-family: sans-serif; padding: 16px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; margin-bottom: 4px; font-weight: 600; }
        input, select { width: 100%; padding: 8px; border: 1px solid #ccc; border-radius: 4px; }
        button { padding: 8px 16px; background: #007AFF; color: white; border: none; border-radius: 4px; cursor: pointer; }
        button:hover { background: #005EC4; }
        .status { margin-top: 12px; font-size: 14px; }
        .status.success { color: #28a745; }
        .status.error { color: #dc3545; }
    </style>
</head>
<body>
    <h2>Weather Settings</h2>
    <div class="form-group">
        <label>City</label>
        <input type="text" id="city" value="Kyiv">
    </div>
    <div class="form-group">
        <label>Units</label>
        <select id="units">
            <option value="celsius">Celsius</option>
            <option value="fahrenheit">Fahrenheit</option>
        </select>
    </div>
    <button onclick="saveSettings()">Save</button>
    <div class="status" id="status"></div>

    <script>
        async function loadSettings() {
            try {
                const res = await fetch('/api/ui/modules/weather-module/settings');
                const data = await res.json();
                document.getElementById('city').value = data.city || '';
                document.getElementById('units').value = data.units || 'celsius';
            } catch (e) {
                showStatus('Failed to load settings', 'error');
            }
        }

        async function saveSettings() {
            const settings = {
                city: document.getElementById('city').value,
                units: document.getElementById('units').value,
            };
            try {
                const res = await fetch('/api/ui/modules/weather-module/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(settings),
                });
                if (res.ok) {
                    showStatus('Settings saved', 'success');
                } else {
                    showStatus('Save failed: ' + res.statusText, 'error');
                }
            } catch (e) {
                showStatus('Network error', 'error');
            }
        }

        function showStatus(message, type) {
            const el = document.getElementById('status');
            el.textContent = message;
            el.className = 'status ' + type;
        }

        loadSettings();
    </script>
</body>
</html>
```

Settings pages should always:

1. **Load existing values on page open** so the user sees current configuration.
2. **Show feedback after save** (success or error message).
3. **Validate inputs client-side** before sending to the backend.

---

## Communication Patterns

How a widget talks to its module backend depends on the module type.

### SYSTEM Modules

System modules run inside the core process and expose REST endpoints via `get_router()`. Widgets fetch directly from these endpoints:

```javascript
// Direct fetch to the module's registered routes
const res = await fetch('/api/ui/modules/{module-name}/endpoint');
const data = await res.json();
```

No authentication is required. UI routes are localhost-only, protected at the network level by iptables.

### User Modules

User modules run in separate processes and communicate via the Module Bus. The core acts as an API proxy, forwarding requests to the module:

```javascript
// Routed through the core's module API proxy
const res = await fetch('/api/ui/modules/{module-name}/api/endpoint');
const data = await res.json();
```

Alternatively, implement the `handle_api_request()` method in your `SmartHomeModule` subclass to handle incoming API requests programmatically.

### Typed postMessage protocol (custom widgets ↔ dashboard)

Custom iframe widgets communicate with the dashboard chrome via a fixed message contract. Phase 5 removed the legacy aliases; the only accepted shapes are:

```ts
type WidgetMessage =
    | { type: "ready" }                                                          // sent by iframe on load
    | { type: "modal_open"; module: string; width?: number; height?: number }    // expand to fullscreen
    | { type: "modal_close"; module: string }                                    // collapse from fullscreen
    | { type: "modal_resize"; width: number; height: number }                    // resize hint
    | { type: "open_settings"; module: string }                                  // navigate to settings page
    | { type: "request_refresh" }                                                // ask dashboard to refetch
    | { type: "theme_changed"; theme: "dark" | "light" };                        // sent by core on theme switch
```

```javascript
// iframe → parent: open this widget in a fullscreen modal
window.parent.postMessage({type: 'modal_open', module: 'lights-switches', width: 480, height: 560}, '*');

// iframe → parent: close the modal
window.parent.postMessage({type: 'modal_close', module: 'lights-switches'}, '*');

// iframe → parent: open the module's settings page
window.parent.postMessage({type: 'open_settings', module: 'lights-switches'}, '*');
```

Removed in Phase 5: `openWidgetModal`, `closeWidgetModal`, `openSettings`, `refresh` — pre-Phase-4 aliases. They no longer reach the dashboard handler. The canonical names above are the only accepted form. See [`src/lib/widgetMessages.ts`](../src/lib/widgetMessages.ts) for the runtime normalizer.

### Real-Time Updates

For widgets that need live data (sensor readings, device states), use periodic polling with `setInterval`. Choose an interval that balances freshness against resource usage:

```javascript
// Poll every 30 seconds for sensor data
setInterval(async () => {
    const res = await fetch('/api/ui/modules/sensors/latest');
    const data = await res.json();
    updateDisplay(data);
}, 30000);
```

Recommended polling intervals:

| Data type            | Interval     |
|----------------------|--------------|
| Temperature, humidity | 30-60 sec   |
| Device status        | 10-30 sec    |
| Counters, statistics | 60-300 sec   |
| Critical alerts      | Use SSE      |

Avoid intervals shorter than 5 seconds unless the data genuinely changes that often.

---

## Icon Requirements

Module icons appear in the dashboard sidebar and on widget tiles.

| Requirement     | Value                                            |
|-----------------|--------------------------------------------------|
| Format          | SVG                                              |
| viewBox         | `0 0 24 24` (recommended)                        |
| Color           | Use `currentColor` for theme compatibility       |
| File location   | Module root directory                            |
| File name       | Declared in `manifest.json` under `ui.icon`      |

### Example Icon

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
     stroke="currentColor" stroke-width="2" stroke-linecap="round"
     stroke-linejoin="round">
    <path d="M12 2v2"/>
    <circle cx="12" cy="12" r="5"/>
    <path d="M12 20v2"/>
    <path d="M4.93 4.93l1.41 1.41"/>
    <path d="M17.66 17.66l1.41 1.41"/>
    <path d="M2 12h2"/>
    <path d="M20 12h2"/>
    <path d="M4.93 19.07l1.41-1.41"/>
    <path d="M17.66 6.34l1.41-1.41"/>
</svg>
```

Using `currentColor` means the icon automatically matches the surrounding text color, adapting to both light and dark themes without any extra work.

---

## Complete Module UI Example

Below is the full file structure for a module with `FULL` UI profile:

```
modules/
  my-sensor-module/
    manifest.json
    icon.svg
    widget.html
    settings.html
    module.py
```

**manifest.json:**

```json
{
    "name": "my-sensor-module",
    "version": "1.0.0",
    "description": "Temperature and humidity sensor display",
    "ui_profile": "FULL",
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "file": "widget.html",
            "size": "2x1",
            "max_size": "2x2"
        },
        "settings": "settings.html"
    }
}
```

**widget.html:**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: transparent;
            color: var(--text-color, #333);
            padding: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            height: 100vh;
        }
        .reading { text-align: center; flex: 1; }
        .reading .value { font-size: 28px; font-weight: 700; }
        .reading .label {
            font-size: 11px;
            color: var(--text-secondary, #666);
            margin-top: 4px;
        }
        .divider {
            width: 1px;
            height: 40px;
            background: var(--border-color, #e0e0e0);
            margin: 0 8px;
        }
        .error {
            text-align: center;
            width: 100%;
            color: var(--text-secondary, #999);
            font-size: 13px;
        }
    </style>
</head>
<body>
    <div id="content">
        <span class="error">Loading...</span>
    </div>

    <script>
        async function refresh() {
            try {
                const res = await fetch('/api/ui/modules/my-sensor-module/readings');
                if (!res.ok) throw new Error(res.statusText);
                const data = await res.json();

                document.getElementById('content').innerHTML = `
                    <div class="reading">
                        <div class="value">${data.temperature.toFixed(1)}°C</div>
                        <div class="label">Temperature</div>
                    </div>
                    <div class="divider"></div>
                    <div class="reading">
                        <div class="value">${data.humidity.toFixed(0)}%</div>
                        <div class="label">Humidity</div>
                    </div>
                `;
            } catch (e) {
                document.getElementById('content').innerHTML =
                    '<span class="error">Sensor unavailable</span>';
            }
        }
        refresh();
        setInterval(refresh, 30000);
    </script>
</body>
</html>
```

---

## Best Practices

1. **Keep widgets lightweight.** Minimize JavaScript and CSS. Avoid heavy frameworks inside widget iframes.
2. **Use CSS custom properties for theming.** This ensures visual consistency across the dashboard in both light and dark modes.
3. **Handle errors gracefully.** Always show fallback content (a dash, "Unavailable", or a retry prompt) instead of leaving the widget blank or showing a stack trace.
4. **Set `background: transparent`.** The dashboard tile provides the card background. A widget with its own opaque background will look out of place.
5. **Refresh data at sensible intervals.** Every 30-60 seconds is appropriate for most sensor data. Do not poll more often than every 5 seconds.
6. **Make widgets responsive.** The widget must look correct at its default `size` and at every size up to `max_size`. Use relative units and flexbox.
7. **Use SVG for icons.** SVG scales cleanly at any resolution and supports `currentColor` for automatic theme adaptation.
8. **Load settings on page open.** A settings page that opens with empty fields forces the user to re-enter values they already configured.
9. **Validate before saving.** Check required fields and value ranges client-side before sending the POST request.
10. **Show save feedback.** Always confirm success or report failure after the user clicks Save.
