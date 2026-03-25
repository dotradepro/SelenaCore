"""
system_modules/user_manager/module.py — User Manager SystemModule.

Runs in-process inside smarthome-core.  Provides:

  • Device-token authentication (HttpOnly cookie + X-Device-Token header)
  • Short-lived elevated sessions for sensitive operations
  • Full user CRUD with role-based access control
  • Per-role permission configuration (owner only)
  • QR-based device registration flow

All API routes are mounted at /api/ui/modules/user-manager/
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import create_async_engine

from core.module_loader.system_module import SystemModule
from system_modules.user_manager.devices import DeviceManager
from system_modules.user_manager.elevated import ElevatedManager
from system_modules.user_manager.permissions import PermissionsManager, RolePermissions
from system_modules.user_manager.profiles import (
    InvalidPinError,
    UserAlreadyExistsError,
    UserManager,
    UserNotFoundError,
    VALID_ROLES,
    _hash_pin,
)

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("SELENA_DB_URL", "sqlite+aiosqlite:///var/lib/selena/selena.db")

_DEVICE_COOKIE = "selena_device"
_ELEVATED_HEADER = "X-Elevated-Token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30   # 30 days
_QR_TTL = 300                           # 5 minutes

# Role privilege hierarchy — higher = more privilege
_ROLE_HIERARCHY: dict[str, int] = {
    "owner": 4,
    "admin": 3,
    "user": 2,
    "guest": 1,
}


# ── Pydantic models ────────────────────────────────────────────────────────────

class RegisterDeviceRequest(BaseModel):
    username: str
    pin: str
    device_name: str = "My Device"


class PinConfirmRequest(BaseModel):
    pin: str


class CreateUserRequest(BaseModel):
    username: str
    display_name: str
    pin: str
    role: str = "user"


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None
    active: bool | None = None


class ChangePinRequest(BaseModel):
    current_pin: str
    new_pin: str


class PermissionsUpdateRequest(BaseModel):
    devices_view: bool | None = None
    devices_control: bool | None = None
    scenes_run: str | None = None
    modules_configure: bool | None = None
    users_manage: bool | None = None
    roles_configure: bool | None = None
    system_reboot: bool | None = None
    system_update: bool | None = None
    integrity_logs_view: bool | None = None
    voice_commands: str | None = None
    allowed_device_types: list[str] | None = None
    allowed_widget_ids: list[str] | None = None


# ── Module class ───────────────────────────────────────────────────────────────

class UserManagerModule(SystemModule):
    name = "user-manager"

    def __init__(self) -> None:
        super().__init__()
        engine = create_async_engine(DB_URL, echo=False)
        self._users = UserManager(DB_URL)
        self._devices = DeviceManager(engine)
        self._permissions = PermissionsManager(engine)
        self._elevated = ElevatedManager()
        # QR pending sessions: session_id → {expires_at, status, device_token?, user_id?}
        self._qr_sessions: dict[str, dict[str, Any]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._users._get_engine()          # triggers ensure_tables via lazy init
        await self._devices.ensure_tables()
        await self._permissions.ensure_tables()
        self._elevated.start_cleanup()
        await self.publish("module.started", {"name": self.name})
        logger.info("UserManager module started")

    async def stop(self) -> None:
        await self._elevated.stop_cleanup()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})
        logger.info("UserManager module stopped")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _set_device_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            _DEVICE_COOKIE,
            token,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="strict",
            secure=False,   # set True when running behind HTTPS
        )

    def _get_raw_token(self, request: Request) -> str | None:
        """Extract device token from cookie or X-Device-Token header."""
        return request.cookies.get(_DEVICE_COOKIE) or request.headers.get("X-Device-Token")

    async def _require_device_auth(self, request: Request) -> dict[str, Any]:
        """Return verified user info dict or raise 401."""
        raw = self._get_raw_token(request)
        if not raw:
            raise HTTPException(status_code=401, detail="Device not registered")
        info = await self._devices.verify(raw)
        if not info:
            raise HTTPException(status_code=401, detail="Invalid or revoked device token")
        return info

    def _require_elevated(self, request: Request, user_info: dict[str, Any]) -> None:
        """Raise 403 if no valid elevated token is present for the current user."""
        elevated_token = request.headers.get(_ELEVATED_HEADER)
        if not elevated_token:
            raise HTTPException(status_code=403, detail="Elevated session required")
        if not self._elevated.verify(elevated_token, user_info["user_id"]):
            raise HTTPException(status_code=403, detail="Elevated session expired or invalid")

    def _require_role(self, user_info: dict[str, Any], min_role: str) -> None:
        """Raise 403 if the user's role is insufficient for *min_role*."""
        user_level = _ROLE_HIERARCHY.get(user_info.get("role", "guest"), 0)
        required = _ROLE_HIERARCHY.get(min_role, 99)
        if user_level < required:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

    def _cleanup_qr_sessions(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._qr_sessions.items() if s["expires_at"] < now]
        for sid in expired:
            self._qr_sessions.pop(sid, None)

    # ── Router ────────────────────────────────────────────────────────────────

    def get_router(self) -> APIRouter:  # noqa: C901
        router = APIRouter()
        mod = self

        # ── Auth: device registration ──────────────────────────────────────────

        @router.post("/auth/device/register", status_code=201)
        async def register_device(
            req: RegisterDeviceRequest,
            response: Response,
            request: Request,
        ) -> dict:
            """Register this browser/phone as a trusted device.

            Verifies the username + PIN combination.  On success sets the
            ``selena_device`` HttpOnly cookie and returns the plain token in
            the response body (for PWA localStorage fallback).
            """
            profile = await mod._users.get_by_username(req.username)
            if not profile or not profile.active:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            if profile.pin_hash != _hash_pin(req.pin):
                raise HTTPException(status_code=401, detail="Invalid credentials")

            ip = request.client.host if request.client else ""
            ua = request.headers.get("user-agent", "")
            plain_token = await mod._devices.register(
                user_id=profile.user_id,
                device_name=req.device_name,
                user_agent=ua,
                ip=ip,
            )
            mod._set_device_cookie(response, plain_token)
            logger.info(
                "Device registered: user=%s name=%s ip=%s", req.username, req.device_name, ip
            )
            return {
                "device_token": plain_token,
                "user_id": profile.user_id,
                "role": profile.role,
                "display_name": profile.display_name,
            }

        @router.post("/auth/device/verify")
        async def verify_device(request: Request) -> dict:
            """Verify the device token from cookie or header. Returns user info."""
            info = await mod._require_device_auth(request)
            return {"authenticated": True, **info}

        @router.delete("/auth/device")
        async def revoke_device(request: Request) -> dict:
            """Revoke the current device token (logout this device)."""
            info = await mod._require_device_auth(request)
            await mod._devices.revoke(info["device_id"])
            return {"revoked": True, "device_id": info["device_id"]}

        # ── Auth: elevated session ─────────────────────────────────────────────

        @router.post("/auth/pin/confirm")
        async def confirm_pin(req: PinConfirmRequest, request: Request) -> dict:
            """Validate PIN and issue a 10-minute elevated session token.

            The returned ``elevated_token`` must be sent in the
            ``X-Elevated-Token`` header for operations requiring elevation.
            """
            info = await mod._require_device_auth(request)
            if not await mod._users.verify_pin(info["user_id"], req.pin):
                raise HTTPException(status_code=401, detail="Incorrect PIN")
            elevated_token = mod._elevated.grant(info["user_id"])
            return {
                "elevated_token": elevated_token,
                "expires_in": 600,
                "user_id": info["user_id"],
                "role": info["role"],
            }

        # ── Auth: QR registration flow ─────────────────────────────────────────

        @router.post("/auth/qr/start", status_code=201)
        async def qr_start(request: Request) -> dict:
            """Generate a one-time QR session for device registration.

            The hub screen shows this QR code.  The user scans it with their
            phone, opens the URL, enters their username + PIN → their device
            gets registered.

            Poll ``GET /auth/qr/status/{session_id}`` to detect completion.
            """
            session_id = str(uuid.uuid4())
            expires_at = time.time() + _QR_TTL
            mod._qr_sessions[session_id] = {
                "status": "pending",
                "expires_at": expires_at,
                "device_token": None,
                "user_id": None,
            }
            mod._cleanup_qr_sessions()

            base = str(request.base_url).rstrip("/")
            join_url = f"{base}/api/ui/modules/user-manager/auth/qr/join/{session_id}"

            qr_image: str | None = None
            try:
                import base64
                import io

                import qrcode  # type: ignore[import]

                qr = qrcode.make(join_url)
                buf = io.BytesIO()
                qr.save(buf, format="PNG")
                qr_image = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            except ImportError:
                logger.debug("qrcode package not available — QR image skipped")

            return {
                "session_id": session_id,
                "join_url": join_url,
                "qr_image": qr_image,
                "expires_in": _QR_TTL,
            }

        @router.get("/auth/qr/status/{session_id}")
        async def qr_status(session_id: str) -> dict:
            """Poll QR session status.

            Returns ``{status: "complete", device_token: "...", user_id: "..."}``
            when the phone has successfully registered.  The session is then
            deleted from the server.
            """
            session = mod._qr_sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="QR session not found or expired")
            if time.time() > session["expires_at"]:
                mod._qr_sessions.pop(session_id, None)
                raise HTTPException(status_code=410, detail="QR session expired")

            result: dict[str, Any] = {"status": session["status"]}
            if session["status"] == "complete" and session.get("device_token"):
                result["device_token"] = session["device_token"]
                result["user_id"] = session["user_id"]
                mod._qr_sessions.pop(session_id, None)
            return result

        @router.post("/auth/qr/complete/{session_id}", status_code=201)
        async def qr_complete(
            session_id: str,
            req: RegisterDeviceRequest,
            response: Response,
            request: Request,
        ) -> dict:
            """Complete QR-based registration by verifying the user's PIN.

            Called by the mobile browser after the user scans the QR code.
            Issues a device_token and sets the cookie for the mobile browser.
            """
            session = mod._qr_sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="QR session not found")
            if time.time() > session["expires_at"]:
                mod._qr_sessions.pop(session_id, None)
                raise HTTPException(status_code=410, detail="QR session expired")
            if session["status"] != "pending":
                raise HTTPException(status_code=409, detail="QR session already completed")

            profile = await mod._users.get_by_username(req.username)
            if not profile or not profile.active:
                raise HTTPException(status_code=401, detail="Invalid credentials")
            if profile.pin_hash != _hash_pin(req.pin):
                raise HTTPException(status_code=401, detail="Invalid credentials")

            ip = request.client.host if request.client else ""
            ua = request.headers.get("user-agent", "")
            plain_token = await mod._devices.register(
                user_id=profile.user_id,
                device_name=req.device_name,
                user_agent=ua,
                ip=ip,
            )
            session["status"] = "complete"
            session["device_token"] = plain_token
            session["user_id"] = profile.user_id

            mod._set_device_cookie(response, plain_token)
            return {
                "device_token": plain_token,
                "user_id": profile.user_id,
                "role": profile.role,
                "display_name": profile.display_name,
            }

        # ── QR join page (simple HTML form for mobile) ─────────────────────────

        @router.get("/auth/qr/join/{session_id}", response_class=HTMLResponse)
        async def qr_join_page(session_id: str) -> HTMLResponse:
            """Minimal HTML page opened by the phone after scanning the QR code."""
            html_path = Path(__file__).parent / "qr_join.html"
            if html_path.exists():
                content = html_path.read_text()
            else:
                content = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Register device — SelenaCore</title>
