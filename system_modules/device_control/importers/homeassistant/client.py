"""Home Assistant WebSocket client (auth + command/response).

Scope is deliberately narrow: authenticate with a Long-Lived Access Token
and issue a handful of read-only ``config/*/list`` commands. Nothing in
this module mutates HA state.

Wire protocol reference:
    https://developers.home-assistant.io/docs/api/websocket/
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0   # seconds — per command; HA is local LAN so this is generous


class HAAuthError(RuntimeError):
    """Raised when HA rejects the LLAT or the handshake protocol drifts."""


class HAProtocolError(RuntimeError):
    """HA sent a response we can't interpret (message out of order, etc.)."""


class HAScopeError(RuntimeError):
    """Raised when a command times out in a way that almost always means
    the session lacks admin scope.

    Real HA returns a prompt ``{"success": false, "error": ...}`` when a
    command isn't permitted; some HA configurations (notably
    ``trusted_networks`` with a non-admin default user) silently drop
    admin commands instead. ``send_command`` translates the timeout into
    this specific error so the UI can say "your token isn't admin"
    instead of "HA unreachable".
    """


class HAClient:
    """Minimal HA WebSocket client.

    Use as an async context manager; the context owns the aiohttp session
    and WS connection and tears them down on exit regardless of outcome.
    Each ``send_command()`` call allocates a unique ``id`` and awaits the
    matching ``{type: "result"}`` frame — concurrent calls are serialised
    by a lock so response demultiplexing stays trivial.
    """

    def __init__(self, url: str, access_token: str, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._url = url
        self._token = access_token
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._msg_id = 0
        self._send_lock = asyncio.Lock()
        self.ha_version: str | None = None

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def __aenter__(self) -> "HAClient":
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self._url, heartbeat=20)
        except Exception:
            await self._session.close()
            self._session = None
            raise
        await self._authenticate()
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None

    # ── Auth ─────────────────────────────────────────────────────────────

    async def _authenticate(self) -> None:
        """Follow HA's 3-frame auth handshake.

        Frames we expect (in order):
            server → {"type": "auth_required", "ha_version": "..."}
            client → {"type": "auth", "access_token": "<LLAT>"}
            server → {"type": "auth_ok"}  or  {"type": "auth_invalid"}
        """
        assert self._ws is not None
        first = await self._recv_json()
        if first.get("type") != "auth_required":
            raise HAProtocolError(
                f"Expected auth_required, got {first.get('type')!r}",
            )
        self.ha_version = first.get("ha_version")
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        reply = await self._recv_json()
        rtype = reply.get("type")
        if rtype == "auth_invalid":
            raise HAAuthError(reply.get("message") or "auth_invalid")
        if rtype != "auth_ok":
            raise HAProtocolError(f"Unexpected auth reply: {rtype!r}")

    # ── Commands ─────────────────────────────────────────────────────────

    async def send_command(self, message_type: str, **payload: Any) -> Any:
        """Send a typed command and return the server's ``result`` field.

        Raises ``HAProtocolError`` if HA responds with ``success=False``.
        """
        async with self._send_lock:
            assert self._ws is not None, "client must be entered first"
            self._msg_id += 1
            msg_id = self._msg_id
            frame = {"id": msg_id, "type": message_type, **payload}
            await self._ws.send_json(frame)
            try:
                reply = await asyncio.wait_for(self._recv_json(), timeout=self._timeout)
            except asyncio.TimeoutError as exc:
                raise HAScopeError(
                    f"HA accepted the connection but never responded to "
                    f"{message_type!r}. This usually means your access "
                    f"token isn't tied to an admin account — only admin "
                    f"tokens can read the device/entity registries. "
                    f"Create a new Long-Lived Access Token from an admin "
                    f"user's profile page in Home Assistant.",
                ) from exc
            if reply.get("id") != msg_id:
                raise HAProtocolError(
                    f"Got reply for id={reply.get('id')}, expected {msg_id}",
                )
            if reply.get("type") != "result":
                raise HAProtocolError(
                    f"Expected result frame, got {reply.get('type')!r}",
                )
            if not reply.get("success", False):
                err = reply.get("error") or {}
                raise HAProtocolError(
                    f"HA command {message_type!r} failed: "
                    f"{err.get('code')} — {err.get('message')}",
                )
            return reply.get("result")

    # ── Internals ────────────────────────────────────────────────────────

    async def _recv_json(self) -> dict[str, Any]:
        assert self._ws is not None
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            try:
                return json.loads(msg.data)
            except json.JSONDecodeError as exc:
                raise HAProtocolError(f"Malformed JSON from HA: {exc}") from exc
        if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
            raise HAProtocolError("HA closed the WebSocket")
        if msg.type == aiohttp.WSMsgType.ERROR:
            raise HAProtocolError(f"HA WebSocket error: {self._ws.exception()}")
        raise HAProtocolError(f"Unexpected WS frame type: {msg.type!r}")


def normalise_ws_url(host: str) -> str:
    """Turn user-supplied host string into a full WebSocket URL.

    Accepts any of:
        "homeassistant.local"
        "http://homeassistant.local:8123"
        "https://ha.example.com"
        "ws://192.168.1.42:8123/api/websocket"
    """
    raw = host.strip().rstrip("/")
    if raw.startswith("ws://") or raw.startswith("wss://"):
        base = raw
    elif raw.startswith("http://"):
        base = "ws://" + raw[len("http://"):]
    elif raw.startswith("https://"):
        base = "wss://" + raw[len("https://"):]
    else:
        base = "ws://" + raw + ":8123"
    if not base.endswith("/api/websocket"):
        base = base + "/api/websocket"
    return base
