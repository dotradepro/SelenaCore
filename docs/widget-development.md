# Widget Development Guide

This guide covers how to build UI widgets, settings pages, and icons for SelenaCore modules. Modules can provide dashboard widgets and configuration interfaces served through the core at `/api/ui/modules/{module_name}/`.

---

## Table of Contents

1. [UI Profiles](#ui-profiles)
2. [manifest.json UI Section](#manifestjson-ui-section)
3. [Grid Sizes](#grid-sizes)
4. [Widget HTML Structure](#widget-html-structure)
5. [Settings Page](#settings-page)
6. [Communication Patterns](#communication-patterns)
7. [Theming](#theming)
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

Add a `ui` block to your module's `manifest.json` to declare all UI assets:

```json
{
    "ui": {
        "icon": "icon.svg",
        "widget": {
            "file": "widget.html",
            "size": "2x2",
            "max_size": "4x4"
        },
        "settings": "settings.html"
    }
}
```

### Field Reference

| Field             | Type   | Description                                          |
|-------------------|--------|------------------------------------------------------|
| `ui.icon`         | string | Path to the SVG icon file (relative to module root)  |
| `ui.widget.file`  | string | HTML file for the dashboard widget                   |
| `ui.widget.size`  | string | Default grid size (`"WxH"`, e.g. `"2x2"`)           |
| `ui.widget.max_size` | string | Maximum grid size the user can resize to          |
| `ui.settings`     | string | HTML file for the module settings page               |

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

## Widget HTML Structure

Widgets are embedded as iframes in the dashboard. Each widget must be a self-contained HTML file with inline styles and scripts.

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

## Theming

The dashboard injects CSS custom properties into widget iframes. Use these variables so your widget adapts to the user's light or dark theme:

```css
body {
    background: transparent;
    color: var(--text-color, #333);
}

.secondary-text {
    color: var(--text-secondary, #666);
}

.card {
    background: var(--surface-color, #ffffff);
    border: 1px solid var(--border-color, #e0e0e0);
    border-radius: 8px;
}

.accent {
    color: var(--accent-color, #007AFF);
}
```

Always provide a fallback value (the second argument to `var()`) so the widget renders correctly even if the theme variables are not yet injected.

### Supporting Dark Mode

```css
/* Fallback dark mode if CSS variables are not provided */
@media (prefers-color-scheme: dark) {
    body {
        color: var(--text-color, #e0e0e0);
    }
    .secondary-text {
        color: var(--text-secondary, #999);
    }
}
```

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