<style>body{{font-family:sans-serif;max-width:400px;margin:2rem auto;padding:1rem}}
input{{width:100%;padding:.5rem;margin:.5rem 0;box-sizing:border-box}}
button{{width:100%;padding:.75rem;background:#7c3aed;color:#fff;border:none;border-radius:.375rem;cursor:pointer}}
</style></head>
<body>
<h2>Register your device</h2>
<p>Enter your credentials to link this phone to your SelenaCore account.</p>
<form id="form">
  <input type="text" id="username" placeholder="Username" required>
  <input type="password" id="pin" placeholder="PIN" inputmode="numeric" maxlength="8" required>
  <input type="text" id="device_name" placeholder="Device name (e.g. My Phone)" value="My Phone">
  <button type="submit">Register</button>
</form>
<p id="msg" style="color:green;display:none"></p>
<p id="err" style="color:red;display:none"></p>
<script>
var SESSION_ID = "{session_id}";
document.getElementById("form").addEventListener("submit", async function(e) {{
  e.preventDefault();
  var btn = e.target.querySelector("button");
  btn.disabled = true;
  btn.textContent = "Registering…";
  try {{
    var BASE = window.location.pathname.replace(/\\/auth\\/qr\\/join\\/[^/]+/, '');
    var res = await fetch(BASE + "/auth/qr/complete/" + SESSION_ID, {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{
        username: document.getElementById("username").value,
        pin: document.getElementById("pin").value,
        device_name: document.getElementById("device_name").value || "My Phone"
      }})
    }});
    if (res.ok) {{
      var data = await res.json();
      localStorage.setItem("selena_device", data.device_token);
      document.getElementById("form").style.display = "none";
      document.getElementById("msg").style.display = "block";
      document.getElementById("msg").textContent = "Device registered! Welcome, " + data.display_name + ". You can close this page.";
    }} else {{
      var err = await res.json();
      document.getElementById("err").style.display = "block";
      document.getElementById("err").textContent = err.detail || "Registration failed";
      btn.disabled = false;
      btn.textContent = "Register";
    }}
  }} catch(ex) {{
    document.getElementById("err").style.display = "block";
    document.getElementById("err").textContent = "Network error: " + ex.message;
    btn.disabled = false;
    btn.textContent = "Register";
  }}
}});
</script>
</body></html>"""
            return HTMLResponse(content)

        # ── Current user (quick session check) ────────────────────────────────

        @router.get("/me")
        async def get_me(request: Request) -> dict:
            """Return current user info, or guest context if not authenticated."""
            info = await mod._devices.verify(mod._get_raw_token(request) or "")
            if info:
                return {"authenticated": True, **info}
            return {
                "authenticated": False,
                "role": "guest",
                "display_name": "Guest",
                "user_id": None,
            }

        # ── Users CRUD ─────────────────────────────────────────────────────────

        @router.get("/users")
        async def list_users(request: Request) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_role(info, "admin")
            profiles = await mod._users.list_all()
            return {"users": [asdict(p) for p in profiles]}

        @router.post("/users", status_code=201)
        async def create_user(req: CreateUserRequest, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            mod._require_role(info, "admin")
            try:
                profile = await mod._users.create(
                    username=req.username,
                    display_name=req.display_name,
                    pin=req.pin,
                    role=req.role,
                )
            except UserAlreadyExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except (ValueError, InvalidPinError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return asdict(profile)

        @router.get("/users/{user_id}")
        async def get_user(user_id: str, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            is_self = info["user_id"] == user_id
            if not is_self:
                mod._require_role(info, "admin")
            try:
                profile = await mod._users.get(user_id)
            except UserNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return asdict(profile)

        @router.patch("/users/{user_id}")
        async def update_user(user_id: str, req: UpdateUserRequest, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            mod._require_role(info, "admin")
            if req.role == "owner" and info["role"] != "owner":
                raise HTTPException(status_code=403, detail="Only owner can grant owner role")
            fields: dict[str, Any] = {}
            if req.display_name is not None:
                fields["display_name"] = req.display_name
            if req.role is not None:
                fields["role"] = req.role
            if req.active is not None:
                fields["active"] = req.active
            try:
                profile = await mod._users.update(user_id, **fields)
            except UserNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return asdict(profile)

        @router.delete("/users/{user_id}", status_code=204)
        async def delete_user(user_id: str, request: Request) -> None:
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            mod._require_role(info, "owner")
            if info["user_id"] == user_id:
                raise HTTPException(status_code=400, detail="Cannot deactivate own account")
            try:
                await mod._users.update(user_id, active=False)
            except UserNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

        @router.post("/users/{user_id}/pin")
        async def change_pin(user_id: str, req: ChangePinRequest, request: Request) -> dict:
            """Change a user's PIN.

            Regular users must supply their current PIN and an elevated token.
            Owner can change any user's PIN (still requires elevation).
            """
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            is_self = info["user_id"] == user_id
            if not is_self:
                mod._require_role(info, "owner")
            try:
                profile = await mod._users.get(user_id)
            except UserNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            if is_self and profile.pin_hash != _hash_pin(req.current_pin):
                raise HTTPException(status_code=401, detail="Current PIN is incorrect")
            try:
                await mod._users.update_pin(user_id, req.new_pin)
            except InvalidPinError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {"status": "ok", "user_id": user_id}

        # ── Registered devices ─────────────────────────────────────────────────

        @router.get("/users/{user_id}/devices")
        async def list_user_devices(user_id: str, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            is_self = info["user_id"] == user_id
            if not is_self:
                mod._require_role(info, "admin")
            devices = await mod._devices.list_by_user(user_id)
            return {"devices": [asdict(d) for d in devices]}

        @router.delete("/devices/{device_id}", status_code=204)
        async def revoke_specific_device(device_id: str, request: Request) -> None:
            info = await mod._require_device_auth(request)
            device = await mod._devices.get_by_id(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            if device.user_id != info["user_id"]:
                mod._require_role(info, "admin")
            await mod._devices.revoke(device_id)

        # ── Role permissions ───────────────────────────────────────────────────

        @router.get("/roles")
        async def list_roles(request: Request) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_role(info, "admin")
            all_perms = await mod._permissions.get_all()
            return {role: asdict(perms) for role, perms in all_perms.items()}

        @router.get("/roles/{role}/permissions")
        async def get_role_permissions(role: str, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_role(info, "admin")
            if role not in VALID_ROLES:
                raise HTTPException(status_code=404, detail=f"Unknown role '{role}'")
            perms = await mod._permissions.get(role)
            return asdict(perms)

        @router.put("/roles/{role}/permissions")
        async def set_role_permissions(
            role: str, req: PermissionsUpdateRequest, request: Request
        ) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            mod._require_role(info, "owner")
            if role not in VALID_ROLES:
                raise HTTPException(status_code=404, detail=f"Unknown role '{role}'")
            if role == "owner":
                raise HTTPException(status_code=400, detail="Owner permissions cannot be modified")
            current = await mod._permissions.get(role)
            updated = asdict(current)
            updated.update(req.model_dump(exclude_none=True))
            new_perms = RolePermissions(**updated)
            await mod._permissions.set(role, new_perms)
            return asdict(new_perms)

        # ── Widget / Settings HTML ─────────────────────────────────────────────

        @router.get("/widget.html", response_class=HTMLResponse)
        async def widget_html(request: Request) -> HTMLResponse:
            p = Path(__file__).parent / "widget.html"
            return HTMLResponse(p.read_text() if p.exists() else "<p>User Manager</p>")

        @router.get("/settings.html", response_class=HTMLResponse)
        async def settings_html(request: Request) -> HTMLResponse:
            p = Path(__file__).parent / "settings.html"
            return HTMLResponse(p.read_text() if p.exists() else "<p>Settings</p>")

        return router
