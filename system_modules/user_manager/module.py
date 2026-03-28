"""
system_modules/user_manager/module.py — User Manager SystemModule.

Runs in-process inside smarthome-core.  Provides:

  • Device-token authentication (HttpOnly cookie + X-Device-Token header)
  • Short-lived elevated sessions for sensitive operations
  • User CRUD (flat model: admin + residents, no role-based permissions)
  • QR-based device registration flow

All API routes are mounted at /api/ui/modules/user-manager/
"""
from __future__ import annotations

import json
import logging
import os
import socket
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
from system_modules.user_manager.pin_auth import get_pin_auth
from system_modules.user_manager.elevated import ElevatedManager
from system_modules.user_manager.profiles import (
    InvalidPinError,
    UserAlreadyExistsError,
    UserManager,
    UserNotFoundError,
    _hash_pin,
)
from system_modules.user_manager.sessions import BrowserSessionManager

logger = logging.getLogger(__name__)

DB_URL = os.environ.get("SELENA_DB_URL", "sqlite+aiosqlite:////var/lib/selena/selena.db")
PRESENCE_DB = os.environ.get("SELENA_PRESENCE_DB", "/var/lib/selena/presence.db")


def _lookup_presence_by_ip(client_ip: str) -> dict[str, Any] | None:
    """Resolve client IP → MAC (ARP) → presence user → linked account.

    Returns dict with user_id, role, display_name if found, else None.
    Runs synchronously (reads /proc/net/arp + SQLite), so wrap in executor
    for async contexts.
    """
    from system_modules.presence_detection.presence import _read_arp_table

    # Step 1: IP → MAC via ARP table
    arp = _read_arp_table()  # {ip: mac}
    mac = arp.get(client_ip)
    if not mac:
        return None

    mac = mac.lower()

    # Step 2: MAC → presence user → linked_account_id
    if not os.path.exists(PRESENCE_DB):
        return None

    import sqlite3 as _sqlite3
    try:
        db = _sqlite3.connect(PRESENCE_DB)
        db.row_factory = _sqlite3.Row
        rows = db.execute(
            "SELECT user_id, name, devices, linked_account_id FROM presence_users"
        ).fetchall()
        db.close()
    except Exception:
        return None

    for row in rows:
        linked_account_id = row["linked_account_id"]
        if not linked_account_id:
            continue
        try:
            devices = json.loads(row["devices"]) if row["devices"] else []
        except (json.JSONDecodeError, TypeError):
            continue
        for dev in devices:
            if dev.get("type") == "mac" and dev.get("address", "").lower() == mac:
                return {
                    "presence_user_id": row["user_id"],
                    "presence_name": row["name"],
                    "linked_account_id": linked_account_id,
                }

    return None


async def _async_lookup_presence(client_ip: str) -> dict[str, Any] | None:
    """Non-blocking wrapper around _lookup_presence_by_ip."""
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _lookup_presence_by_ip, client_ip)


