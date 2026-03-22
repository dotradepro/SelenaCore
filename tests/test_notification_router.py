"""tests/test_notification_router.py — pytest tests for notification_router module"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import httpx


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_router(publish=None):
    from system_modules.notification_router.router import NotificationRouter
    return NotificationRouter(publish_event_cb=publish or AsyncMock())


def add_tg_channel(rt, name="telegram"):
    rt.add_channel(name, {"bot_token": "TEST_TOKEN", "chat_id": "12345"})


def add_webhook_channel(rt, url="http://test.hook/notify"):
    rt.add_channel("webhook", {"url": url})


# ── Channel management ────────────────────────────────────────────────────────

class TestChannelManagement:
    def test_add_valid_channel(self):
        rt = make_router()
        rt.add_channel("telegram", {"bot_token": "abc", "chat_id": "123"})
        chs = rt.get_channels()
        assert "telegram" in chs

    def test_add_invalid_channel_raises(self):
        rt = make_router()
        with pytest.raises(ValueError):
            rt.add_channel("email", {"address": "a@b.com"})

    def test_remove_channel(self):
        rt = make_router()
        add_tg_channel(rt)
        assert rt.remove_channel("telegram") is True
        assert "telegram" not in rt.get_channels()

    def test_remove_nonexistent_channel(self):
        rt = make_router()
        assert rt.remove_channel("push") is False

    def test_all_valid_channels(self):
        rt = make_router()
        for ch in ["tts", "telegram", "push", "webhook"]:
            rt.add_channel(ch, {})
        assert len(rt.get_channels()) == 4


# ── Rule management ────────────────────────────────────────────────────────────

class TestRuleManagement:
    def test_add_rule(self):
        rt = make_router()
        add_tg_channel(rt)
        rule_id = rt.add_rule({"channel": "telegram", "priority": 10})
        assert rule_id is not None
        assert any(r["rule_id"] == rule_id for r in rt.get_rules())

    def test_rules_sorted_by_priority(self):
        rt = make_router()
        rt.add_rule({"channel": "tts", "priority": 200, "rule_id": "r1"})
        rt.add_rule({"channel": "tts", "priority": 50, "rule_id": "r2"})
        rt.add_rule({"channel": "tts", "priority": 100, "rule_id": "r3"})
        ids = [r["rule_id"] for r in rt.get_rules()]
        assert ids == ["r2", "r3", "r1"]

    def test_remove_rule(self):
        rt = make_router()
        rule_id = rt.add_rule({"channel": "tts", "rule_id": "test-rule"})
        assert rt.remove_rule(rule_id) is True
        assert all(r["rule_id"] != rule_id for r in rt.get_rules())

    def test_remove_nonexistent_rule(self):
        rt = make_router()
        assert rt.remove_rule("no-such-rule") is False

    def test_update_existing_rule(self):
        rt = make_router()
        rt.add_rule({"channel": "tts", "priority": 10, "rule_id": "r1"})
        rt.add_rule({"channel": "push", "priority": 5, "rule_id": "r1"})
        # Should only have one rule with r1
        assert len([r for r in rt.get_rules() if r["rule_id"] == "r1"]) == 1


# ── Routing / delivery ─────────────────────────────────────────────────────────

class TestRouting:
    @pytest.mark.asyncio
    async def test_send_no_channels_returns_empty(self):
        rt = make_router()
        rt.add_rule({"channel": "telegram", "priority": 10})
        # No channels configured → nothing delivered
        result = await rt.send("Hello")
        assert result == []

    @pytest.mark.asyncio
    async def test_send_matches_rule_and_delivers(self):
        rt = make_router()
        add_webhook_channel(rt, "http://hook/recv")
        rt.add_rule({"channel": "webhook", "priority": 10})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            result = await rt.send("Test message")
        assert "webhook" in result

    @pytest.mark.asyncio
    async def test_send_level_filter_match(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook", "priority": 10, "level": "critical"})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            result = await rt.send("Alert!", level="critical")
        assert "webhook" in result

    @pytest.mark.asyncio
    async def test_send_level_filter_no_match(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook", "priority": 10, "level": "critical"})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            result = await rt.send("Info msg", level="info")
        assert "webhook" not in result

    @pytest.mark.asyncio
    async def test_send_event_type_filter(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook", "priority": 10, "event_types": ["device.offline"]})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            result_match = await rt.send("Offline!", event_type="device.offline")
            result_no = await rt.send("Other", event_type="device.state_changed")
        assert "webhook" in result_match
        assert "webhook" not in result_no

    @pytest.mark.asyncio
    async def test_send_disabled_rule_skipped(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook", "priority": 10, "enabled": False})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            result = await rt.send("Test")
        assert "webhook" not in result

    @pytest.mark.asyncio
    async def test_handle_event(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook", "event_types": ["sensor.alert"]})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            channels = await rt.handle_event("sensor.alert", {"message": "Heat alert", "level": "warning"})
        assert "webhook" in channels

    @pytest.mark.asyncio
    async def test_delivery_error_does_not_raise(self):
        rt = make_router()
        add_webhook_channel(rt, "http://badhost/recv")
        rt.add_rule({"channel": "webhook", "priority": 10})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(side_effect=Exception("timeout"))):
            # Should not raise
            result = await rt.send("Test")
        # Result may be empty or webhook attempted
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_stats_incremented_on_success(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook", "priority": 10})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            await rt.send("A")
            await rt.send("B")
        assert rt._stats.get("webhook", 0) == 2


# ── History ────────────────────────────────────────────────────────────────────

class TestHistory:
    @pytest.mark.asyncio
    async def test_history_recorded(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook"})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            await rt.send("Message one")
        history = rt.get_history()
        assert len(history) == 1
        assert history[0]["message"] == "Message one"

    @pytest.mark.asyncio
    async def test_history_limit(self):
        rt = make_router()
        for i in range(10):
            await rt.send(f"Msg {i}")
        history = rt.get_history(limit=5)
        assert len(history) <= 5


# ── Status ────────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_initial(self):
        rt = make_router()
        s = rt.get_status()
        assert s["channels"] == 0
        assert s["rules"] == 0
        assert s["total_sent"] == 0

    @pytest.mark.asyncio
    async def test_status_after_config(self):
        rt = make_router()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook"})
        s = rt.get_status()
        assert s["channels"] == 1
        assert s["rules"] == 1


# ── Deliver methods ────────────────────────────────────────────────────────────

class TestDeliverMethods:
    @pytest.mark.asyncio
    async def test_deliver_telegram_missing_token(self):
        rt = make_router()
        result = await rt._deliver_telegram({}, "Test")
        assert result is False

    @pytest.mark.asyncio
    async def test_deliver_webhook_missing_url(self):
        rt = make_router()
        result = await rt._deliver_webhook({}, "Test", "info", {})
        assert result is False

    @pytest.mark.asyncio
    async def test_deliver_tts_ok(self):
        rt = make_router()
        req = httpx.Request("POST", "http://localhost:8080/api/tts/say")
        mock_resp = httpx.Response(200, request=req)
        with patch("httpx.AsyncClient") as mc:
            instance = AsyncMock()
            mc.return_value.__aenter__.return_value = instance
            instance.post.return_value = mock_resp
            result = await rt._deliver_tts({"tts_url": "http://localhost:8080/api/tts/say"}, "Hello")
        assert result is True


# ── API ───────────────────────────────────────────────────────────────────────

class TestNotificationAPI:
    def _make_app(self):
        import system_modules.notification_router.main as nr_main
        rt = make_router()
        nr_main._router = rt
        return nr_main.app, rt

    @pytest.mark.asyncio
    async def test_health(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_send_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, rt = self._make_app()
        add_webhook_channel(rt)
        rt.add_rule({"channel": "webhook"})
        with patch.object(rt, "_deliver_webhook", new=AsyncMock(return_value=True)):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                r = await c.post("/notify/send", json={"message": "Test", "level": "info"})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    @pytest.mark.asyncio
    async def test_add_channel_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, rt = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/channels", json={"name": "webhook", "config": {"url": "http://x.com"}})
        assert r.status_code == 201
        assert "webhook" in rt.get_channels()

    @pytest.mark.asyncio
    async def test_add_invalid_channel_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/channels", json={"name": "email", "config": {}})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_remove_channel_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, rt = self._make_app()
        add_tg_channel(rt)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/channels/telegram")
        assert r.status_code == 204

    @pytest.mark.asyncio
    async def test_remove_channel_not_found(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/channels/nonexistent")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_add_rule_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, rt = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/rules", json={"channel": "webhook", "priority": 10})
        assert r.status_code == 201
        assert "rule_id" in r.json()

    @pytest.mark.asyncio
    async def test_add_rule_invalid_channel(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/rules", json={"channel": "email"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_remove_rule_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, rt = self._make_app()
        rule_id = rt.add_rule({"channel": "push", "rule_id": "my-rule"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete(f"/rules/{rule_id}")
        assert r.status_code == 204

    @pytest.mark.asyncio
    async def test_remove_rule_not_found(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.delete("/rules/no-such-rule")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_history_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/notify/history")
        assert r.status_code == 200
        assert "history" in r.json()

    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/notify/status")
        assert r.status_code == 200
        assert "total_sent" in r.json()

    @pytest.mark.asyncio
    async def test_widget_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/widget.html")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/settings.html")
        assert r.status_code == 200
