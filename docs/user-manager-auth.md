# User Manager — Authentication & Authorization

🇺🇦 [Українська версія](uk/user-manager-auth.md)

**Module:** `system_modules/user_manager/`  
**Routes:** `/api/ui/modules/user-manager/`  
**Type:** SYSTEM (in-process, no port)

---

## Overview

User Manager handles all identity and access control for SelenaCore. It provides:

- **Device token** authentication (long-lived, stored in HttpOnly cookie + header)
- **Elevated sessions** for sensitive operations (PIN or QR approval, lasts 10 min)
- **Full user CRUD** with roles: `owner` → `admin` → `user` → `guest`
- **QR-based device registration** (new browser) and **QR-based kiosk unlock** (elevate)
- **PIN confirmation** for quick elevation without QR

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
      ├─ token valid + not expired (< 10 min) → proceed
      └─ missing / expired → 403 Forbidden
```

### Token types

| Token | Storage | Expiry | Purpose |
|-------|---------|--------|---------|
| `device_token` | HttpOnly cookie + `localStorage('selena_device')` | 30 days | Identifies a registered browser/phone |
| `elevated_token` | `sessionStorage('selena_elevated')` only | 10 min | Unlocks sensitive operations |
| `qr_session` | In-memory dict in user-manager | 5 min (`_QR_TTL = 300`) | One-time QR handshake |

---

## Roles

| Role | Level | Can do |
|------|-------|--------|
| `owner` | 4 | Everything, including deleting admins |
| `admin` | 3 | Manage users (not owner), view audit log |
| `user` | 2 | Own profile, change own PIN |
| `guest` | 1 | Read-only, no sensitive operations |

Role-based permission config is stored per-role in `permissions` table and can be edited via `PATCH /roles/{role}` (owner only, requires elevation).

---

## Registration Flow (new browser/phone)

A new device registers by providing username + PIN.

### Option A — Direct registration (AuthWall)

```
Browser                       user-manager
   │                               │
   │── POST /auth/register ────────►│
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
   │    (10 min session)            │
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

## API Reference

Base path: `/api/ui/modules/user-manager`

### Auth — Device registration

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/register` | — | Register new device (username + PIN) |
| `POST` | `/auth/pin/confirm` | device token | Verify PIN → get elevated_token |
| `POST` | `/auth/device/verify` | device token in header | Check token validity, returns user info |
| `POST` | `/auth/device/revoke` | device token | Revoke own device token |
| `GET`  | `/auth/devices` | device token | List all registered devices for own user |

### Auth — QR flow

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/auth/qr/start` | — | Create QR session (`mode`: `access` or `elevate`) |
| `GET`  | `/auth/qr/status/{id}` | — | Poll session status |
| `GET`  | `/auth/qr/info/{id}` | — | Get mode + `expires_in_seconds` (used by join page) |
| `GET`  | `/auth/qr/join/{id}` | — | Phone approval page (HTML) |
| `POST` | `/auth/qr/approve/{id}` | device token | Phone approves kiosk unlock (elevate mode) |
| `POST` | `/auth/qr/complete/{id}` | — | Phone submits username+PIN (access mode) |

#### `POST /auth/qr/start` — Request

```json
{ "mode": "access" }   // new browser registration
{ "mode": "elevate" }  // kiosk unlock
```

#### `POST /auth/qr/start` — Response `201`

```json
{
  "session_id": "uuid-...",
  "join_url":   "http://192.168.1.45/api/ui/modules/user-manager/auth/qr/join/uuid-...",
  "qr_image":   "data:image/png;base64,...",
  "expires_in": 300
}
```

`join_url` always contains the **LAN IP** when the request originates from localhost/127.0.0.1.

#### `GET /auth/qr/info/{id}` — Response `200`

```json
{
  "mode": "elevate",
  "status": "pending",
  "expires_in_seconds": 247
}
```

Returns `410 Gone` if the session has expired.

#### `GET /auth/qr/status/{id}` — Response `200`

```json
// pending:
{ "status": "pending" }

// complete (access mode):
{ "status": "complete", "device_token": "...", "user_id": "uuid-..." }

// complete (elevate mode):
{ "status": "complete", "elevated_token": "...", "user_id": "uuid-..." }
```

Returns `404` if session not found, `410` if expired.

### Users CRUD

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET`    | `/users` | device token, role ≥ admin | List all users |
| `POST`   | `/users` | device token + elevated, role ≥ admin | Create user |
| `GET`    | `/users/{id}` | device token | Get user by ID |
| `PATCH`  | `/users/{id}` | device token + elevated, role ≥ admin | Update user |
| `DELETE` | `/users/{id}` | device token + elevated, role = owner | Deactivate user |
| `POST`   | `/users/{id}/pin` | device token + elevated | Change PIN |

### Devices

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET`    | `/devices` | device token | List own devices (admins see all) |
| `DELETE` | `/devices/{id}` | device token | Revoke device |

### Roles & Permissions

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET`   | `/roles` | device token | List role permissions |
| `PATCH` | `/roles/{role}` | device token + elevated, role = owner | Update role permissions |

### System

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/me` | — | Own user info or guest context |
| `GET` | `/health` | — | Module health check |

---

## KioskElevationGate

React component (`src/components/KioskElevationGate.tsx`) that wraps restricted UI sections.

**Applies to all browser sessions** — there are no "trusted" browsers. Registered devices are only used for push notifications and QR auth, not to bypass elevation.

**Restricted routes**: `/modules`, `/system`, `/integrity`, `/settings`
**Always accessible**: `/` (dashboard)

**Behaviour:**
1. User navigates to a restricted section
2. Gate overlay covers the full screen
3. User unlocks via PIN tab (username + PIN) or QR tab
4. Elevated token stored in `sessionStorage` + Zustand store
5. On success: overlay slides away, restricted section renders
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

- `device_token` is generated via `secrets.token_urlsafe(48)` — 64 characters of base64url
- Only the SHA-256 hash of the token is stored in the database
- `elevated_token` is a separate short-lived token, never reused
- QR sessions live only in-memory; a restart clears all pending sessions
- `qr_join.html` reads the phone's token from `localStorage` / cookie — the token is **never** sent via URL
- PATCH/DELETE on users requires both a valid device_token **and** a valid elevated_token
- PIN brute-force protection: rate-limiting middleware (5 attempts → 10 min lock) is handled at the API middleware layer

---

## File Map

```
system_modules/user_manager/
  module.py          — FastAPI routes, QR session logic, LAN IP detection
  profiles.py        — UserManager: CRUD, PIN hashing, role checks
  devices.py         — DeviceManager: token creation, verification, revoke
  elevated.py        — ElevatedManager: short-lived elevated tokens
  permissions.py     — PermissionsManager: per-role config
  qr_join.html       — Phone approval page (standalone HTML, EN/UK)
  manifest.json      — SYSTEM type, no port field

src/components/
  AuthWall.tsx           — New browser registration overlay (PIN + QR tabs)
  KioskElevationGate.tsx — Kiosk lock overlay (PIN + QR tabs, countdown)

src/i18n/locales/
  en.ts  — auth.* and kiosk.* keys
  uk.ts  — auth.* and kiosk.* keys (Ukrainian)
```
