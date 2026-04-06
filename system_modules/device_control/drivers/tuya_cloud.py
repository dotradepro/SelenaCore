"""
system_modules/device_control/drivers/tuya_cloud.py — Tuya Cloud via user-code.

NEW auth flow (since v2): uses the official ``tuya-device-sharing-sdk``
(the same library Home Assistant 2024.2+ uses for its "Smart Life" integration).

Why not developer credentials:
    The old flow required creating a cloud project on iot.tuya.com, subscribing
    to IoT Core + Smart Home Basic Service, linking the Smart Life app account
    via QR, and providing Access ID / Access Secret. That flow is now blocked
    for individual users because Tuya requires a business review for the
    developer account.

New flow (personal-use friendly):
    1. User opens Smart Life app → Me tab → ⚙️ icon → "Get authorization code"
       (or similar; the exact label changed over versions). A 7-character
       alphanumeric code appears, valid for 10 minutes.
    2. User enters that code in the SelenaCore wizard.
    3. SelenaCore calls ``LoginControl.qr_code(client_id, schema, user_code)``
       which returns a QR-code payload encoded as a URL.
    4. SelenaCore renders the QR in the wizard UI; the user scans it with the
       **same Smart Life app** (the camera inside the app, not a system camera).
    5. After the tap "Authorize" in Smart Life, SelenaCore polls
       ``LoginControl.login_result(token, client_id, user_code)`` every 2
       seconds; on success Tuya returns ``{access_token, refresh_token,
       endpoint, terminal_id, uid, expire_time}``.
    6. Those creds are stored in SecretsVault and used to build a persistent
       ``tuya_sharing.Manager`` for ongoing device control + push updates via
       ``SharingDeviceListener``.

HA client constants (``HA_3y9q4ak7g4ephrvke`` / ``haauthorize``) are hardcoded
because personal-use integrations don't register their own app with Tuya —
the SDK treats ``client_id`` as an opaque identifier and Tuya cloud accepts
the HA one for any community integration.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncGenerator

from .base import DeviceDriver, DriverError

logger = logging.getLogger(__name__)

# Constants from home-assistant/core tuya integration — the SDK uses these as
# an opaque "app identifier" string, not as an actual OAuth client_id.
TUYA_CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
TUYA_SCHEMA = "haauthorize"

_VAULT_SERVICE = "device-control_tuya_cloud"


# ── Shared session manager (singleton) ────────────────────────────────────


class TuyaCloudClient:
    """Singleton wrapper around tuya_sharing.Manager + LoginControl.

    Holds one authenticated Manager for the whole process. Owns the push-event
    queue used by TuyaCloudDriver.stream_events().
    """

    _instance: "TuyaCloudClient | None" = None

    def __init__(self) -> None:
        self._manager: Any = None  # tuya_sharing.Manager
        self._login_control: Any = None  # tuya_sharing.LoginControl
        self._pending_qr: dict[str, dict] = {}  # user_code → {token, qrcode, created_at}
        self._device_listeners: dict[str, "asyncio.Queue[dict]"] = {}
        self._lock = asyncio.Lock()
        self._main_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def get(cls) -> "TuyaCloudClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton so the next ``get()`` rebuilds from fresh creds."""
        inst = cls._instance
        if inst is not None and inst._manager is not None:
            try:
                inst._manager.unload()
            except Exception:
                pass
        cls._instance = None

    # ── Credential persistence ──────────────────────────────────────────

    def _load_token(self) -> dict | None:
        """Load the stored tuya-sharing token response from the vault.

        Returns a dict in the exact shape ``Manager`` expects, or None.
        """
        try:
            from system_modules.secrets_vault.vault import get_vault
            rec = get_vault().load(_VAULT_SERVICE)
        except Exception:
            return None
        if rec is None or rec.extra is None:
            return None
        token_response = {
            "access_token": rec.access_token,
            "refresh_token": rec.refresh_token or "",
            "expire_time": int(rec.extra.get("expire_time", 0)),
            "uid": rec.extra.get("uid", ""),
        }
        stored_endpoint = rec.extra.get("endpoint", "") or ""
        # Normalise endpoint — older wizards stored it without the https://
        # prefix, which breaks tuya_sharing CustomerApi URL concatenation.
        if stored_endpoint and not stored_endpoint.startswith(("http://", "https://")):
            stored_endpoint = "https://" + stored_endpoint
            token_response["endpoint"] = stored_endpoint
        extras = {
            "endpoint": stored_endpoint,
            "terminal_id": rec.extra.get("terminal_id", ""),
            "user_code": rec.extra.get("user_code", ""),
            "token_response": token_response,
        }
        if not all([
            token_response["access_token"],
            extras["endpoint"],
            extras["terminal_id"],
            extras["user_code"],
        ]):
            return None
        return extras

    @staticmethod
    def _store_token(info: dict, user_code: str, terminal_id: str) -> None:
        """Persist the successful login response to the vault.

        ``info`` is the dict returned by ``LoginControl.login_result()`` —
        shape: ``{access_token, refresh_token, expire_time, uid, endpoint}``.
        """
        from system_modules.secrets_vault.vault import SecretRecord, get_vault
        get_vault().store(SecretRecord(
            service=_VAULT_SERVICE,
            access_token=info.get("access_token", ""),
            refresh_token=info.get("refresh_token", ""),
            expires_at=float(info.get("expire_time", 0) or 0) or None,
            extra={
                "endpoint": info.get("endpoint", ""),
                "uid": info.get("uid", ""),
                "terminal_id": terminal_id,
                "user_code": user_code,
                "expire_time": info.get("expire_time", 0),
            },
        ))

    @staticmethod
    def wipe_creds() -> None:
        from system_modules.secrets_vault.vault import get_vault
        try:
            get_vault().delete(_VAULT_SERVICE)
        except Exception:
            pass

    def status_summary(self) -> dict:
        """Return a safe summary of stored creds for the UI status badge."""
        from system_modules.secrets_vault.vault import get_vault
        try:
            rec = get_vault().load(_VAULT_SERVICE)
        except Exception:
            return {"connected": False}
        if rec is None or rec.extra is None:
            return {"connected": False}
        return {
            "connected": True,
            "user_code": rec.extra.get("user_code", ""),
            "uid": rec.extra.get("uid", ""),
            "endpoint": rec.extra.get("endpoint", ""),
        }

    # ── Wizard: QR code generation ──────────────────────────────────────

    def start_qr_login(self, user_code: str) -> dict:
        """Step 1 of the wizard: ask Tuya for a QR token.

        Synchronous (tuya_sharing is a sync lib); call from a thread in the
        route handler via ``asyncio.to_thread``.

        Returns ``{qrcode_url, qr_token, user_code}``.
        """
        if self._login_control is None:
            from tuya_sharing import LoginControl
            self._login_control = LoginControl()
        try:
            resp = self._login_control.qr_code(
                TUYA_CLIENT_ID, TUYA_SCHEMA, user_code,
            )
        except Exception as exc:
            raise DriverError(f"Tuya qr_code() failed: {exc}") from exc
        if not isinstance(resp, dict) or not resp.get("success", False):
            msg = (resp or {}).get("msg", "unknown")
            code = (resp or {}).get("code", "?")
            raise DriverError(
                f"Tuya qr_code rejected (code={code}): {msg}. "
                "Check that your Smart Life user code is fresh (valid ~10 min)."
            )
        result = resp.get("result", {}) or {}
        # Tuya returns a single opaque string in result.qrcode — that SAME
        # string is both the payload for the QR image and the ``token``
        # parameter of the subsequent login_result() poll call.
        qrcode = result.get("qrcode", "")
        if not qrcode:
            raise DriverError(f"Tuya qr_code returned empty response: {result}")
        # The Smart Life app expects the QR to encode a deep-link URL:
        #   tuyaSmart--qrLogin?token=<qrcode>
        qr_url = f"tuyaSmart--qrLogin?token={qrcode}"
        self._pending_qr[user_code] = {
            "qr_token": qrcode,  # same string used for login_result()
            "qr_url": qr_url,
        }
        return {"qr_url": qr_url, "qr_token": qrcode, "user_code": user_code}

    # ── Wizard: poll for login completion ──────────────────────────────

    async def poll_login(self, user_code: str) -> dict:
        """Step 2: call ``LoginControl.login_result`` until the user scans.

        Loops up to 3 minutes. Returns ``{status, devices}`` on success;
        raises DriverError on failure or timeout.
        """
        pending = self._pending_qr.get(user_code)
        if pending is None:
            raise DriverError("No pending QR login — call /tuya/wizard/start first")

        qr_token = pending["qr_token"]

        if self._login_control is None:
            from tuya_sharing import LoginControl
            self._login_control = LoginControl()

        deadline = asyncio.get_event_loop().time() + 180.0  # 3 minutes
        while asyncio.get_event_loop().time() < deadline:
            def _poll():
                return self._login_control.login_result(
                    qr_token, TUYA_CLIENT_ID, user_code,
                )

            try:
                ok, info = await asyncio.to_thread(_poll)
            except Exception as exc:
                await asyncio.sleep(2.0)
                logger.debug("Tuya login_result transient error: %s", exc)
                continue

            if ok and isinstance(info, dict):
                # Successful login. Persist and build a Manager.
                terminal_id = str(uuid.uuid4())
                self._store_token(info, user_code, terminal_id)
                self._pending_qr.pop(user_code, None)
                # Build manager with the fresh token response
                await self._build_manager_from_info(info, user_code, terminal_id)
                devices = await self.list_devices()
                return {"status": "ok", "devices": devices}

            # Not done yet — Tuya returns ok=False while the user hasn't
            # confirmed. Wait and retry.
            await asyncio.sleep(2.0)

        # Timed out — user never scanned.
        self._pending_qr.pop(user_code, None)
        raise DriverError(
            "QR login timed out. The user did not scan within 3 minutes. "
            "Open Smart Life → + → Scan → point at the QR and tap Authorize."
        )

    # ── Manager construction ───────────────────────────────────────────

    async def _build_manager_from_info(
        self,
        info: dict,
        user_code: str,
        terminal_id: str,
    ) -> None:
        from tuya_sharing import Manager

        endpoint = info.get("endpoint", "")
        # Tuya sometimes returns the endpoint as "openapi.tuyaeu.com" without
        # scheme; Manager's CustomerApi concatenates it with paths so we need
        # to force the ``https://`` prefix.
        if endpoint and not endpoint.startswith(("http://", "https://")):
            endpoint = "https://" + endpoint
        token_response = {
            "access_token": info.get("access_token", ""),
            "refresh_token": info.get("refresh_token", ""),
            "expire_time": info.get("expire_time", 0),
            "uid": info.get("uid", ""),
        }

        listener = _TokenRefreshListener()

        def _build():
            return Manager(
                client_id=TUYA_CLIENT_ID,
                user_code=user_code,
                terminal_id=terminal_id,
                end_point=endpoint,
                token_response=token_response,
                listener=listener,
            )

        self._manager = await asyncio.to_thread(_build)
        # Store the main asyncio loop so the push-listener (which runs in a
        # tuya_sharing background thread) can schedule coroutines on it.
        self._main_loop = asyncio.get_running_loop()
        # Register a single global push listener that distributes events to
        # per-device queues.
        self._manager.add_device_listener(_PushFanout(self))
        await asyncio.to_thread(self._manager.update_device_cache)

    async def ensure_manager(self) -> None:
        """Lazy-build the Manager from stored creds if not yet built."""
        async with self._lock:
            if self._manager is not None:
                return
            stored = self._load_token()
            if stored is None:
                raise DriverError(
                    "Tuya Smart Life not connected. Run the wizard first."
                )
            # Pass endpoint alongside the token payload so _build_manager
            # can pick it up (the tuya_sharing login response carries it at
            # the top level, we stored it in a separate field).
            info = dict(stored["token_response"])
            info["endpoint"] = stored["endpoint"]
            await self._build_manager_from_info(
                info,
                stored["user_code"],
                stored["terminal_id"],
            )

    # ── Device listing / commands / push ───────────────────────────────

    async def list_devices(self) -> list[dict]:
        """Refresh and return a list of devices discovered in the Smart Life account."""
        await self.ensure_manager()
        await asyncio.to_thread(self._manager.update_device_cache)
        out: list[dict] = []
        for d in self._manager.device_map.values():
            out.append({
                "id": d.id,
                "name": getattr(d, "name", ""),
                "category": getattr(d, "category", ""),
                "product_name": getattr(d, "product_name", ""),
                "ip": getattr(d, "ip", ""),
                "local_key": getattr(d, "local_key", ""),
                "version": "3.3",  # tuya_sharing doesn't expose proto version
                "online": getattr(d, "online", False),
                "status": getattr(d, "status", {}),
            })
        return out

    async def get_device_state(self, cloud_id: str) -> dict:
        """Return current ``status`` dict for a specific device."""
        await self.ensure_manager()
        d = self._manager.device_map.get(cloud_id)
        if d is None:
            # Refresh cache and retry once
            await asyncio.to_thread(self._manager.update_device_cache)
            d = self._manager.device_map.get(cloud_id)
        if d is None:
            raise DriverError(f"Device {cloud_id} not found in Tuya account")
        return dict(getattr(d, "status", {}) or {})

    async def send_commands(self, cloud_id: str, commands: list[dict]) -> None:
        """Dispatch commands via Tuya cloud.

        ``commands`` is a list of ``{"code": "switch_1", "value": True}``.
        """
        await self.ensure_manager()

        def _send():
            return self._manager.send_commands(cloud_id, commands)

        try:
            await asyncio.to_thread(_send)
        except Exception as exc:
            raise DriverError(f"Tuya send_commands failed: {exc}") from exc

    def subscribe_push(self, cloud_id: str) -> "asyncio.Queue[dict]":
        """Return an asyncio queue that receives push state dicts for this device.

        Called by ``TuyaCloudDriver.stream_events``.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._device_listeners[cloud_id] = queue
        return queue

    def unsubscribe_push(self, cloud_id: str) -> None:
        self._device_listeners.pop(cloud_id, None)

    def _dispatch_push(self, device_id: str, status: dict) -> None:
        """Called (from a background thread) by _PushFanout when Tuya pushes a state update."""
        q = self._device_listeners.get(device_id)
        if q is None or self._main_loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(q.put(status), self._main_loop)
        except Exception:
            pass


# ── tuya_sharing listeners ────────────────────────────────────────────────


class _TokenRefreshListener:
    """Called by tuya_sharing Manager when it refreshes the access token.

    We simply re-write the vault so the new token survives restart.
    """

    def update_token(self, token_info: dict) -> None:
        try:
            from system_modules.secrets_vault.vault import SecretRecord, get_vault
            rec = get_vault().load(_VAULT_SERVICE)
            if rec is None or rec.extra is None:
                return
            rec.access_token = token_info.get("access_token", rec.access_token)
            rec.refresh_token = token_info.get("refresh_token", rec.refresh_token)
            expire = token_info.get("expire_time", 0) or 0
            rec.expires_at = float(expire) if expire else None
            rec.extra["expire_time"] = expire
            get_vault().store(rec)
        except Exception:
            pass


class _PushFanout:
    """tuya_sharing SharingDeviceListener — fans out events into per-device queues."""

    def __init__(self, client: "TuyaCloudClient") -> None:
        self._client = client

    def update_device(
        self,
        device: Any,
        updated_status_properties: list[str] | None = None,
        dp_timestamps: dict | None = None,
    ) -> None:
        try:
            self._client._dispatch_push(
                device.id, dict(getattr(device, "status", {}) or {}),
            )
        except Exception:
            pass

    def add_device(self, device: Any) -> None:
        # A new device was paired in the mobile app — push its initial state.
        try:
            self._client._dispatch_push(
                device.id, dict(getattr(device, "status", {}) or {}),
            )
        except Exception:
            pass

    def remove_device(self, device_id: str) -> None:
        # Device was unlinked from the Smart Life account — let the watcher loop
        # see a state change so the UI reflects it.
        pass


# ── Per-device driver ─────────────────────────────────────────────────────


class TuyaCloudDriver(DeviceDriver):
    protocol = "tuya_cloud"

    def __init__(self, device_id: str, meta: dict[str, Any]) -> None:
        super().__init__(device_id, meta)
        cfg = (meta or {}).get("tuya") or {}
        self._cloud_id: str = cfg.get("cloud_device_id") or cfg.get("device_id", "")
        # Logical key → Tuya status code (e.g. "on" → "switch_1")
        self._code_map: dict[str, str] = dict(cfg.get("code_map") or {"on": "switch_1"})
        self._reverse_codes: dict[str, str] = {v: k for k, v in self._code_map.items()}
        self._client = TuyaCloudClient.get()
        self._queue: asyncio.Queue[dict] | None = None

    def _raw_to_logical(self, raw: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if not raw:
            return out
        # tuya_sharing gives status as a dict keyed by string code
        for code, value in raw.items():
            logical = self._reverse_codes.get(str(code))
            if logical:
                out[logical] = value
        return out

    async def connect(self) -> dict[str, Any]:
        if not self._cloud_id:
            raise DriverError(f"Device {self.device_id}: missing cloud_device_id")
        raw = await self._client.get_device_state(self._cloud_id)
        self._queue = self._client.subscribe_push(self._cloud_id)
        return self._raw_to_logical(raw)

    async def disconnect(self) -> None:
        self._client.unsubscribe_push(self._cloud_id)
        self._queue = None

    async def set_state(self, state: dict[str, Any]) -> None:
        commands: list[dict] = []
        for k, v in state.items():
            code = self._code_map.get(k)
            if code is not None:
                commands.append({"code": code, "value": v})
        if not commands:
            return
        await self._client.send_commands(self._cloud_id, commands)

    async def get_state(self) -> dict[str, Any]:
        raw = await self._client.get_device_state(self._cloud_id)
        return self._raw_to_logical(raw)

    async def stream_events(self) -> AsyncGenerator[dict[str, Any], None]:
        if self._queue is None:
            self._queue = self._client.subscribe_push(self._cloud_id)
        while True:
            raw = await self._queue.get()
            logical = self._raw_to_logical(raw)
            if logical:
                yield logical
