# UI Sync Architecture

> Real-time synchronization of UI state (theme, language, widget layout) across all connected clients: kiosk display, phone browser, PC browser.

---

## Overview

SelenaCore serves the React SPA and all API endpoints from a **single unified process** on port **80**. Real-time state synchronization between clients uses a **WebSocket** connection at `/api/ui/sync` with versioned state, snapshot delivery on connect, and delta event replay on reconnect.

```
Browser/Kiosk/Phone
      |
      | WebSocket /api/ui/sync?v=<last_version>
      |
Core API :80 (unified: API + SPA + WebSocket)
      |
      SyncManager (singleton)
        |- _version: int (monotonic counter)
        |- _settings: {theme, language}
        |- _layout: {pinned, sizes, positions, ...}
        |- _event_log: deque(maxlen=256)
        |- _clients: dict[id, WebSocket]
```

HTTPS on port 443 is handled by a lightweight Python TLS proxy (~5 MB RAM) that forwards to the main process on :80.

---

## Problem (before rework)

| Issue | Root cause |
|-------|-----------|
| Kiosk screen freezes | SSE stream through httpx proxy created zombie connections with no health detection |
| Theme/language not syncing | `asyncio.QueueFull` silently dropped SSE events (maxsize=64) |
| Different state on different clients | No state recovery after EventSource reconnect; missed events lost permanently |
| ~3 GB RAM usage | Two full uvicorn processes (Core :7070 + UI :80), each loading all modules |

### Previous architecture

```
Browser --> UI Server :80 (httpx proxy) --> Core API :7070
            |                                   |
            |- SSE /api/ui/stream (lossy)       |- EventBus
            |- Static files (SPA)               |- Module routers
            |- 60-90 MB RAM wasted              |- All business logic
```

---

## Solution

### Unified server (single process)

```
Browser --> Core API :80 (direct)
            |
            |- API routes (/api/v1/*, /api/ui/*)
            |- Module routers (/api/ui/modules/{name}/*)
            |- WebSocket /api/ui/sync (versioned state)
            |- Static SPA files (/assets/*, /icons/*, /*)
            |- PWA (/manifest.json, /sw.js)

HTTPS :443 --> TLS proxy (asyncio, ~5 MB) --> :80
```

### Resource savings

| Metric | Before | After |
|--------|--------|-------|
| Python processes | 2-3 | 1 + TLS proxy |
| RAM usage | ~3 GB | ~1.5 GB |
| Proxy latency per request | 100-200 ms | 0 ms |
| Zombie connection detection | None | WebSocket ping/pong (5s) |
| State recovery on reconnect | None | Full snapshot or delta replay |

---

## WebSocket Sync Protocol

### Endpoint

```
ws://host/api/ui/sync?v=<last_known_version>
wss://host/api/ui/sync?v=<last_known_version>   (via TLS proxy)
```

### Connection flow

```
1. Client connects with v=0 (first time) or v=N (reconnect)

2. Server sends initial state:

   If v=0 or version too old:
   <- {"type": "hello", "version": 5,
       "settings": {"theme": "dark", "language": "uk"},
       "layout": {"pinned": [...], "sizes": {...}, ...}}

   If v>0 and events available in log:
   <- {"type": "replay", "events": [
        {"version": 3, "event_type": "settings_changed", "payload": {"theme": "dark"}},
        {"version": 4, "event_type": "layout_changed", "payload": {...}}
      ]}

3. Server sends delta events as they occur:
   <- {"type": "event", "version": 6,
       "event_type": "settings_changed",
       "payload": {"language": "en"}}

4. Server sends ping every 5 seconds:
   <- {"type": "ping", "version": 6, "ts": 1712345678.0}

5. Client must respond with pong:
   -> {"type": "pong"}
   (no pong within 15s -> server closes connection)

6. On disconnect, client reconnects with v=<lastVersion>
   and receives only missed events (or full snapshot if too old)
```

### Event types