def _get_lan_ip() -> str:
    """Return the primary LAN IP of this host (not loopback)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as _s:
            _s.settimeout(0.05)
            _s.connect(("8.8.8.8", 80))
            return _s.getsockname()[0]
    except Exception:
        return ""

_DEVICE_COOKIE = "selena_device"
_ELEVATED_HEADER = "X-Elevated-Token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30   # 30 days
_QR_TTL = 300                           # 5 minutes

# ── Pydantic models ────────────────────────────────────────────────────────────

class RegisterDeviceRequest(BaseModel):
    username: str = ""   # optional — ignored for PIN-only login
    pin: str
    device_name: str = "My Device"


class PinConfirmRequest(BaseModel):
    pin: str
    username: str = ""  # optional — if empty, tries all users


class ElevatedRevokeRequest(BaseModel):
    elevated_token: str


class ElevatedRefreshRequest(BaseModel):
    elevated_token: str


class CreateUserRequest(BaseModel):
    username: str
    display_name: str
    pin: str


class UpdateUserRequest(BaseModel):
    display_name: str | None = None
    active: bool | None = None


class ChangePinRequest(BaseModel):
    current_pin: str
    new_pin: str


class QrStartRequest(BaseModel):
    mode: str = "access"  # "access" | "elevate" | "invite" | "wizard_setup"
    display_name: str = ""  # pre-fill hint for invite
    wizard_username: str = ""  # only for wizard_setup: admin username from step 7
    wizard_pin: str = ""       # only for wizard_setup: admin PIN from step 7


class WizardPhoneLinkRequest(BaseModel):
    device_name: str = "My Phone"


# ── Module class ───────────────────────────────────────────────────────────────

class UserManagerModule(SystemModule):
    name = "user-manager"

    def __init__(self) -> None:
        super().__init__()
        engine = create_async_engine(DB_URL, echo=False)
        self._users = UserManager(DB_URL)
        self._devices = DeviceManager(engine)
        self._elevated = ElevatedManager()
        self._sessions = BrowserSessionManager(db_path="/var/lib/selena/selena.db")
        # QR pending sessions: session_id → {expires_at, status, device_token?, user_id?}
        self._qr_sessions: dict[str, dict[str, Any]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await self._users._get_engine()          # triggers ensure_tables via lazy init
        await self._devices.ensure_tables()
        self._elevated.start_cleanup()
        self._sessions.start_cleanup()
        await self.publish("module.started", {"name": self.name})
        logger.info("UserManager module started")

    async def stop(self) -> None:
        await self._elevated.stop_cleanup()
        await self._sessions.stop_cleanup()
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
        """Extract device token from X-Device-Token header (priority) or cookie."""
        return request.headers.get("X-Device-Token") or request.cookies.get(_DEVICE_COOKIE)

    async def _require_device_auth(self, request: Request) -> dict[str, Any]:
        """Return verified user info dict or raise 401.

        Checks (in order):
        1. Elevated token (PIN-only flow — no device token needed)
        2. Browser session token (QR-issued temporary tokens)
        3. Persistent device token
        """
        # 1. Elevated token — PIN-only flow
        elevated_token = request.headers.get(_ELEVATED_HEADER)
        if elevated_token:
            user_id = self._elevated.get_user_id(elevated_token)
            if user_id:
                try:
                    profile = await self._users.get(user_id)
                    return {"user_id": user_id, "role": profile.role, "display_name": profile.display_name}
                except Exception:
                    pass

        # 2. Session / device token
        raw = self._get_raw_token(request)
        if raw:
            session_info = self._sessions.verify(raw)
            if session_info:
                return session_info
            info = await self._devices.verify(raw)
            if info:
                return info

        raise HTTPException(status_code=401, detail="Authentication required")

    def _require_elevated(self, request: Request, user_info: dict[str, Any]) -> None:
        """Raise 403 if no valid elevated token is present for the current user."""
        elevated_token = request.headers.get(_ELEVATED_HEADER)
        if not elevated_token:
            raise HTTPException(status_code=403, detail="Elevated session required")
        if not self._elevated.verify(elevated_token, user_info["user_id"]):
            raise HTTPException(status_code=403, detail="Elevated session expired or invalid")

    def _cleanup_qr_sessions(self) -> None:
        now = time.time()
        expired = [sid for sid, s in self._qr_sessions.items() if s["expires_at"] < now]
        for sid in expired:
            self._qr_sessions.pop(sid, None)

    # ── Router ────────────────────────────────────────────────────────────────

    def get_router(self) -> APIRouter:  # noqa: C901
        router = APIRouter()
        mod = self

        # ── Auth: system status (setup required?) ─────────────────────────────

        @router.get("/auth/status")
        async def auth_status() -> dict:
            """Return whether first-time setup is needed.

            Called by AuthWall on mount to decide which UI to show:
            - setup_required=true  → show "Create first account" form
            - setup_required=false → show normal login form
            """
            count = await mod._users.count_users()
            return {"setup_required": count == 0}

        # ── Auth: first-time owner setup ─────────────────────────────────────

        @router.post("/auth/setup", status_code=201)
        async def first_setup(
            req: RegisterDeviceRequest,
            response: Response,
            request: Request,
        ) -> dict:
            """Bootstrap the system with the first owner account.

            Only works when no users exist at all (empty database).
            Creates the owner account and immediately registers the calling
            device, returning a device_token + cookie.

            The endpoint is automatically locked after the first call —
            subsequent calls return 409.
            """
            count = await mod._users.count_users()
            if count > 0:
                raise HTTPException(
                    status_code=409,
                    detail="System already initialized. Use /auth/device/register instead.",
                )
            try:
                profile = await mod._users.create(
                    username=req.username,
                    display_name=req.device_name or req.username,
                    pin=req.pin,
                )
            except UserAlreadyExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except (ValueError, InvalidPinError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            ip = (request.client.host if request.client else "") or ""
            ua = request.headers.get("user-agent", "")
            plain_token = await mod._devices.register(
                user_id=profile.user_id,
                device_name=ua[:60] or "First device",
                user_agent=ua,
                ip=ip,
            )
            mod._set_device_cookie(response, plain_token)
            logger.info(
                "First admin account created: username=%s ip=%s", req.username, ip
            )
            return {
                "device_token": plain_token,
                "user_id": profile.user_id,
                "role": profile.role,
                "display_name": profile.display_name,
            }

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

            ip = (request.client.host if request.client else "") or ""
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
            """Validate PIN and issue a 5-minute elevated session token.

            No-token path: any browser without a device token sends username + PIN.
            Device-token path: registered device sends device token + PIN.
            """
            # Try device-token path first (if token present and valid)
            raw_token = mod._get_raw_token(request)
            if raw_token:
                info = await mod._devices.verify(raw_token)
                if info:
                    if not await mod._users.verify_pin(info["user_id"], req.pin):
                        raise HTTPException(status_code=401, detail="Incorrect PIN")
                    elevated_token = mod._elevated.grant(info["user_id"])
                    return {
                        "elevated_token": elevated_token,
                        "expires_in": 300,
                        "user_id": info["user_id"],
                        "role": info["role"],
                    }
                # Token invalid/revoked — fall through to PIN-only path

            # PIN-only path — find user by PIN (optionally by username)
            if req.username:
                profile = await mod._users.get_by_username(req.username)
            else:
                profile = await mod._users.find_by_pin(req.pin)
            if not profile:
                raise HTTPException(status_code=401, detail="Invalid PIN")
            ok, msg = await get_pin_auth().authenticate(profile.user_id, req.pin, profile.pin_hash)
            if not ok:
                raise HTTPException(status_code=401, detail=msg)
            elevated_token = mod._elevated.grant(profile.user_id)
            return {
                "elevated_token": elevated_token,
                "expires_in": 300,
                "user_id": profile.user_id,
                "role": profile.role,
            }

        # ── Auth: Elevated session revoke / refresh ────────────────────────────

        @router.post("/auth/elevated/revoke", status_code=200)
        async def revoke_elevated(req: ElevatedRevokeRequest) -> dict:
            """Immediately invalidate an elevated session token."""
            mod._elevated.revoke(req.elevated_token)
            return {"ok": True}

        @router.post("/auth/elevated/refresh", status_code=200)
        async def refresh_elevated(req: ElevatedRefreshRequest) -> dict:
            """Reset the TTL of an elevated session (sliding-window keep-alive).

            Called by the frontend every ~10 s while the user is active.
            Returns 401 if the token has already expired — frontend clears the session.
            """
            ok = mod._elevated.refresh(req.elevated_token)
            if not ok:
                raise HTTPException(status_code=401, detail="Elevated session expired")
            return {"ok": True, "expires_in": 300}

        # ── Auth: QR registration flow ─────────────────────────────────────────

        @router.post("/auth/qr/approve/{session_id}", status_code=200)
        async def qr_approve(session_id: str, request: Request) -> dict:
            """Approve a pending QR session using an already-registered device.

            The scanning phone sends this request with its device_token cookie.
            - mode == "access": issues a new device_token for the waiting browser
              (registered under the approver's account).
            - mode == "elevate": issues an elevated_token for the waiting client.

            If the approver is NOT registered this endpoint returns 401 —
            the caller (phone) sees the error, the polling browser stays pending.
            """
            info = await mod._require_device_auth(request)

            session = mod._qr_sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="QR session not found or expired")
            if time.time() > session["expires_at"]:
                mod._qr_sessions.pop(session_id, None)
                raise HTTPException(status_code=410, detail="QR session expired")
            if session["status"] != "pending":
                raise HTTPException(status_code=409, detail="QR session already completed")

            mode = session.get("mode", "access")
            if mode == "elevate":
                elevated_token = mod._elevated.grant(info["user_id"])
                session.update({
                    "status": "complete",
                    "elevated_token": elevated_token,
                    "user_id": info["user_id"],
                    "approved_by": info.get("display_name", info.get("name", "")),
                })
            else:
                # Issue a temporary browser session — no new device registered
                session_token = mod._sessions.grant(
                    user_id=info["user_id"],
                    role=info["role"],
                    display_name=info.get("display_name", info.get("name", "")),
                    device_name=f"Browser (approved by {info.get('display_name', '')})",
                )
                session.update({
                    "status": "complete",
                    "device_token": session_token,
                    "session_token": True,
                    "user_id": info["user_id"],
                    "approved_by": info.get("display_name", info.get("name", "")),
                })

            logger.info(
                "QR session %s approved (mode=%s) by user %s",
                session_id, mode, info["user_id"],
            )
            return {"approved": True, "mode": mode}

        # ── Auth: Presence-based phone identification ──────────────────────────

        @router.post("/auth/phone/identify")
        async def phone_identify(request: Request) -> dict:
            """Identify the calling phone by IP → MAC → presence user → account.

            Returns the linked account info if the phone is a tracked presence
            device.  Used by qr_join.html to decide whether to show the
            one-tap "Approve" button or the username/PIN form.
            """
            client_ip = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or request.headers.get("x-real-ip", "")
                or ((request.client.host if request.client else "") or "")
            )
            if not client_ip:
                raise HTTPException(status_code=400, detail="Cannot determine client IP")

            presence_info = await _async_lookup_presence(client_ip)
            if not presence_info:
                return {"identified": False}

            # Resolve the linked account to get role + display_name
            account_id = presence_info["linked_account_id"]
            profile = await mod._users.get(account_id)
            if not profile or not profile.active:
                return {"identified": False}

            return {
                "identified": True,
                "user_id": profile.user_id,
                "display_name": profile.display_name or profile.username,
                "role": profile.role,
                "presence_name": presence_info["presence_name"],
            }

        @router.post("/auth/qr/approve-by-presence/{session_id}", status_code=200)
        async def qr_approve_by_presence(session_id: str, request: Request) -> dict:
            """Approve a QR session using presence-based phone identity.

            No device_token required — the phone is identified by its
            IP → MAC → presence tracking → linked account.
            Only works if the linked account has role >= user.
            """
            client_ip = (
                request.headers.get("x-forwarded-for", "").split(",")[0].strip()
                or request.headers.get("x-real-ip", "")
                or ((request.client.host if request.client else "") or "")
            )
            if not client_ip:
                raise HTTPException(status_code=400, detail="Cannot determine client IP")

            presence_info = await _async_lookup_presence(client_ip)
            if not presence_info:
                raise HTTPException(
                    status_code=401,
                    detail="Phone not recognized as a tracked presence device",
                )

            account_id = presence_info["linked_account_id"]
            profile = await mod._users.get(account_id)
            if not profile or not profile.active:
                raise HTTPException(status_code=401, detail="Linked account not found or inactive")

            # Build info dict matching _require_device_auth() output format
            info = {
                "user_id": profile.user_id,
                "role": profile.role,
                "display_name": profile.display_name or profile.username,
                "name": profile.username,
            }

            session = mod._qr_sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="QR session not found or expired")
            if time.time() > session["expires_at"]:
                mod._qr_sessions.pop(session_id, None)
                raise HTTPException(status_code=410, detail="QR session expired")
            if session["status"] != "pending":
                raise HTTPException(status_code=409, detail="QR session already completed")

            mode = session.get("mode", "access")
            if mode == "elevate":
                elevated_token = mod._elevated.grant(info["user_id"])
                session.update({
                    "status": "complete",
                    "elevated_token": elevated_token,
                    "user_id": info["user_id"],
                    "approved_by": info.get("display_name", ""),
                })
            else:
                session_token = mod._sessions.grant(
                    user_id=info["user_id"],
                    role=info["role"],
                    display_name=info.get("display_name", ""),
                    device_name=f"Browser (approved by {info.get('display_name', '')})",
                )
                session.update({
                    "status": "complete",
                    "device_token": session_token,
                    "session_token": True,
                    "user_id": info["user_id"],
                    "approved_by": info.get("display_name", ""),
                })

            logger.info(
                "QR session %s approved via presence (mode=%s) by account %s (ip=%s)",
                session_id, mode, info["user_id"], client_ip,
            )
            return {"approved": True, "mode": mode}

        @router.post("/auth/qr/start", status_code=201)
        async def qr_start(req: QrStartRequest, request: Request) -> dict:
            """Generate a one-time QR session.

            mode="access"  — phone scans, approves, waiting browser gets device_token.
            mode="elevate" — kiosk shows QR, phone approves, kiosk gets elevated_token.

            Poll ``GET /auth/qr/status/{session_id}`` to detect completion.
            """
            session_id = str(uuid.uuid4())
            expires_at = time.time() + _QR_TTL
            session_data: dict[str, Any] = {
                "status": "pending",
                "mode": req.mode,
                "display_name": req.display_name,
                "expires_at": expires_at,
                "device_token": None,
                "elevated_token": None,
                "user_id": None,
            }
            if req.mode == "wizard_setup" and req.wizard_username and req.wizard_pin:
                session_data["wizard_username"] = req.wizard_username
                session_data["wizard_pin_hash"] = _hash_pin(req.wizard_pin)
                session_data["display_name"] = req.wizard_username
            mod._qr_sessions[session_id] = session_data
            mod._cleanup_qr_sessions()

            # Build a join URL that the *phone* can reach.
            # If the request came from localhost/127.0.0.1 (kiosk) we swap in the
            # actual LAN IP + UI port so the QR encodes a reachable address.
            x_proto = request.headers.get("x-forwarded-proto", "http")
            host_hdr = (
                request.headers.get("x-forwarded-host")
                or request.headers.get("host")
                or ""
            )
            hostname = host_hdr.split(":")[0]
            if hostname in ("localhost", "127.0.0.1", "::1", ""):
                lan_ip = _get_lan_ip()
                # Prefer HTTPS on :443 if self-signed TLS cert is present
                _tls_cert = Path("/secure/tls/selena.crt")
                if _tls_cert.exists():
                    scheme = "https"
                    port_sfx = ""  # 443 is implicit for https
                else:
                    scheme = x_proto
                    ui_port = int(os.environ.get("UI_PORT", "80"))
                    port_sfx = "" if ui_port == 80 else f":{ui_port}"
                base = f"{scheme}://{lan_ip}{port_sfx}" if lan_ip else str(request.base_url).rstrip("/")
            else:
                base = f"{x_proto}://{host_hdr}"
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

            On completion returns one of:
            - ``{status: "complete", device_token: "...", user_id: "..."}`` (access mode)
            - ``{status: "complete", elevated_token: "...", user_id: "..."}`` (elevate mode)
            """
            session = mod._qr_sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="QR session not found or expired")
            if time.time() > session["expires_at"]:
                mod._qr_sessions.pop(session_id, None)
                raise HTTPException(status_code=410, detail="QR session expired")

            result: dict[str, Any] = {"status": session["status"]}
            if session["status"] == "complete":
                if session.get("device_token"):
                    result["device_token"] = session["device_token"]
                    result["session_token"] = bool(session.get("session_token"))
                if session.get("elevated_token"):
                    result["elevated_token"] = session["elevated_token"]
                result["user_id"] = session.get("user_id")
                mod._qr_sessions.pop(session_id, None)
                logger.info("QR poll → complete (session_id=%s, token_prefix=%.8s, session_token=%s)",
                           session_id, result.get("device_token", ""), result.get("session_token"))
            return result

        @router.get("/auth/qr/info/{session_id}")
        async def qr_info(session_id: str) -> dict:
            """Return session metadata (mode, remaining seconds, status).

            Used by the phone's join page to show the correct UI and countdown.
            """
            session = mod._qr_sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="QR session not found or expired")
            remaining = session["expires_at"] - time.time()
            if remaining <= 0:
                mod._qr_sessions.pop(session_id, None)
                raise HTTPException(status_code=410, detail="QR session expired")
            return {
                "mode": session["mode"],
                "display_name": session.get("display_name", ""),
                "status": session["status"],
                "expires_in_seconds": int(remaining),
            }

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

            ip = (request.client.host if request.client else "") or ""
            ua = request.headers.get("user-agent", "")

            if session["mode"] == "invite":
                # Create new account + register device in one step
                try:
                    profile = await mod._users.create(
                        username=req.username,
                        display_name=req.device_name or req.username,
                        pin=req.pin,
                    )
                except UserAlreadyExistsError:
                    raise HTTPException(status_code=409, detail="Username already taken")
                except (InvalidPinError, ValueError) as exc:
                    raise HTTPException(status_code=422, detail=str(exc))
            else:
                profile = await mod._users.get_by_username(req.username)
                if not profile or not profile.active:
                    raise HTTPException(status_code=401, detail="Invalid credentials")
                if profile.pin_hash != _hash_pin(req.pin):
                    raise HTTPException(status_code=401, detail="Invalid credentials")

            # Register the phone as a device (so next time it can use Approve)
            plain_token = await mod._devices.register(
                user_id=profile.user_id,
                device_name=req.device_name,
                user_agent=ua,
                ip=ip,
            )
            mod._set_device_cookie(response, plain_token)

            # For access mode: issue a temporary session for the BROWSER
            # (the phone keeps its permanent device_token)
            if session["mode"] == "access":
                browser_session = mod._sessions.grant(
                    user_id=profile.user_id,
                    role=profile.role,
                    display_name=profile.display_name or profile.username,
                    device_name=f"Browser (via {req.device_name or 'QR'})",
                )
                session["status"] = "complete"
                session["device_token"] = browser_session
                session["session_token"] = True
                session["user_id"] = profile.user_id
            else:
                session["status"] = "complete"
                session["device_token"] = plain_token
                session["user_id"] = profile.user_id

            return {
                "device_token": plain_token,
                "user_id": profile.user_id,
                "role": profile.role,
                "display_name": profile.display_name,
            }

        # ── Wizard phone link (phone only sends device_name) ──────────────────

        @router.post("/auth/qr/wizard-link/{session_id}", status_code=201)
        async def qr_wizard_link(
            session_id: str,
            req: WizardPhoneLinkRequest,
            response: Response,
            request: Request,
        ) -> dict:
            """Link phone to admin account during wizard setup.

            The admin username + PIN were stored in the QR session when
            the kiosk started it (wizard step 8). The phone only provides
            a device_name.
            """
            session = mod._qr_sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="QR session not found")
            if time.time() > session["expires_at"]:
                mod._qr_sessions.pop(session_id, None)
                raise HTTPException(status_code=410, detail="QR session expired")
            if session["status"] != "pending":
                raise HTTPException(status_code=409, detail="QR session already completed")
            if session["mode"] != "wizard_setup":
                raise HTTPException(status_code=400, detail="Not a wizard setup session")

            wizard_username = session.get("wizard_username", "")
            wizard_pin_hash = session.get("wizard_pin_hash", "")
            if not wizard_username or not wizard_pin_hash:
                raise HTTPException(status_code=400, detail="Wizard session missing credentials")

            ip = (request.client.host if request.client else "") or ""
            ua = request.headers.get("user-agent", "")

            # Create admin user if not exists
            profile = await mod._users.get_by_username(wizard_username)
            if not profile:
                try:
                    profile = await mod._users.create(
                        username=wizard_username,
                        display_name=wizard_username,
                        pin="0000",  # dummy — we'll overwrite hash directly
                    )
                    # Overwrite with the correct hash from wizard
                    await mod._users._execute(
                        "UPDATE users SET pin_hash = :pin_hash WHERE user_id = :user_id",
                        {"pin_hash": wizard_pin_hash, "user_id": profile.user_id},
                    )
                    logger.info("Wizard: created admin account '%s'", wizard_username)
                except UserAlreadyExistsError:
                    profile = await mod._users.get_by_username(wizard_username)

            # Register the phone as a device
            plain_token = await mod._devices.register(
                user_id=profile.user_id,
                device_name=req.device_name,
                user_agent=ua,
                ip=ip,
            )
            mod._set_device_cookie(response, plain_token)

            # Mark QR session complete — the kiosk poll will detect this
            session["status"] = "complete"
            session["device_token"] = plain_token
            session["user_id"] = profile.user_id
            session["display_name"] = profile.display_name or profile.username

            logger.info(
                "Wizard: phone linked for admin '%s', device='%s' ip=%s",
                wizard_username, req.device_name, ip,
            )

            # Auto-add phone to presence tracking (IP → MAC → presence user)
            phone_mac = ""
            presence_user_id = profile.username.lower().replace(" ", "-")
            try:
                from system_modules.presence_detection.presence import _read_arp_table
                arp = await asyncio.get_event_loop().run_in_executor(None, _read_arp_table)
                phone_mac = arp.get(ip, "").lower()
                if phone_mac:
                    from core.module_loader.sandbox import get_sandbox
                    pd = get_sandbox().get_in_process_module("presence-detection")
                    if pd and pd._detector:
                        presence_user_id = profile.username.lower().replace(" ", "-")
                        pd._detector.add_user({
                            "user_id": presence_user_id,
                            "name": profile.display_name or profile.username,
                            "devices": [
                                {"type": "mac", "address": phone_mac},
                                {"type": "ip", "address": ip},
                            ],
                            "linked_account_id": profile.user_id,
                        })
                        logger.info(
                            "Wizard: auto-added presence user '%s' mac=%s linked to %s",
                            presence_user_id, phone_mac, profile.user_id,
                        )
                else:
                    logger.debug("Wizard: could not resolve MAC for phone ip=%s", ip)
            except Exception as exc:
                logger.warning("Wizard: failed to auto-add presence tracking: %s", exc)

            return {
                "device_token": plain_token,
                "user_id": profile.user_id,
                "presence_user_id": presence_user_id,
                "role": profile.role,
                "display_name": profile.display_name,
            }

        # ── QR join page (simple HTML form for mobile) ─────────────────────────

        @router.get("/auth/qr/join/{session_id}", response_class=HTMLResponse)
        async def qr_join_page(session_id: str) -> HTMLResponse:
            p = Path(__file__).parent / "qr_join.html"
            content = p.read_text().replace("{session_id}", session_id) if p.exists() else "<p>Not found</p>"
            return HTMLResponse(
                content,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )

        # ── Current user (quick session check) ────────────────────────────────

        @router.get("/me")
        async def get_me(request: Request) -> dict:
            """Return current user info, or guest context if not authenticated."""
            raw = mod._get_raw_token(request) or ""
            token_src = "header" if request.headers.get("X-Device-Token") else ("cookie" if request.cookies.get(_DEVICE_COOKIE) else "none")
            token_from_header = request.headers.get("X-Device-Token") or ""
            token_from_cookie = request.cookies.get(_DEVICE_COOKIE) or ""
            logger.info("/me tokens: header=%.8s… cookie=%.8s… using=%.8s…",
                        token_from_header, token_from_cookie, raw)
            # Check browser session first
            session_info = mod._sessions.verify(raw)
            if session_info:
                logger.info("/me → session OK (user=%s, src=%s)", session_info.get("user_id"), token_src)
                return {"authenticated": True, "session": True, **session_info}
            # Check persistent device token
            info = await mod._devices.verify(raw)
            if info:
                logger.info("/me → device OK (user=%s, src=%s)", info.get("user_id"), token_src)
                return {"authenticated": True, "session": False, **info}
            # Log token prefix for debugging mismatch
            session_tokens = list(mod._sessions._sessions.keys())
            logger.info("/me → guest (token=%.8s…, src=%s, active_sessions=%d, session_keys=[%s])",
                        raw, token_src, len(session_tokens),
                        ", ".join(t[:8] for t in session_tokens))
            return {
                "authenticated": False,
                "role": "guest",
                "display_name": "Guest",
                "user_id": None,
            }

        # ── Browser session heartbeat & logout ─────────────────────────────────

        @router.post("/auth/session/heartbeat")
        async def session_heartbeat(request: Request) -> dict:
            """Reset the idle timer for a temporary browser session.

            Returns remaining seconds.  If session is expired → 401.
            """
            raw = mod._get_raw_token(request) or ""
            result = mod._sessions.heartbeat(raw)
            if result is None:
                raise HTTPException(status_code=401, detail="Session expired or invalid")
            return result

        @router.post("/auth/session/logout")
        async def session_logout(request: Request) -> dict:
            """End a temporary browser session immediately."""
            raw = mod._get_raw_token(request) or ""
            mod._sessions.revoke(raw)
            return {"status": "ok"}

        # ── Users CRUD ─────────────────────────────────────────────────────────

        @router.get("/users")
        async def list_users(request: Request) -> dict:
            info = await mod._require_device_auth(request)
            profiles = await mod._users.list_all()
            return {"users": [asdict(p) for p in profiles]}

        @router.post("/users", status_code=201)
        async def create_user(req: CreateUserRequest, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            try:
                profile = await mod._users.create(
                    username=req.username,
                    display_name=req.display_name,
                    pin=req.pin,
                )
            except UserAlreadyExistsError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except (ValueError, InvalidPinError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return asdict(profile)

        @router.get("/users/{user_id}")
        async def get_user(user_id: str, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            try:
                profile = await mod._users.get(user_id)
            except UserNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return asdict(profile)

        @router.patch("/users/{user_id}")
        async def update_user(user_id: str, req: UpdateUserRequest, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            fields: dict[str, Any] = {}
            if req.display_name is not None:
                fields["display_name"] = req.display_name
            if req.active is not None:
                fields["active"] = req.active
            try:
                profile = await mod._users.update(user_id, **fields)
            except UserNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return asdict(profile)

        @router.delete("/users/{user_id}", status_code=204, response_model=None)
        async def delete_user(user_id: str, request: Request) -> None:
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            if info["user_id"] == user_id:
                raise HTTPException(status_code=400, detail="Cannot deactivate own account")
            try:
                await mod._users.get(user_id)
            except UserNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            await mod._users.update(user_id, active=False)

        @router.post("/users/{user_id}/pin")
        async def change_pin(user_id: str, req: ChangePinRequest, request: Request) -> dict:
            """Change a user's PIN.

            Regular users must supply their current PIN and an elevated token.
            Owner can change any user's PIN (still requires elevation).
            """
            info = await mod._require_device_auth(request)
            mod._require_elevated(request, info)
            is_self = info["user_id"] == user_id
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
            devices = await mod._devices.list_by_user(user_id)
            return {"devices": [asdict(d) for d in devices]}

        @router.delete("/devices/{device_id}", status_code=204, response_model=None)
        async def revoke_specific_device(device_id: str, request: Request) -> None:
            info = await mod._require_device_auth(request)
            device = await mod._devices.get_by_id(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            await mod._devices.revoke(device_id)

        @router.patch("/devices/{device_id}")
        async def rename_device(device_id: str, req: dict, request: Request) -> dict:
            info = await mod._require_device_auth(request)
            device = await mod._devices.get_by_id(device_id)
            if not device:
                raise HTTPException(status_code=404, detail="Device not found")
            new_name = str(req.get("device_name", "")).strip()
            if not new_name:
                raise HTTPException(status_code=422, detail="device_name required")
            updated = await mod._devices.rename(device_id, new_name)
            if not updated:
                raise HTTPException(status_code=404, detail="Device not found or already revoked")
            return {"device_id": device_id, "device_name": new_name}

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
