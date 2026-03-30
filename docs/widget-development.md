# Widget & Settings UI Development Guide
**SelenaCore · UI Module Reference**

This document describes how to properly write `widget.html` and `settings.html` for SelenaCore modules. Read it **in full** before building any module UI.

---

## Table of Contents

1. [How the dashboard grid works](#1-how-the-dashboard-grid-works)
2. [Key rule: the widget lives in an iframe](#2-key-rule-the-widget-lives-in-an-iframe)
3. [Required CSS template](#3-required-css-template)
4. [BASE URL — the only correct approach](#4-base-url--the-only-correct-approach)
5. [Cell sizes and adaptation](#5-cell-sizes-and-adaptation)
6. [Fetching data from the module](#6-fetching-data-from-the-module)
7. [Requests to Core API from the widget](#7-requests-to-core-api-from-the-widget)
8. [Realtime: SSE and postMessage](#8-realtime-sse-and-postmessage)
9. [settings.html — rules](#9-settingshtml--rules)
10. [Full widget.html template](#10-full-widgethtml-template)
11. [Full settings.html template](#11-full-settingshtml-template)
12. [Pre-submission checklist](#12-pre-submission-checklist)
13. [Common mistakes](#13-common-mistakes)

---

## 1. How the dashboard grid works

UI Core (:80) builds the dashboard as a CSS grid. Every module with status `RUNNING` and `ui_profile != HEADLESS` gets a cell in this grid. The cell size is defined in `manifest.json`:

```json
"ui": {
  "widget": {
    "file": "widget.html",
    "size": "2x1"
  }
}
```

| `size` value | Width | Height | Typical use |
|---|---|---|---|
| `1x1` | 1 column | 1 row | Simple indicator, counter |
| `2x1` | 2 columns | 1 row | Compact status with details |
| `1x2` | 1 column | 2 rows | Narrow vertical list |
| `2x2` | 2 columns | 2 rows | Full-featured widget with chart/forecast |
| `4x1` | full width | 1 row | Horizontal panel |

UI Core creates the following for each module:

```html
<iframe
  src="http://localhost:{port}/widget.html?ui_token=..."
  sandbox="allow-scripts allow-same-origin"
  scrolling="no"
  style="width: {N*cell_px}px; height: {M*row_px}px; border: none;"
/>
```

The exact pixel dimensions depend on the UI Core configuration (screen resolution, number of columns). **The widget does not know and should not know the exact pixels** — it simply fills 100% of the allocated iframe.

---

## 2. Key rule: the widget lives in an iframe

An iframe is an isolated document of fixed size. Inside it:

- **no `100vh`** — `vh` is calculated from the height of the iframe itself, which already has a fixed height. This leads to overflow.
- **no scrolling** — `scrolling="no"` is set by the parent. Content that does not fit is clipped.
- **no access to parent DOM** — `sandbox` forbids `window.parent`, `window.top`, `document.cookie`.
- **no `alert()`, `confirm()`, `prompt()`** — blocked by sandbox.
- **no localStorage/sessionStorage** — `allow-same-origin` is present, but relying on storage across reloads is not recommended; data should come from the module API.

```
┌─────────────────────────────────┐
│  UI Core dashboard (browser)    │
│                                  │
│  ┌──────────┐  ┌──────────────┐ │
│  │ iframe   │  │ iframe 2x2   │ │
│  │ 1x1      │  │              │ │
│  │ widget   │  │  widget.html │ │
│  └──────────┘  │  fills       │ │
│                │  100%x100%   │ │
│  ┌─────────────┤  of cell     │ │
│  │ iframe 4x1  └──────────────┘ │
│  └─────────────────────────────┘│
└─────────────────────────────────┘
```

---

## 3. Required CSS template

This is the only correct way to start `widget.html`. Deviating from it is the #1 cause of rendering bugs.

```css
/* ── Reset — widget in iframe fills the grid cell ── */
*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

/* html and body — exactly 100% of iframe */
html, body {
  width: 100%;
  height: 100%;
  overflow: hidden;       /* ← required, scrolling="no" on the parent */
  background: transparent; /* or your own color */
}

/* Root container — fills everything */
.root {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  /* padding to taste */
  padding: 14px 16px;
  gap: 10px;
  overflow: hidden;       /* ← content does not escape the bounds */
}
```

**Forbidden:**

```css
/* ❌ breaks rendering */
body { min-height: 100vh; }
body { height: 100vh; }
.container { min-height: 100vh; }

/* ❌ causes scroll inside the iframe */
body { overflow: auto; }
body { overflow-y: scroll; }

/* ❌ content escapes the cell bounds */
.widget { position: fixed; }
.popup  { position: fixed; }
```

---

## 4. BASE URL — the only correct approach

The widget and settings are loaded at a path like:

```
# User module (Docker container)
http://localhost:8115/widget.html?ui_token=...

# System module (in-process, mounted in core FastAPI)
http://localhost:7070/api/ui/modules/weather-service/widget.html?ui_token=...
```

The path to the module API always matches the directory where the widget resides. You need to derive it from the current URL:

```javascript
// ✅ Correct — works for both module types
const BASE = window.location.pathname
  .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

// Examples:
// /widget.html                           → ''
// /api/ui/modules/weather-service/widget → '/api/ui/modules/weather-service'

// Usage:
fetch(BASE + '/weather/current')
fetch(BASE + '/status')
```

```javascript
// ❌ Wrong — hardcoded port
const BASE = 'http://localhost:8115';

// ❌ Wrong — does not account for system module path prefix
const BASE = window.location.origin;

// ❌ Wrong — relative path without BASE
fetch('/weather/current');
```

---

## 5. Cell sizes and adaptation

The widget does not know its exact size in pixels, but it can obtain it at runtime and adapt:

```javascript
// Determine size at startup and on change (the parent may resize the iframe)
function checkLayout() {
  const root = document.getElementById('root');
  const w = root.offsetWidth;
  const h = root.offsetHeight;

  // Compact mode for small cells (1x1, 2x1)
  root.classList.toggle('compact', h < 160);

  // Wide mode for 4x1
  root.classList.toggle('wide', w > 600 && h < 160);
}

checkLayout();
window.addEventListener('resize', checkLayout);
```

**Recommended thresholds:**

| Condition | Class | Behavior |
|---|---|---|
| `height < 160px` | `.compact` | Hide secondary details, reduce font size |
| `height > 300px` | `.expanded` | Show extended content, charts |
| `width > 500px` | `.wide` | Horizontal layout instead of vertical |

**CSS adaptation example:**

```css
/* Base view (2x1, 2x2) */
.detail-text   { display: block; }
.main-temp     { font-size: 2.2rem; }
.forecast-cond { display: block; }

/* Compact (1x1, narrow cells) */
.compact .detail-text   { display: none; }
.compact .main-temp     { font-size: 1.6rem; }
.compact .forecast-cond { display: none; }

/* Wide (4x1) */
.wide .layout   { flex-direction: row; }
.wide .forecast { grid-template-columns: repeat(5, 1fr); }
```

---

## 6. Fetching data from the module

The widget fetches data **only** from its own module via HTTP requests to its API:

```javascript
const BASE = window.location.pathname.replace(/\/(widget|settings)(\.html)?$/, '');

async function load() {
  try {
    const data = await fetch(BASE + '/status').then(r => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    });
    render(data);
  } catch (e) {
    showError(e.message);
  }
}

// Initial load
load();

// Auto-refresh (choose an appropriate interval)
setInterval(load, 30_000); // every 30 seconds
```

**Recommended polling intervals:**

| Data type | Interval |
|---|---|
| Temperature, humidity | 30–60 sec |
| Device status | 10–30 sec |
| Counters, statistics | 60–300 sec |
| Critical alerts | SSE (see section 8) |

---

## 7. Requests to Core API from the widget

If the widget needs data directly from the Core API (device list, etc.), UI Core passes `ui_token` via a query parameter:

```javascript
// Get ui_token from URL
const uiToken = new URLSearchParams(window.location.search).get('ui_token');

// Request to Core API
const devices = await fetch('http://localhost:7070/api/v1/devices', {
  headers: { 'Authorization': `Bearer ${uiToken}` }
}).then(r => r.json());
```

**`ui_token` limitations:**
- Read-only: `device.read`, `events.subscribe`
- TTL: 1 hour (the widget will reload the page on 401)
- Cannot write devices, publish events, or manage modules

```javascript
// Handling an expired token
async function coreRequest(url) {
  const res = await fetch(url, {
    headers: { 'Authorization': `Bearer ${uiToken}` }
  });
  if (res.status === 401) {
    // Token expired — silently reload the iframe
    window.location.reload();
    return null;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
```

---

## 8. Realtime: SSE and postMessage

### SSE from the module (recommended for realtime data)

```javascript
const BASE = window.location.pathname.replace(/\/(widget|settings)(\.html)?$/, '');

const es = new EventSource(BASE + '/events/stream');

es.addEventListener('state_changed', (e) => {
  const data = JSON.parse(e.data);
  updateUI(data);
});

es.onerror = () => {
  // Reconnection — EventSource handles this automatically
  console.warn('SSE reconnecting...');
};
```

### postMessage with parent (UI Core → widget only)

Sandbox allows `allow-scripts allow-same-origin` — `postMessage` works in one direction: from parent to iframe. The widget can listen for messages from UI Core:

```javascript
window.addEventListener('message', (e) => {
  // Verify the source — only from the same origin
  if (e.origin !== window.location.origin) return;

  if (e.data?.type === 'theme_changed') {
    applyTheme(e.data.theme);
  }
  if (e.data?.type === 'refresh') {
    load();
  }
});
```

**Not allowed:** sending messages from the iframe to the parent (blocked by sandbox without `allow-same-origin` + explicit `targetOrigin`).

---

## 9. settings.html — rules

The settings page opens in a **separate modal window** of UI Core, not in the dashboard grid. Rules:

- Size is **not fixed** — the page is displayed in a scrollable modal window.
- `overflow: auto` on body is **allowed** — scrolling is needed here.
- `100vh` is **forbidden** for the same reason — use `min-height: 100%`.
- Forms save data **via the module API**, not via `localStorage`.

```css
/* settings.html — body */
html, body {
  width: 100%;
  min-height: 100%;  /* ← not 100vh */
  overflow-y: auto;  /* ← scrolling is allowed here */
  background: #0e1220;
  color: #e8eaf0;
  font-family: 'Segoe UI', system-ui, sans-serif;
}

.settings-root {
  padding: 20px 16px;
  max-width: 600px;
  margin: 0 auto;
}
```

**Saving settings:**

```javascript
async function save() {
  const body = {
    latitude: parseFloat(document.getElementById('lat').value),
    units: document.getElementById('units').value,
  };

  // ❌ Wrong — cannot write directly to a file
  // localStorage.setItem('config', JSON.stringify(body));

  // ✅ Correct — via the module API
  const r = await fetch(BASE + '/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (r.ok) showMsg('Saved!', 'ok');
  else      showMsg('Error: ' + r.status, 'err');
}
```

---

## 10. Full widget.html template

A ready-made minimal template for copying:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My Widget</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* Required: fill iframe without overflow */
    html, body {
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: #0e1220;
      color: #e0e4f0;
      font-family: 'Segoe UI', system-ui, sans-serif;
    }

    .root {
      display: flex;
      flex-direction: column;
      width: 100%;
      height: 100%;
      padding: 14px 16px;
      gap: 10px;
    }

    /* ── Loading / error states ── */
    .state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 100%;
      gap: 8px;
      color: #3a4060;
      font-size: 0.82rem;
      text-align: center;
    }
    .state .icon { font-size: 1.8rem; opacity: .4; }

    @keyframes pulse { 0%,100%{opacity:.3} 50%{opacity:.9} }
    .pulse { animation: pulse 1.8s ease-in-out infinite; }

    /* ── Compact mode (height < 160px) ── */
    .compact .secondary { display: none; }

    /* ── Your styles ── */

  </style>
</head>
<body>

<div class="root" id="root">
  <div class="state">
    <div class="icon pulse">⏳</div>
    <div>Loading…</div>
  </div>
</div>

<script>
(function () {
  // ── BASE URL — the only correct approach ──────────────────────────────
  const BASE = window.location.pathname
    .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

  // ── ui_token for requests to Core API ─────────────────────────────────
  const uiToken = new URLSearchParams(window.location.search).get('ui_token');

  // ── Adaptation to cell size ───────────────────────────────────────────
  function checkLayout() {
    const root = document.getElementById('root');
    if (!root) return;
    root.classList.toggle('compact', root.offsetHeight < 160);
  }

  // ── Render (your code) ────────────────────────────────────────────────
  function render(data) {
    const root = document.getElementById('root');
    root.innerHTML = `
      <div>Data: ${JSON.stringify(data)}</div>
    `;
    checkLayout();
  }

  // ── Data loading ──────────────────────────────────────────────────────
  async function load() {
    try {
      const data = await fetch(BASE + '/status').then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      });
      render(data);
    } catch (e) {
      document.getElementById('root').innerHTML = `
        <div class="state">
          <div class="icon">⚠️</div>
          <div>${e.message}</div>
        </div>`;
    }
  }

  // ── Initialization ────────────────────────────────────────────────────
  checkLayout();
  load();
  setInterval(load, 30_000);
  window.addEventListener('resize', checkLayout);
})();
</script>
</body>
</html>
```

---

## 11. Full settings.html template

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Settings</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* settings — scrollable modal window, not a fixed cell */
    html, body {
      width: 100%;
      min-height: 100%;     /* ← not 100vh */
      overflow-y: auto;     /* ← scrolling is allowed */
      background: #0e1220;
      color: #e0e4f0;
      font-family: 'Segoe UI', system-ui, sans-serif;
    }

    .settings-root {
      padding: 20px 16px 32px;
      max-width: 560px;
    }

    h1 { font-size: 1.2rem; margin-bottom: 20px; color: #fff; }

    .section {
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.07);
      border-radius: 12px;
      padding: 16px;
      margin-bottom: 14px;
    }

    .section h2 {
      font-size: 0.8rem;
      color: #5a6080;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 12px;
    }

    label {
      display: block;
      font-size: 0.8rem;
      color: #7880a0;
      margin-bottom: 4px;
    }

    input, select {
      width: 100%;
      background: rgba(0,0,0,.4);
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 8px;
      color: #e0e4f0;
      padding: 9px 12px;
      font-size: 0.88rem;
      margin-bottom: 12px;
    }

    input:focus, select:focus {
      outline: none;
      border-color: #4a6ee0;
    }

    .btn {
      display: inline-block;
      background: #3a5cc8;
      color: #fff;
      border: none;
      border-radius: 8px;
      padding: 10px 18px;
      font-size: 0.9rem;
      cursor: pointer;
    }
    .btn:hover  { background: #2e4eb0; }
    .btn-ghost  { background: rgba(255,255,255,.07); }
    .btn-ghost:hover { background: rgba(255,255,255,.12); }
    .btn-full   { width: 100%; text-align: center; }

    .msg {
      margin-top: 10px;
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 0.82rem;
    }
    .msg.ok  { background: rgba(50,200,100,.12); color: #3cc870; }
    .msg.err { background: rgba(220,50,50,.12); color: #e05858; }
  </style>
</head>
<body>
<div class="settings-root">
  <h1>⚙️ Module Settings</h1>

  <div class="section">
    <h2>Configuration</h2>

    <label>Parameter 1</label>
    <input type="text" id="param1" placeholder="Value">

    <label>Parameter 2</label>
    <select id="param2">
      <option value="a">Option A</option>
      <option value="b">Option B</option>
    </select>
  </div>

  <button class="btn btn-full" onclick="save()">💾 Save</button>
  <button class="btn btn-ghost btn-full" style="margin-top:8px" onclick="loadStatus()">🔄 Reload</button>

  <div id="msg"></div>
</div>

<script>
  const BASE = window.location.pathname
    .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');

  // ── Load current settings ─────────────────────────────────────────────
  async function loadStatus() {
    try {
      const s = await fetch(BASE + '/status').then(r => r.json());
      if (s.param1) document.getElementById('param1').value = s.param1;
      if (s.param2) document.getElementById('param2').value = s.param2;
    } catch (e) {
      showMsg('Load failed: ' + e.message, 'err');
    }
  }

  // ── Save ──────────────────────────────────────────────────────────────
  async function save() {
    const body = {
      param1: document.getElementById('param1').value,
      param2: document.getElementById('param2').value,
    };
    try {
      const r = await fetch(BASE + '/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      showMsg('Saved!', 'ok');
    } catch (e) {
      showMsg('Error: ' + e.message, 'err');
    }
  }

  function showMsg(text, type) {
    const el = document.getElementById('msg');
    el.className = 'msg ' + type;
    el.textContent = text;
    setTimeout(() => { el.textContent = ''; el.className = ''; }, 5000);
  }

  loadStatus();
</script>
</body>
</html>
```

---

## 12. Pre-submission checklist

**CSS:**
- [ ] `html, body { width: 100%; height: 100%; overflow: hidden; }` — in widget.html
- [ ] No `100vh`, `min-height: 100vh` in widget.html
- [ ] `.root { width: 100%; height: 100%; display: flex; overflow: hidden; }`
- [ ] No `position: fixed` elements in widget.html
- [ ] settings.html uses `min-height: 100%` and `overflow-y: auto`

**JavaScript:**
- [ ] `BASE` is computed via `window.location.pathname.replace(...)`
- [ ] No hardcoded `localhost:PORT` or IP addresses
- [ ] No `fetch('/endpoint')` without BASE prefix
- [ ] Fetch error handling present (try/catch + error state display)
- [ ] Auto-refresh of data via `setInterval` (or SSE) is present
- [ ] `window.addEventListener('resize', checkLayout)` for adaptation is present

**Functionality:**
- [ ] Widget shows loading state (`Loading...`) while there is no data
- [ ] Widget shows error state if the API is unavailable
- [ ] With empty data (`null`, `undefined`) there is no crash — `'—'` is displayed
- [ ] Compact mode at `height < 160px` hides secondary content

**Size compliance:**
- [ ] Content fits in the cell without scrolling at the base size
- [ ] When the iframe shrinks (compact), nothing is clipped awkwardly

---

## 13. Common mistakes

### ❌ White bar at the bottom / content does not fill the cell

**Cause:** `body` has default margin or `height` is not set.

```css
/* Fix */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 100%; height: 100%; }
```

---

### ❌ Scrollbar appears in the dashboard cell

**Cause:** content exceeds `height: 100%`, and `overflow: hidden` is not set.

```css
/* Fix */
html, body { overflow: hidden; }
.root      { overflow: hidden; }
```

---

### ❌ Forecast / list is clipped at the top or bottom

**Cause:** a flex child cannot shrink below its content size.

```css
/* Fix */
.forecast {
  flex: 1;
  min-height: 0;  /* ← key property, allows the flex item to shrink */
  overflow: hidden;
}
```

---

### ❌ API requests fail with CORS or 404

**Cause:** BASE URL computed incorrectly (hardcoded port or origin without path).

```javascript
// Fix
const BASE = window.location.pathname
  .replace(/\/(widget|settings)(\.html)?(\?.*)?$/, '');
```

---

### ❌ Module shows stale data after changing settings

**Cause:** data is not reloaded after saving in settings.

```javascript
// In settings.html — after a successful save():
async function save() {
  // ... POST /config ...
  showMsg('Saved!', 'ok');
  // Reload status to reflect changes
  await loadStatus();
}
```

---

### ❌ TypeError: Cannot read properties of null / undefined

**Cause:** data arrived, but a field is missing — no null guard.

```javascript
// ❌ Crashes if temperature == null
document.getElementById('temp').textContent = Math.round(data.temperature) + '°';

// ✅ Safe
const t = data.temperature;
document.getElementById('temp').textContent =
  t != null ? Math.round(t) + '°' : '—';
```

---

*SelenaCore · Widget Development Guide · MIT*