| Event | Trigger | Payload |
|-------|---------|---------|
| `settings_changed` | `POST /api/ui/settings` | `{theme?, language?}` |
| `layout_changed` | `POST /api/ui/layout` | Full widget layout object |
| `module.started` | Module lifecycle | `{name}` |
| `module.stopped` | Module lifecycle | `{name}` |
| `module.removed` | Module lifecycle | `{name}` |

---

## Key Components

### SyncManager (`core/api/sync_manager.py`)

Singleton that holds the authoritative UI state:

- **Versioned state**: monotonic counter incremented on every change
- **Event log**: `deque(maxlen=256)` — stores recent events for replay (~50-100 KB)
- **Client registry**: tracks connected WebSocket clients with last pong timestamp
- **Dual broadcast**: pushes events to both WebSocket clients and legacy SSE clients

```python
from core.api.sync_manager import get_sync_manager

manager = get_sync_manager()
manager.get_snapshot()                    # Full state for new clients
manager.get_events_since(version=5)       # Delta replay
await manager.update_settings({"theme": "dark"})  # Publish change
await manager.update_layout(layout_dict)           # Publish layout
```

### Settings endpoint

```http
GET /api/ui/settings
-> {"theme": "auto", "language": "uk"}

POST /api/ui/settings
Content-Type: application/json
{"theme": "dark"}
-> {"ok": true}
(broadcasts settings_changed to all clients via WebSocket + SSE)
```

### Kiosk watchdog (`src/hooks/useConnectionHealth.ts`)

React hook that monitors WebSocket health:
- Checks `lastServerContact` timestamp every 10 seconds
- If no contact for 60 seconds, forces `window.location.reload()`
- Ensures the kiosk display never shows stale state

### TLS proxy

Lightweight Python asyncio script (embedded in `scripts/start.sh`):
- Listens on :443 with SSL context
- Forwards TCP connections to :80
- ~5 MB RAM (vs ~1.5 GB for a second uvicorn)

---

## SPA Static File Serving

The React SPA is served directly by Core API (`core/main.py`):

- `/assets/*` — Vite-built JS/CSS bundles (NoCacheStaticFiles)
- `/icons/*` — PWA icons (NoCacheStaticFiles)
- `/manifest.json` — PWA Web App Manifest
- `/sw.js` — Service Worker
- `/{any_path}` — SPA catch-all returns `index.html`

The SPA catch-all is registered **after** all module routers (during lifespan startup) to avoid intercepting `/api/ui/modules/{name}/*` routes.

Source: `system_modules/ui_core/static/` (built by `npx vite build`)

---

## Deprecated Components

| File | Status | Replacement |
|------|--------|-------------|
| `system_modules/ui_core/server.py` | Gutted | Core API serves SPA directly |
| `system_modules/ui_core/routes/dashboard.py` | Gutted | Routes in `core/api/routes/ui.py` |
| `system_modules/ui_core/wizard.py` | Gutted | Routes in `core/api/routes/ui.py` |
| SSE `/api/ui/stream` | Kept (backward compat) | WebSocket `/api/ui/sync` |

---

## Migration Notes

### Port change: 7070 -> 80

All references to port 7070 have been updated:
- `core/config.py`: `core_port` default = 80
- `scripts/start.sh`: single uvicorn on :80
- `Dockerfile.core`: `EXPOSE 80 443`
- `docker-compose.yml`: healthcheck on `http://localhost/api/v1/health`
- `smarthome-core.service`: `--port 80`
- All system module `main.py` files: `localhost:7070` -> `localhost`

### After `docker compose up -d --build`

The `start.sh` file is **COPY'd** into the Docker image (not volume-mounted). After editing `scripts/start.sh`, either:
- Rebuild: `docker compose up -d --build`
- Or manually: `docker cp scripts/start.sh selena-core:/opt/selena-core/start.sh && docker restart selena-core`

### Clearing bytecode cache

After port changes in Python files, clear `__pycache__` to avoid stale `.pyc`:
```bash
docker exec selena-core find /opt/selena-core -name "__pycache__" -type d -exec rm -rf {} +
docker restart selena-core
```

### Persistent data

Check `/var/lib/selena/*.json` for hardcoded port references:
```bash
docker exec selena-core grep -r "7070" /var/lib/selena/ --include="*.json"
```
