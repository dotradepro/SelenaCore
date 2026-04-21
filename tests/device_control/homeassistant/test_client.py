"""Unit tests for the HA WebSocket client.

Mocks aiohttp's ws_connect so no real network IO happens. The fake
WebSocket accepts a scripted sequence of server frames and records every
frame the client sends, letting us assert on the exact HA wire protocol
(auth_required → auth → auth_ok, then id-scoped result frames).
"""
from __future__ import annotations

import json
from typing import Any

import aiohttp
import pytest

from system_modules.device_control.importers.homeassistant.client import (
    HAAuthError,
    HAClient,
    HAProtocolError,
    HAScopeError,
    normalise_ws_url,
)


# ── Scripted fake WebSocket ───────────────────────────────────────────────


class _FakeMessage:
    def __init__(self, data: str, mtype: aiohttp.WSMsgType = aiohttp.WSMsgType.TEXT) -> None:
        self.type = mtype
        self.data = data


class _FakeWS:
    def __init__(self, scripted: list[dict | _FakeMessage]) -> None:
        self._script = list(scripted)
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def receive(self) -> _FakeMessage:
        if not self._script:
            return _FakeMessage("", aiohttp.WSMsgType.CLOSED)
        nxt = self._script.pop(0)
        if isinstance(nxt, _FakeMessage):
            return nxt
        return _FakeMessage(json.dumps(nxt))

    async def close(self) -> None:
        self.closed = True

    def exception(self) -> BaseException | None:  # pragma: no cover - only on error path
        return None


class _FakeSession:
    def __init__(self, ws: _FakeWS) -> None:
        self._ws = ws
        self.closed = False

    async def ws_connect(self, url: str, **_kwargs) -> _FakeWS:
        return self._ws

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def patch_aiohttp(monkeypatch):
    """Factory: build a client wired to a scripted fake WS."""
    def _make(script: list[dict | _FakeMessage]) -> tuple[HAClient, _FakeWS]:
        ws = _FakeWS(script)
        session = _FakeSession(ws)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: session)
        client = HAClient("ws://fake/api/websocket", "tok123")
        return client, ws
    return _make


# ── Auth handshake ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_handshake_happy_path(patch_aiohttp):
    client, ws = patch_aiohttp([
        {"type": "auth_required", "ha_version": "2025.2.0"},
        {"type": "auth_ok"},
    ])
    async with client as c:
        assert c.ha_version == "2025.2.0"
    # Client should have emitted exactly one auth frame with the token.
    assert ws.sent == [{"type": "auth", "access_token": "tok123"}]


@pytest.mark.asyncio
async def test_auth_rejected_raises(patch_aiohttp):
    client, _ = patch_aiohttp([
        {"type": "auth_required", "ha_version": "2025.2.0"},
        {"type": "auth_invalid", "message": "Invalid access token"},
    ])
    with pytest.raises(HAAuthError, match="Invalid access token"):
        async with client:
            pass


@pytest.mark.asyncio
async def test_auth_unexpected_first_frame(patch_aiohttp):
    client, _ = patch_aiohttp([
        {"type": "result", "id": 0, "success": True},
    ])
    with pytest.raises(HAProtocolError, match="auth_required"):
        async with client:
            pass


# ── Command/response ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_command_returns_result_payload(patch_aiohttp):
    client, ws = patch_aiohttp([
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {"id": 1, "type": "result", "success": True, "result": [{"id": "area1"}]},
    ])
    async with client as c:
        out = await c.send_command("config/area_registry/list")
    assert out == [{"id": "area1"}]
    # Verify the frame we sent matched HA's expected shape.
    cmd_frame = ws.sent[1]
    assert cmd_frame["id"] == 1
    assert cmd_frame["type"] == "config/area_registry/list"


@pytest.mark.asyncio
async def test_send_command_error_surfaces_as_protocol_error(patch_aiohttp):
    client, _ = patch_aiohttp([
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {
            "id": 1, "type": "result", "success": False,
            "error": {"code": "not_found", "message": "no such registry"},
        },
    ])
    async with client as c:
        with pytest.raises(HAProtocolError, match="not_found"):
            await c.send_command("config/area_registry/list")


@pytest.mark.asyncio
async def test_send_command_timeout_raises_scope_error(patch_aiohttp):
    """HA silently dropping admin commands (e.g. non-admin session under
    trusted_networks) must surface as HAScopeError with a clear message —
    not a generic TimeoutError the UI shows as ``connect_failed``."""
    import asyncio
    # Script auth frames + a message that never arrives (await blocks
    # forever so wait_for trips its own timeout).
    class _BlockForever:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
    client, ws = patch_aiohttp([
        {"type": "auth_required"},
        {"type": "auth_ok"},
    ])
    async with client as c:
        # Override receive() to block forever so the timeout is exercised.
        async def _block_forever():
            await asyncio.sleep(3600)
        ws.receive = _block_forever   # type: ignore[assignment]
        c._timeout = 0.05
        with pytest.raises(HAScopeError, match="admin"):
            await c.send_command("config/device_registry/list")


@pytest.mark.asyncio
async def test_send_command_mismatched_id_is_protocol_error(patch_aiohttp):
    client, _ = patch_aiohttp([
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {"id": 99, "type": "result", "success": True, "result": None},
    ])
    async with client as c:
        with pytest.raises(HAProtocolError, match="expected 1"):
            await c.send_command("config/area_registry/list")


@pytest.mark.asyncio
async def test_successive_commands_increment_id(patch_aiohttp):
    client, ws = patch_aiohttp([
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {"id": 1, "type": "result", "success": True, "result": "a"},
        {"id": 2, "type": "result", "success": True, "result": "b"},
    ])
    async with client as c:
        a = await c.send_command("x")
        b = await c.send_command("y")
    assert (a, b) == ("a", "b")
    assert [f["id"] for f in ws.sent[1:]] == [1, 2]


# ── URL normaliser ────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("homeassistant.local",            "ws://homeassistant.local:8123/api/websocket"),
    ("http://ha:8123",                 "ws://ha:8123/api/websocket"),
    ("https://ha.example.com",         "wss://ha.example.com/api/websocket"),
    ("ws://192.168.1.42:8123",         "ws://192.168.1.42:8123/api/websocket"),
    ("https://ha.example.com/",        "wss://ha.example.com/api/websocket"),
    ("ws://ha:8123/api/websocket",     "ws://ha:8123/api/websocket"),
])
def test_normalise_ws_url(raw, expected):
    assert normalise_ws_url(raw) == expected
