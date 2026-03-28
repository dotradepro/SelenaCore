# User Manager — Authentication & Authorization

🇺🇦 [Українська версія](uk/user-manager-auth.md)

**Module:** `system_modules/user_manager/`
**Routes:** `/api/ui/modules/user-manager/`
**Type:** SYSTEM (in-process, no port)

---

## Overview

User Manager handles all identity and access control for SelenaCore. It provides:

- **Device token** authentication (long-lived, stored in HttpOnly cookie + header)
- **Elevated sessions** for sensitive operations (PIN or QR approval, lasts 5 min with sliding window)
- **User CRUD** — flat model: first user = `admin`, all others = `resident` (house members)
- **QR-based device registration** (new browser) and **QR-based kiosk unlock** (elevate)
- **PIN confirmation** for quick elevation without QR

**No role-based permissions.** The PIN/QR elevation gate is the only security boundary.
Any elevated user can manage settings, users, and devices.

All tokens are random, stored hashed in SQLite at `/var/lib/selena/selena.db`.
The database path is resolved as an **absolute path** via `sqlite+aiosqlite:////var/lib/selena/selena.db`
(4 leading slashes = absolute; 3 slashes would be relative to the container's CWD).

---

## Authentication Model

```
Browser / Phone
      │
      │  X-Device-Token: <device_token>    (header)
      │  Cookie: selena_device=<device_token>  (HttpOnly)
      │
      ▼
 user_manager → DeviceManager.verify(token)
      │
      ├─ token valid  → returns {user_id, role, display_name, ...}
      └─ token invalid → 401 Unauthorized


Elevated operation (PATCH /users, DELETE /users, PIN change, etc.)
      │
      │  X-Elevated-Token: <elevated_token>   (header)
      │
      ▼
 _require_elevated() checks:
      ├─ token valid + not expired (< 5 min, sliding window) → proceed
      └─ missing / expired → 403 Forbidden
```

### Token types

| Token | Storage | Expiry | Purpose |
|-------|---------|--------|---------|
| `device_token` | HttpOnly cookie + `localStorage('selena_device')` | 30 days | Identifies a registered browser/phone |
| `elevated_token` | `sessionStorage('selena_elevated')` only | 5 min (sliding) | Unlocks sensitive operations |
| `qr_session` | In-memory dict in user-manager | 5 min (`_QR_TTL = 300`) | One-time QR handshake |

---

## User Model

| Field | Description |
|-------|-------------|
| `admin` | First user created during wizard setup. Has PIN. |
| `resident` | All subsequent users (house members). Created with name + optional device link. |

There are no role-based permission checks. The `role` field is stored in the database
(`admin` or `resident`) but carries no authorization weight. The elevation gate (PIN/QR)
is the sole access control mechanism for all sections beyond the Dashboard.

---

## Registration Flow (new browser/phone)

A new device registers by providing username + PIN.

### Option A — Direct registration (AuthWall)

```
Browser                       user-manager
   │                               │
   │── POST /auth/device/register ─►│
   │   {username, pin, device_name} │
   │                               │── verify user credentials
   │                               │── create device record
   │◄── 201 {device_token} ─────────│
   │    Set-Cookie: selena_device   │
```

### Option B — QR registration (mobile scan)

```
Desktop browser               user-manager               Phone
      │                            │                        │
      │── POST /auth/qr/start ────►│                        │
      │   {mode:"access"}          │── create session ──────│
      │◄── {session_id,            │   expires in 5 min     │
      │     qr_image,              │                        │
      │     join_url,              │   QR encodes join_url  │
      │     expires_in:300}        │   with LAN IP*         │
      │                            │                        │
      │  [shows QR on screen]      │    [Phone scans QR]    │
      │                            │                        │
      │                            │◄── GET /auth/qr/join/{id}  ─── Phone browser
      │                            │    → qr_join.html           (served to phone)
      │                            │
      │                            │◄── POST /auth/qr/complete/{id}
      │                            │    {username, pin, device_name}
      │                            │── verify + create device_token for phone
      │                            │── mark session "complete"
      │                            │
      │── GET /auth/qr/status/{id}►│  (polled every 2s by desktop)
      │◄── {status:"complete",     │
      │     device_token:"..."}    │
```

`*` **LAN IP detection:** when `qr_start` is called from localhost/127.0.0.1 (kiosk), the server detects the host's LAN IP via a UDP socket probe and embeds it in the `join_url`. This ensures the QR encodes a reachable address like `http://192.168.1.x/...` instead of `http://localhost/...`.

---

## Kiosk Elevation Flow (QR unlock)

The kiosk (device screen) shows `KioskElevationGate` over restricted sections. Two ways to unlock:

### PIN elevation

```
Kiosk                         user-manager
   │                               │
   │── POST /auth/pin/confirm ─────►│
   │   X-Device-Token: <token>      │── verify device token
   │   {pin}                        │── check PIN hash
   │◄── 200 {elevated_token} ───────│
   │    (5 min session, sliding)    │
```

### QR elevation

```
Kiosk                         user-manager               Phone
   │                               │                        │
   │── POST /auth/qr/start ────────►│                        │
   │   {mode:"elevate"}            │── create session ──────│
   │◄── {session_id,               │   mode="elevate"       │
   │     qr_image (LAN IP),        │   expires 5 min        │
   │     expires_in:300}           │                        │
   │                               │   QR shown on kiosk    │
   │  [kiosk shows QR + countdown] │    [Phone scans]       │
   │                               │                        │
   │                               │◄── GET /auth/qr/join/{id}  ─── Phone
   │                               │    → qr_join.html          fetches /qr/info
   │                               │      shows "Approve access?"
   │                               │◄── POST /auth/qr/approve/{id}
   │                               │    X-Device-Token: <phone_token>
   │                               │── verify phone token
   │                               │── issue elevated_token
   │                               │── mark session "complete"
   │                               │
   │── GET /auth/qr/status/{id} ──►│  (polled every 2s by kiosk)
   │◄── {status:"complete",        │
   │     elevated_token:"..."}     │
   │                               │
   │  [gate unlocks, section opens]│  [Phone shows: "Done. You may close this tab."]
```

### QR elevation via presence (no device token required)

```
Kiosk                         user-manager               Phone (tracked)
   │                               │                        │
   │── POST /auth/qr/start ────────►│                        │
   │   {mode:"elevate"}            │── create session ──────│
   │◄── {session_id, qr_image}     │                        │
   │                               │   QR shown on kiosk    │
   │  [kiosk shows QR + countdown] │    [Phone scans]       │
   │                               │                        │
   │                               │◄── POST /auth/qr/approve-by-presence/{id}
   │                               │    (no token needed — server resolves
   │                               │     IP → ARP MAC → presence DB → linked account)
   │                               │── verify → elevated_token
   │                               │── mark session "complete"
   │                               │
   │── GET /auth/qr/status/{id} ──►│  (polled every 2s by kiosk)
   │◄── {status:"complete",        │
   │     elevated_token:"..."}     │
```

This flow works when the phone is a tracked presence device (its MAC is in the
presence-detection database with a `linked_account_id`). No device token
or PIN is needed — identity is resolved from the network layer.

---

## QR Join Page (`/auth/qr/join/{session_id}`)

A standalone HTML page served directly from `system_modules/user_manager/qr_join.html`.
Designed for mobile browsers. Features:

| Feature | Detail |
|---------|--------|
| Session check | Fetches `GET /auth/qr/info/{id}` first — detects mode + remaining seconds |
| Countdown timer | MM:SS ticker, turns red when < 60 s, shows "Code expired" at 0 |
| Already expired | 410 response → shows "QR code expired" immediately, no form shown |
| `elevate` mode | Reads phone's `selena_device` token → "Approve access?" → checkmark on success |
| `access` mode | Username + PIN form → registers phone device → saves token to `localStorage` |
| Localisation | EN / UK via `localStorage('selena-lang')`, full inline dictionaries |
| BASE URL | Computed from `window.location.pathname` — no hardcoded hosts or ports |

---

## Countdown Timer

QR sessions are valid for **5 minutes** (`_QR_TTL = 300`).

- **Phone page:** countdown displayed in the page header (MM:SS), turns red at 60 s
- **Kiosk QrPane:** countdown shown below the QR image (MM:SS, tabular-nums), turns red at 60 s
- **On expiry:** server returns HTTP 410; both sides show "QR expired" + "Generate new code" button
- **TTL is configurable** by changing `_QR_TTL` in `system_modules/user_manager/module.py`

---

## UI Navigation

The main interface has no sidebar. Navigation uses:

- **Logo** (top-left of TopBar) — click to return to Dashboard
- **Gear icon** (top-right of TopBar, next to clock) — opens Settings (requires PIN/QR elevation)
- **Dashboard** (`/`) — always accessible, no authentication required
- **Settings** (`/settings/*`) — protected by `KioskElevationGate`, contains all admin sections:
  - Appearance, Voice & LLM, Audio, Network, Users, Modules, System, System Info, Integrity, Security, System Modules

---

## API Reference

Base path: `/api/ui/modules/user-manager`

### Auth — Device registration

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/setup` | — | First-time admin account creation (empty DB only) |
| `POST` | `/auth/device/register` | — | Register new device (username + PIN) |
| `POST` | `/auth/pin/confirm` | device token | Verify PIN → get elevated_token |
| `POST` | `/auth/device/verify` | device token in header | Check token validity, returns user info |
| `DELETE` | `/auth/device` | device token | Revoke own device token |

### Auth — QR flow

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/qr/start` | — | Create QR session (`mode`: `access`, `elevate`, `invite`, `wizard_setup`) |
| `GET`  | `/auth/qr/status/{id}` | — | Poll session status |
| `GET`  | `/auth/qr/info/{id}` | — | Get mode + `expires_in_seconds` (used by join page) |
| `GET`  | `/auth/qr/join/{id}` | — | Phone approval page (HTML) |
| `POST` | `/auth/qr/approve/{id}` | device token | Phone approves kiosk unlock (elevate mode) |
| `POST` | `/auth/qr/approve-by-presence/{id}` | — | Approve QR via presence detection (IP → MAC → account) |
| `POST` | `/auth/qr/complete/{id}` | — | Phone submits username+PIN (access/invite mode) |
| `POST` | `/auth/qr/wizard-link/{id}` | — | Phone links during wizard setup + auto-adds presence tracking |

### Auth — Presence-based phone identification

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/phone/identify` | — | Identify phone by IP → MAC → presence user → linked account |

### Auth — Elevated session

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/elevated/refresh` | elevated token | Reset TTL (sliding window keep-alive) |
| `POST` | `/auth/elevated/revoke` | elevated token | Immediately invalidate session |

### Auth — Browser sessions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/session/heartbeat` | session token | Reset idle timer for QR temp session |
| `POST` | `/auth/session/logout` | session token | End temporary browser session |

### Users CRUD

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET`    | `/users` | device token | List all users |
| `POST`   | `/users` | device token + elevated | Create user (name + PIN) |
| `GET`    | `/users/{id}` | device token | Get user by ID |
| `PATCH`  | `/users/{id}` | device token + elevated | Update user (display_name) |
| `DELETE` | `/users/{id}` | device token + elevated | Deactivate user |
| `POST`   | `/users/{id}/pin` | device token + elevated | Change PIN |

### Devices

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET`    | `/users/{id}/devices` | device token | List user's registered devices |
| `DELETE` | `/devices/{id}` | device token | Revoke device |
| `PATCH`  | `/devices/{id}` | device token | Rename device |

### System

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/me` | — | Own user info or guest context |
| `GET` | `/auth/status` | — | Check if first-time setup is needed |

---

## KioskElevationGate

React component (`src/components/KioskElevationGate.tsx`) that wraps restricted UI sections.

**Applies to all browser sessions** — there are no "trusted" browsers. Registered devices are only used for push notifications and QR auth, not to bypass elevation.

**Restricted routes**: `/settings/*` (includes Modules, System Info, Integrity as tabs)
**Always accessible**: `/` (dashboard)

**Behaviour:**
1. User clicks gear icon in TopBar
2. Gate overlay covers the full screen
3. User unlocks via PIN tab (username + PIN) or QR tab
4. Elevated token stored in `sessionStorage` + Zustand store
5. On success: overlay slides away, Settings section renders
6. Inactivity 5 min → session auto-revoked → gate re-appears

**PIN auth paths:**
- **With device token** (`X-Device-Token` header): token identifies user, only PIN required
- **Without device token** (any browser): username + PIN required — works from any IP

**Inactivity lock (5 min):**
- Frontend: `setTimeout` debounce — resets on `click`, `keypress`, `mousemove`, `scroll`
- Frontend: pings `POST /auth/elevated/refresh` every 1 min if there was activity
- Backend: rolling window — `verify()` extends TTL on every valid elevated API call
- On timeout: `POST /auth/elevated/revoke` + reset to guest + redirect to `/`
- Global 401 intercept: any elevated API call returning 401 clears the session immediately

**QR tab extras:** countdown timer (MM:SS) shown below the QR image; turns red at 60 s remaining; expired state shows "Generate new code" button.

---

## Security Notes

- `device_token` is generated via `uuid.uuid4()` — stored as SHA-256 hash only
- Only the SHA-256 hash of the token is stored in the database
- `elevated_token` is a separate short-lived token, never reused
- QR sessions live only in-memory; a restart clears all pending sessions
- `qr_join.html` reads the phone's token from `localStorage` / cookie — the token is **never** sent via URL
- PATCH/DELETE on users requires both a valid device_token **and** a valid elevated_token
- PIN brute-force protection: 5 failed attempts → 10 min lockout (per-user, in-memory)

---

## File Map

```
system_modules/user_manager/
  module.py          — FastAPI routes, QR session logic, LAN IP detection
  profiles.py        — UserManager: CRUD, PIN hashing (admin/resident model)
  devices.py         — DeviceManager: token creation, verification, revoke
  elevated.py        — ElevatedManager: short-lived elevated tokens (5 min TTL)
  pin_auth.py        — PIN rate limiting (5 attempts → 10 min lock)
  sessions.py        — BrowserSessionManager: temporary QR-based browser sessions
  face_auth.py       — Face recognition enrollment/verification
  audit_log.py       — Audit trail logging
  qr_join.html       — Phone approval page (standalone HTML, EN/UK)
  manifest.json      — SYSTEM type, no port field

src/components/
  KioskElevationGate.tsx — Kiosk lock overlay (PIN + QR tabs, countdown)
  Layout.tsx             — TopBar with logo (→ home), gear icon (→ settings)
  Settings.tsx           — All admin tabs (Appearance, Users, Modules, System, etc.)
  UsersPanel.tsx         — User management (create residents, link devices)

src/hooks/
  useElevated.ts         — Elevated token lifecycle management
  useKioskInactivity.ts  — 5-min inactivity auto-lock
  useSessionKeepAlive.ts — QR session heartbeat
```
