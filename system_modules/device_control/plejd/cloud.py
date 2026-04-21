"""One-time client for ``hems.plejd.com`` (Plejd's Parse Server backend).

The cloud is used *only* to discover sites and fetch the AES-128 site
key + device topology. Every runtime command after that flows over BLE.
After the initial import the user can block outbound internet to
hems.plejd.com without losing control.

Protocol: standard Parse REST API.

    POST /parse/login             — {"username", "password"} → sessionToken
    GET  /parse/classes/Site       — list sites owned by the account
    GET  /parse/classes/Site/:id   — site detail (devices, crypto_key)

All three endpoints require ``X-Parse-Application-Id`` set to the
public Plejd iOS app identifier (same one every community integration
uses). Authenticated requests additionally carry ``X-Parse-Session-Token``.

The module exposes ``PlejdCloudClient`` as an async context manager.
Credentials and session token live only in-memory for the wizard
session; the encrypted *site key* is the only thing persisted (via
``secrets_vault``) and that happens outside this module.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

#: Public identifier used by the official Plejd mobile app; every
#: community integration reuses it. Safe to hardcode.
PLEJD_APP_ID = "zHtVqXt8k4yFyk2QGmgp48D9rasQXskp"
PLEJD_BASE_URL = "https://cloud.plejd.com/parse"

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


class PlejdCloudError(RuntimeError):
    """Surfaced to the UI for any hems.plejd.com failure."""


class PlejdAuthError(PlejdCloudError):
    """Login was refused — credentials wrong or account locked."""


@dataclass
class PlejdCloudDevice:
    """One controllable output inside a Plejd site."""
    ble_address: str            # "AA:BB:CC:DD:EE:FF"
    output_address: int         # mesh-level 1-byte id used in commands
    title: str                  # user-visible name from the Plejd app
    device_type: str            # "DIM-02", "LED-10", "REL-01", ...
    room: str | None = None     # user-visible room name, may be None
    dimmable: bool = True       # False for pure on/off relays


@dataclass
class PlejdSite:
    """A single Plejd installation. ``crypto_key`` is what the BLE
    gateway needs to encrypt/decrypt frames; everything else is UI
    metadata."""
    site_id: str
    title: str
    crypto_key: bytes
    devices: list[PlejdCloudDevice] = field(default_factory=list)

    def crypto_key_b64(self) -> str:
        """Encoded for storage in secrets_vault.access_token (which
        accepts strings, not raw bytes)."""
        return base64.b64encode(self.crypto_key).decode("ascii")


# ── Cloud client ─────────────────────────────────────────────────────────


class PlejdCloudClient:
    """Async context manager for a Plejd cloud session.

    Single-session — one login, one account. Callers wanting to switch
    accounts instantiate a fresh client. No caching across instances so
    credentials never survive the wizard tab.
    """

    def __init__(self, *, timeout: aiohttp.ClientTimeout = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self.session_token: str | None = None
        self.user_id: str | None = None

    async def __aenter__(self) -> "PlejdCloudClient":
        self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ── Auth ─────────────────────────────────────────────────────────

    def _base_headers(self) -> dict[str, str]:
        h = {
            "X-Parse-Application-Id": PLEJD_APP_ID,
            "Content-Type": "application/json",
        }
        if self.session_token:
            h["X-Parse-Session-Token"] = self.session_token
        return h

    async def login(self, username: str, password: str) -> None:
        """Exchange (username, password) for a session token."""
        assert self._session is not None, "enter the context manager first"
        url = f"{PLEJD_BASE_URL}/login"
        try:
            async with self._session.post(
                url, json={"username": username, "password": password},
                headers=self._base_headers(),
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status == 404 or resp.status == 401:
                    raise PlejdAuthError(
                        body.get("error")
                        or "Plejd cloud refused the credentials.",
                    )
                if resp.status >= 400:
                    raise PlejdCloudError(
                        f"login failed ({resp.status}): {body}",
                    )
        except aiohttp.ClientError as exc:
            raise PlejdCloudError(f"login network error: {exc}") from exc

        token = body.get("sessionToken")
        if not token:
            raise PlejdCloudError(f"login ok but no sessionToken in: {body}")
        self.session_token = token
        self.user_id = body.get("objectId")

    # ── Site discovery ───────────────────────────────────────────────

    async def list_sites(self) -> list[dict[str, Any]]:
        """Return the sites tied to the logged-in account.

        Each entry: ``{"site_id", "title"}``. Device detail / crypto_key
        come from ``fetch_site()``.
        """
        rows = await self._get("/user_sites/list")
        out: list[dict[str, Any]] = []
        for r in rows or []:
            # The user_sites endpoint nests site metadata under "site".
            s = r.get("site") or r
            site_id = s.get("siteId") or s.get("objectId")
            title = s.get("title") or s.get("siteName") or ""
            if site_id:
                out.append({"site_id": str(site_id), "title": str(title)})
        return out

    async def fetch_site(self, site_id: str) -> PlejdSite:
        """Fetch the full site detail (devices + crypto_key)."""
        rows = await self._get("/user_sites/detail", params={"siteId": site_id})
        if not rows:
            raise PlejdCloudError(f"site {site_id!r} not visible to this account")
        raw = rows[0]
        site_obj = raw.get("site") or {}
        title = site_obj.get("title") or site_obj.get("siteName") or site_id

        key_b64 = (
            site_obj.get("cryptoKey")
            or raw.get("cryptoKey")
            or ""
        )
        if not key_b64:
            raise PlejdCloudError(
                f"site {title!r} has no cryptoKey — your account may lack admin rights",
            )
        try:
            crypto_key = _parse_crypto_key(key_b64)
        except ValueError as exc:
            raise PlejdCloudError(f"invalid cryptoKey: {exc}") from exc

        devices = _parse_devices(raw)
        return PlejdSite(
            site_id=site_id,
            title=title,
            crypto_key=crypto_key,
            devices=devices,
        )

    # ── Internals ────────────────────────────────────────────────────

    async def _get(self, path: str, *, params: dict[str, Any] | None = None) -> list[Any]:
        """GET ``path`` and return the ``results`` array (or [] on empty)."""
        if self.session_token is None:
            raise PlejdCloudError("not logged in — call login() first")
        assert self._session is not None
        url = f"{PLEJD_BASE_URL}{path}"
        try:
            async with self._session.get(
                url, headers=self._base_headers(), params=params or {},
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status == 401:
                    raise PlejdAuthError("session expired — re-login")
                if resp.status >= 400:
                    raise PlejdCloudError(
                        f"{path} failed ({resp.status}): {body}",
                    )
        except aiohttp.ClientError as exc:
            raise PlejdCloudError(f"{path} network error: {exc}") from exc
        # Parse API wraps lists in {"result": [...]} for query endpoints.
        if isinstance(body, dict):
            return body.get("result") or body.get("results") or []
        return body if isinstance(body, list) else []


# ── Parsing helpers (pure) ────────────────────────────────────────────────


def _parse_crypto_key(raw: str) -> bytes:
    """Accept either hex-32 or base64-encoded 16-byte site keys."""
    if len(raw) == 32 and all(c in "0123456789abcdefABCDEF" for c in raw):
        out = bytes.fromhex(raw)
    else:
        try:
            out = base64.b64decode(raw, validate=False)
        except Exception as exc:
            raise ValueError(f"bad encoding: {exc}") from exc
    if len(out) != 16:
        raise ValueError(f"expected 16 bytes, got {len(out)}")
    return out


def _parse_devices(raw: dict[str, Any]) -> list[PlejdCloudDevice]:
    """Extract devices from a site-detail response.

    Plejd's schema has moved around between API versions — we look in
    three plausible locations for device lists and pick the first
    populated one. Fields get normalised into ``PlejdCloudDevice``.
    """
    sources = (
        raw.get("plejdDevices"),
        raw.get("devices"),
        (raw.get("site") or {}).get("plejdDevices"),
    )
    pairs_raw = next((s for s in sources if isinstance(s, list) and s), [])

    # Plejd keeps "titles" (friendly names) in a parallel list keyed by
    # output_address; look for the typical field names.
    titles_raw = raw.get("outputs") or raw.get("deviceAddress") or []
    titles_by_addr: dict[int, str] = {}
    if isinstance(titles_raw, list):
        for t in titles_raw:
            if not isinstance(t, dict):
                continue
            addr = _coerce_int(t.get("outputAddress") or t.get("deviceAddress"))
            name = t.get("title") or t.get("deviceName") or ""
            if addr is not None and name:
                titles_by_addr[addr] = str(name)

    out: list[PlejdCloudDevice] = []
    for d in pairs_raw:
        if not isinstance(d, dict):
            continue
        mac = str(d.get("deviceId") or d.get("ble_address") or d.get("mac") or "").strip()
        if not mac:
            continue
        # Plejd stores BLE addresses without colons.
        if ":" not in mac and len(mac) == 12:
            mac = ":".join(mac[i:i+2] for i in range(0, 12, 2)).upper()
        output_addr = _coerce_int(d.get("outputAddress") or d.get("output_address"))
        if output_addr is None:
            continue
        device_type = str(d.get("hardwareId") or d.get("deviceType") or "") or "UNKNOWN"
        title = titles_by_addr.get(output_addr) or str(d.get("title") or "")
        room = d.get("roomTitle") or d.get("room") or None
        dimmable = bool(d.get("dimmable", "DIM" in device_type.upper()))
        out.append(PlejdCloudDevice(
            ble_address=mac.upper(),
            output_address=output_addr,
            title=title or f"{device_type} {output_addr}",
            device_type=device_type,
            room=room,
            dimmable=dimmable,
        ))
    return out


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
