"""tests/test_presence_detection.py — pytest tests for presence_detection module"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_detector(publish=None, scan_interval=60, away_threshold=180):
    from system_modules.presence_detection.presence import PresenceDetector
    return PresenceDetector(
        publish_event_cb=publish or AsyncMock(),
        scan_interval_sec=scan_interval,
        away_threshold_sec=away_threshold,
    )


ALICE = {
    "user_id": "user-alice",
    "name": "Alice",
    "devices": [
        {"type": "ip", "address": "192.168.1.101"},
        {"type": "mac", "address": "aa:bb:cc:dd:ee:ff"},
    ],
}

BOB = {
    "user_id": "user-bob",
    "name": "Bob",
    "devices": [
        {"type": "ip", "address": "192.168.1.102"},
    ],
}


# ── ping_ip helper ────────────────────────────────────────────────────────────

class TestPingIp:
    @pytest.mark.asyncio
    async def test_ping_uses_tcp_fallback_when_no_icmplib(self):
        from system_modules.presence_detection import presence as p_mod
        p_mod.ICMPLIB_AVAILABLE = False

        with patch.object(p_mod, "_tcp_ping", new=AsyncMock(return_value=True)):
            result = await p_mod.ping_ip("192.168.1.1")

        assert result is True
        p_mod.ICMPLIB_AVAILABLE = False  # restore

    @pytest.mark.asyncio
    async def test_ping_returns_false_on_timeout(self):
        from system_modules.presence_detection import presence as p_mod
        p_mod.ICMPLIB_AVAILABLE = False

        with patch.object(p_mod, "_tcp_ping", new=AsyncMock(return_value=False)):
            result = await p_mod.ping_ip("192.168.1.254")

        assert result is False


# ── mac_in_arp_table ─────────────────────────────────────────────────────────

class TestMacInArpTable:
    def test_mac_found(self):
        from system_modules.presence_detection import presence as p_mod

        arp_data = {"192.168.1.1": "aa:bb:cc:dd:ee:ff", "192.168.1.2": "11:22:33:44:55:66"}
        with patch.object(p_mod, "_read_arp_table", return_value=arp_data):
            assert p_mod.mac_in_arp_table("AA:BB:CC:DD:EE:FF") is True

    def test_mac_not_found(self):
        from system_modules.presence_detection import presence as p_mod

        with patch.object(p_mod, "_read_arp_table", return_value={}):
            assert p_mod.mac_in_arp_table("aa:bb:cc:dd:ee:ff") is False

    def test_mac_case_insensitive(self):
        from system_modules.presence_detection import presence as p_mod

        arp_data = {"192.168.1.5": "AA:BB:CC:DD:EE:FF".lower()}
        with patch.object(p_mod, "_read_arp_table", return_value=arp_data):
            assert p_mod.mac_in_arp_table("AA:BB:CC:DD:EE:FF") is True


# ── User management ───────────────────────────────────────────────────────────

class TestUserManagement:
    def test_add_user(self):
        d = make_detector()
        user = d.add_user(ALICE.copy())
        assert user["user_id"] == "user-alice"
        assert user["state"] == "unknown"
        assert user["confidence"] == 0.0

    def test_add_user_duplicate_updates(self):
        d = make_detector()
        d.add_user(ALICE.copy())
        updated = {**ALICE, "name": "Alice Smith"}
        d.add_user(updated)
        assert d.get_user("user-alice")["name"] == "Alice Smith"

    def test_get_user_not_found(self):
        d = make_detector()
        assert d.get_user("nonexistent") is None

    def test_remove_user(self):
        d = make_detector()
        d.add_user(ALICE.copy())
        result = d.remove_user("user-alice")
        assert result is True
        assert d.get_user("user-alice") is None

    def test_remove_user_not_found(self):
        d = make_detector()
        assert d.remove_user("no-such-user") is False

    def test_list_users_empty(self):
        d = make_detector()
        assert d.list_users() == []

    def test_list_users(self):
        d = make_detector()
        d.add_user(ALICE.copy())
        d.add_user(BOB.copy())
        users = d.list_users()
        assert len(users) == 2


# ── Detection logic ───────────────────────────────────────────────────────────

class TestDetectionLogic:
    @pytest.mark.asyncio
    async def test_check_device_ip_reachable(self):
        from system_modules.presence_detection import presence as p_mod
        d = make_detector()

        with patch.object(p_mod, "ping_ip", new=AsyncMock(return_value=True)):
            result = await d._check_device({"type": "ip", "address": "192.168.1.1"})

        assert result is True

    @pytest.mark.asyncio
    async def test_check_device_ip_unreachable(self):
        from system_modules.presence_detection import presence as p_mod
        d = make_detector()

        with patch.object(p_mod, "ping_ip", new=AsyncMock(return_value=False)):
            result = await d._check_device({"type": "ip", "address": "192.168.1.99"})

        assert result is False

    @pytest.mark.asyncio
    async def test_check_device_mac(self):
        from system_modules.presence_detection import presence as p_mod
        d = make_detector()

        with patch.object(p_mod, "mac_in_arp_table", return_value=True):
            result = await d._check_device({"type": "mac", "address": "aa:bb:cc:dd:ee:ff"})

        assert result is True

    @pytest.mark.asyncio
    async def test_check_device_empty_address(self):
        d = make_detector()
        result = await d._check_device({"type": "ip", "address": ""})
        assert result is False

    @pytest.mark.asyncio
    async def test_check_device_unknown_type(self):
        d = make_detector()
        result = await d._check_device({"type": "gps", "address": "123,456"})
        assert result is False

    @pytest.mark.asyncio
    async def test_detect_user_any_device_reachable(self):
        from system_modules.presence_detection import presence as p_mod
        d = make_detector()

        # First device fails, second succeeds
        call_count = 0
        async def mock_ping(addr, **kw):
            nonlocal call_count
            call_count += 1
            return call_count > 1  # first call False, second True

        user = {
            **ALICE,
            "devices": [
                {"type": "ip", "address": "192.168.1.1"},
                {"type": "ip", "address": "192.168.1.2"},
            ],
        }
        with patch.object(p_mod, "ping_ip", side_effect=mock_ping):
            result = await d._detect_user(user)

        assert result is True

    @pytest.mark.asyncio
    async def test_detect_user_no_devices(self):
        d = make_detector()
        user = {**ALICE, "devices": []}
        result = await d._detect_user(user)
        assert result is False


# ── Scan / state transitions ──────────────────────────────────────────────────

class TestStateMachine:
    @pytest.mark.asyncio
    async def test_user_transitions_to_home(self):
        publish = AsyncMock()
        d = make_detector(publish=publish)
        d.add_user(ALICE.copy())

        with patch.object(d, "_detect_user", new=AsyncMock(return_value=True)):
            await d._scan_all()

        user = d.get_user("user-alice")
        assert user["state"] == "home"
        # presence.home event should be published
        event_types = [call[0][0] for call in publish.call_args_list]
        assert "presence.home" in event_types

    @pytest.mark.asyncio
    async def test_user_transitions_to_away_after_threshold(self):
        publish = AsyncMock()
        d = make_detector(publish=publish, away_threshold=60)
        d.add_user(ALICE.copy())

        # Simulate user was home
        user = d.get_user("user-alice")
        user["state"] = "home"
        past_time = (datetime.now(tz=timezone.utc) - timedelta(seconds=120)).isoformat()
        user["last_seen"] = past_time

        with patch.object(d, "_detect_user", new=AsyncMock(return_value=False)):
            await d._scan_all()

        assert d.get_user("user-alice")["state"] == "away"
        event_types = [call[0][0] for call in publish.call_args_list]
        assert "presence.away" in event_types

    @pytest.mark.asyncio
    async def test_user_not_away_too_soon(self):
        """User should not go away if threshold not reached."""
        publish = AsyncMock()
        d = make_detector(publish=publish, away_threshold=300)
        d.add_user(ALICE.copy())

        user = d.get_user("user-alice")
        user["state"] = "home"
        # Just seen 30 seconds ago (below threshold)
        user["last_seen"] = (datetime.now(tz=timezone.utc) - timedelta(seconds=30)).isoformat()

        with patch.object(d, "_detect_user", new=AsyncMock(return_value=False)):
            await d._scan_all()

        assert d.get_user("user-alice")["state"] == "home"  # still home

    @pytest.mark.asyncio
    async def test_scan_publishes_scan_event(self):
        publish = AsyncMock()
        d = make_detector(publish=publish)
        d.add_user(ALICE.copy())

        with patch.object(d, "_detect_user", new=AsyncMock(return_value=True)):
            await d._scan_all()

        event_types = [call[0][0] for call in publish.call_args_list]
        assert "presence.scan" in event_types

    @pytest.mark.asyncio
    async def test_home_not_fired_if_already_home(self):
        """presence.home should not be re-published if user was already home."""
        publish = AsyncMock()
        d = make_detector(publish=publish)
        d.add_user(ALICE.copy())
        user = d.get_user("user-alice")
        user["state"] = "home"
        user["last_seen"] = datetime.now(tz=timezone.utc).isoformat()

        publish.reset_mock()

        with patch.object(d, "_detect_user", new=AsyncMock(return_value=True)):
            await d._scan_all()

        event_types = [call[0][0] for call in publish.call_args_list]
        assert "presence.home" not in event_types


# ── Status ────────────────────────────────────────────────────────────────────

class TestDetectorStatus:
    def test_status_empty(self):
        d = make_detector()
        s = d.get_status()
        assert s["users_total"] == 0
        assert s["users_home"] == 0
        assert s["users_away"] == 0

    def test_status_counts(self):
        d = make_detector()
        d.add_user(ALICE.copy())
        d.get_user("user-alice")["state"] = "home"
        d.add_user(BOB.copy())
        d.get_user("user-bob")["state"] = "away"
        s = d.get_status()
        assert s["users_home"] == 1
        assert s["users_away"] == 1
        assert s["users_total"] == 2


# ── FastAPI endpoints ─────────────────────────────────────────────────────────

class TestPresenceAPI:
    @pytest.mark.asyncio
    async def test_health(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.presence_detection.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_add_and_get_user(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.presence_detection.main as pd_main
        pd_main._detector = make_detector()

        async with AsyncClient(transport=ASGITransport(app=pd_main.app), base_url="http://test") as c:
            r = await c.post("/users", json=ALICE)
            assert r.status_code == 201

            r2 = await c.get("/users/user-alice")
            assert r2.status_code == 200
            assert r2.json()["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_delete_user(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.presence_detection.main as pd_main
        pd_main._detector = make_detector()
        pd_main._detector.add_user(ALICE.copy())

        async with AsyncClient(transport=ASGITransport(app=pd_main.app), base_url="http://test") as c:
            r = await c.delete("/users/user-alice")
            assert r.status_code == 204

            r2 = await c.get("/users/user-alice")
            assert r2.status_code == 404

    @pytest.mark.asyncio
    async def test_get_404_for_unknown_user(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.presence_detection.main as pd_main
        pd_main._detector = make_detector()

        async with AsyncClient(transport=ASGITransport(app=pd_main.app), base_url="http://test") as c:
            r = await c.get("/users/no-such-user")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_widget_served(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.presence_detection.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/widget")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_served(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.presence_detection.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/settings")
        assert r.status_code == 200
