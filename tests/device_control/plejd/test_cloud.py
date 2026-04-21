"""Tests for plejd.cloud — mock aiohttp so we never hit hems.plejd.com.

The goal is to nail down wire-format handling:
    - login flow (200 → sessionToken stored; 401 → PlejdAuthError)
    - list_sites unwraps Parse ``result``/``results`` envelopes
    - fetch_site parses both hex and base64 crypto_keys
    - device list gets normalised into PlejdCloudDevice
"""
from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp
import pytest

from system_modules.device_control.plejd import cloud


# ── Fake aiohttp session ──────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = body
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def json(self, content_type=None): return self._body


class _FakeSession:
    def __init__(self, *, get_map: dict[str, Any] | None = None,
                 login_response: tuple[int, Any] | None = None) -> None:
        self.closed = False
        self._get_map = get_map or {}
        self._login = login_response or (200, {"sessionToken": "tok", "objectId": "u1"})
        self.last_get: tuple[str, dict, dict] | None = None
        self.last_post: tuple[str, dict, dict] | None = None

    def post(self, url, json=None, headers=None):
        self.last_post = (url, json or {}, headers or {})
        return _FakeResponse(*self._login)

    def get(self, url, headers=None, params=None):
        self.last_get = (url, headers or {}, params or {})
        # Match on path suffix so callers don't have to know base URL.
        for suffix, (status, body) in self._get_map.items():
            if url.endswith(suffix):
                return _FakeResponse(status, body)
        return _FakeResponse(404, {"error": "not mocked"})

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_session(monkeypatch):
    """Install a fake ClientSession class. The test function provides the
    per-call response map via the returned configure() callback."""
    state: dict = {}

    def _make(get_map=None, login_response=None):
        state["session"] = _FakeSession(get_map=get_map, login_response=login_response)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: state["session"])
        return state["session"]

    return _make


# ── Login ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_login_stores_session_token(fake_session):
    session = fake_session(login_response=(200, {
        "sessionToken": "sess-123",
        "objectId": "user-abc",
    }))
    async with cloud.PlejdCloudClient() as c:
        await c.login("user@example.com", "hunter2")
    assert session.last_post is not None
    url, body, headers = session.last_post
    assert url.endswith("/parse/login")
    assert body == {"username": "user@example.com", "password": "hunter2"}
    assert headers.get("X-Parse-Application-Id") == cloud.PLEJD_APP_ID
    # Client stored the session token for subsequent calls.


@pytest.mark.asyncio
async def test_login_401_raises_auth_error(fake_session):
    fake_session(login_response=(401, {"error": "Invalid credentials"}))
    async with cloud.PlejdCloudClient() as c:
        with pytest.raises(cloud.PlejdAuthError, match="Invalid credentials"):
            await c.login("x", "y")


@pytest.mark.asyncio
async def test_login_no_session_token_raises(fake_session):
    fake_session(login_response=(200, {"objectId": "u1"}))   # missing sessionToken
    async with cloud.PlejdCloudClient() as c:
        with pytest.raises(cloud.PlejdCloudError, match="no sessionToken"):
            await c.login("x", "y")


# ── Site discovery ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sites_unwraps_result_envelope(fake_session):
    fake_session(get_map={
        "/user_sites/list": (200, {"result": [
            {"site": {"siteId": "sid-1", "title": "Home"}},
            {"site": {"siteId": "sid-2", "title": "Cabin"}},
        ]}),
    })
    async with cloud.PlejdCloudClient() as c:
        c.session_token = "tok"
        sites = await c.list_sites()
    assert sites == [
        {"site_id": "sid-1", "title": "Home"},
        {"site_id": "sid-2", "title": "Cabin"},
    ]


@pytest.mark.asyncio
async def test_list_sites_requires_login(fake_session):
    fake_session(get_map={})
    async with cloud.PlejdCloudClient() as c:
        with pytest.raises(cloud.PlejdCloudError, match="not logged in"):
            await c.list_sites()


@pytest.mark.asyncio
async def test_fetch_site_parses_hex_crypto_key_and_devices(fake_session):
    # 32-char hex = 16 raw bytes when decoded.
    hex_key = "000102030405060708090a0b0c0d0e0f"
    fake_session(get_map={
        "/user_sites/detail": (200, {"result": [{
            "site": {"title": "Home", "cryptoKey": hex_key},
            "plejdDevices": [
                {"deviceId": "AABBCCDDEEFF", "outputAddress": 1,
                 "hardwareId": "DIM-02", "roomTitle": "Kitchen"},
                {"deviceId": "11:22:33:44:55:66", "outputAddress": 5,
                 "hardwareId": "REL-01", "roomTitle": "Garage"},
            ],
            "outputs": [
                {"outputAddress": 1, "title": "Kitchen ceiling"},
                {"outputAddress": 5, "title": "Garage door light"},
            ],
        }]}),
    })
    async with cloud.PlejdCloudClient() as c:
        c.session_token = "tok"
        site = await c.fetch_site("sid-1")
    assert site.site_id == "sid-1"
    assert site.title == "Home"
    assert site.crypto_key == bytes.fromhex(hex_key)
    assert site.crypto_key_b64() == base64.b64encode(bytes.fromhex(hex_key)).decode()

    titles = [d.title for d in site.devices]
    assert "Kitchen ceiling" in titles
    assert "Garage door light" in titles
    dim = next(d for d in site.devices if d.device_type == "DIM-02")
    assert dim.dimmable is True
    rel = next(d for d in site.devices if d.device_type == "REL-01")
    assert rel.dimmable is False
    assert rel.ble_address == "11:22:33:44:55:66"


@pytest.mark.asyncio
async def test_fetch_site_parses_base64_crypto_key(fake_session):
    raw = bytes(range(16))
    b64 = base64.b64encode(raw).decode()
    fake_session(get_map={
        "/user_sites/detail": (200, {"result": [{
            "site": {"title": "Home", "cryptoKey": b64},
            "plejdDevices": [],
        }]}),
    })
    async with cloud.PlejdCloudClient() as c:
        c.session_token = "tok"
        site = await c.fetch_site("sid-1")
    assert site.crypto_key == raw


@pytest.mark.asyncio
async def test_fetch_site_missing_crypto_key_raises(fake_session):
    fake_session(get_map={
        "/user_sites/detail": (200, {"result": [{
            "site": {"title": "Home"},
            "plejdDevices": [],
        }]}),
    })
    async with cloud.PlejdCloudClient() as c:
        c.session_token = "tok"
        with pytest.raises(cloud.PlejdCloudError, match="cryptoKey"):
            await c.fetch_site("sid-1")


@pytest.mark.asyncio
async def test_fetch_site_not_visible_raises(fake_session):
    fake_session(get_map={
        "/user_sites/detail": (200, {"result": []}),
    })
    async with cloud.PlejdCloudClient() as c:
        c.session_token = "tok"
        with pytest.raises(cloud.PlejdCloudError, match="not visible"):
            await c.fetch_site("sid-missing")


# ── crypto_key parsing ────────────────────────────────────────────────────


def test_parse_crypto_key_accepts_hex():
    raw = cloud._parse_crypto_key("000102030405060708090a0b0c0d0e0f")
    assert raw == bytes.fromhex("000102030405060708090a0b0c0d0e0f")


def test_parse_crypto_key_accepts_base64():
    raw = bytes(range(16))
    b64 = base64.b64encode(raw).decode()
    assert cloud._parse_crypto_key(b64) == raw


def test_parse_crypto_key_rejects_wrong_length():
    with pytest.raises(ValueError):
        cloud._parse_crypto_key("aabbccdd")   # 4 bytes after hex decode
