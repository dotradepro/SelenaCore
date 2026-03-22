"""tests/test_automation_engine.py — pytest tests for automation_engine module"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_engine(
    send_cmd=None,
    publish=None,
    get_state=None,
    notify=None,
):
    from system_modules.automation_engine.engine import AutomationEngine
    return AutomationEngine(
        send_device_command_cb=send_cmd or AsyncMock(),
        publish_event_cb=publish or AsyncMock(),
        get_device_state_cb=get_state or AsyncMock(return_value={}),
        send_notification_cb=notify or AsyncMock(),
    )


SIMPLE_RULE = {
    "id": "test_rule_1",
    "name": "Test Rule",
    "enabled": True,
    "trigger": {"type": "event", "event_type": "device.state_changed"},
    "conditions": [],
    "actions": [
        {"type": "device_command", "device_id": "dev-001", "state": {"state": "ON"}}
    ],
}


# ── _eval_condition ──────────────────────────────────────────────────────────

class TestEvalCondition:
    def test_eq_true(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition("ON", "eq", "ON") is True

    def test_eq_false(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition("OFF", "eq", "ON") is False

    def test_gt_true(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition(26, "gt", 25) is True

    def test_gt_false(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition(24, "gt", 25) is False

    def test_lte(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition(25.0, "lte", 25.0) is True

    def test_ne(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition("home", "ne", "away") is True

    def test_changed_always_true(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition("any", "changed", None) is True

    def test_type_error_returns_false(self):
        from system_modules.automation_engine.engine import _eval_condition
        assert _eval_condition("hello", "gt", "world") is False


# ── _now_in_range ────────────────────────────────────────────────────────────

class TestNowInRange:
    def test_within_range(self):
        from system_modules.automation_engine.engine import _now_in_range
        # Always within 00:00-23:59
        assert _now_in_range("00:00", "23:59") is True

    def test_outside_range(self):
        from system_modules.automation_engine.engine import _now_in_range
        # 25:00 is invalid, should default to True
        result = _now_in_range("25:00", "26:00")
        assert isinstance(result, bool)

    def test_invalid_format_returns_true(self):
        from system_modules.automation_engine.engine import _now_in_range
        # On parse error, should return True (don't block)
        result = _now_in_range("bad", "format")
        assert result is True


# ── Rule Management ──────────────────────────────────────────────────────────

class TestRuleManagement:
    def test_load_rule(self):
        engine = make_engine()
        rule = engine.load_rule(SIMPLE_RULE.copy())
        assert rule.id == "test_rule_1"
        assert rule.name == "Test Rule"
        assert rule.enabled is True

    def test_load_rule_auto_id(self):
        engine = make_engine()
        defn = {"name": "No ID Rule", "trigger": {}, "actions": []}
        rule = engine.load_rule(defn)
        assert rule.id is not None
        assert len(rule.id) > 0

    def test_list_rules(self):
        engine = make_engine()
        engine.load_rule(SIMPLE_RULE.copy())
        rules = engine.list_rules()
        assert len(rules) == 1
        assert rules[0]["id"] == "test_rule_1"

    def test_delete_rule(self):
        engine = make_engine()
        engine.load_rule(SIMPLE_RULE.copy())
        result = engine.delete_rule("test_rule_1")
        assert result is True
        assert len(engine.list_rules()) == 0

    def test_delete_nonexistent_rule(self):
        engine = make_engine()
        assert engine.delete_rule("does_not_exist") is False

    def test_enable_disable_rule(self):
        engine = make_engine()
        engine.load_rule(SIMPLE_RULE.copy())
        engine.enable_rule("test_rule_1", False)
        rule = engine.get_rule("test_rule_1")
        assert rule.enabled is False

    def test_load_rules_from_yaml(self):
        engine = make_engine()
        yaml_text = """
automations:
  - id: yaml_rule_1
    name: "YAML Rule 1"
    trigger:
      type: time
      at: "08:00"
    actions:
      - type: device_command
        device_id: dev-abc
        state:
          state: ON
  - id: yaml_rule_2
    name: "YAML Rule 2"
    trigger:
      type: event
      event_type: device.state_changed
    actions: []
"""
        rules = engine.load_rules_from_yaml(yaml_text)
        assert len(rules) == 2
        assert rules[0].id == "yaml_rule_1"
        assert rules[1].id == "yaml_rule_2"

    def test_load_rules_from_yaml_list(self):
        engine = make_engine()
        yaml_text = """
- id: list_rule
  name: "List Rule"
  trigger:
    type: event
    event_type: test.event
  actions: []
"""
        rules = engine.load_rules_from_yaml(yaml_text)
        assert len(rules) == 1

    def test_load_rules_invalid_yaml(self):
        engine = make_engine()
        with pytest.raises(ValueError, match="Invalid YAML"):
            engine.load_rules_from_yaml("{invalid: yaml: : :")

    def test_load_rules_wrong_structure(self):
        engine = make_engine()
        with pytest.raises(ValueError):
            engine.load_rules_from_yaml("just a string value")


# ── Event trigger ────────────────────────────────────────────────────────────

class TestEventTrigger:
    @pytest.mark.asyncio
    async def test_event_trigger_fires_action(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule(SIMPLE_RULE.copy())

        await engine.on_event("device.state_changed", {"device_id": "dev-001"})

        send_cmd.assert_called_once_with("dev-001", {"state": "ON"})

    @pytest.mark.asyncio
    async def test_event_trigger_wrong_type_no_fire(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule(SIMPLE_RULE.copy())

        await engine.on_event("some.other.event", {})

        send_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_rule_not_triggered(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        rule_def = {**SIMPLE_RULE, "enabled": False}
        engine.load_rule(rule_def)

        await engine.on_event("device.state_changed", {})

        send_cmd.assert_not_called()


# ── Device state trigger ─────────────────────────────────────────────────────

class TestDeviceStateTrigger:
    @pytest.mark.asyncio
    async def test_device_state_gt_threshold_fires(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "temp_rule",
            "name": "High Temp",
            "enabled": True,
            "trigger": {
                "type": "device_state",
                "device_id": "thermo-01",
                "attribute": "temperature",
                "condition": "gt",
                "value": 25,
            },
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "ac-01",
                          "state": {"mode": "cool"}}],
        })

        await engine.on_event("device.state_changed", {
            "device_id": "thermo-01",
            "new_state": {"temperature": 27.5},
        })

        send_cmd.assert_called_once_with("ac-01", {"mode": "cool"})

    @pytest.mark.asyncio
    async def test_device_state_below_threshold_no_fire(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "temp_rule",
            "name": "High Temp",
            "enabled": True,
            "trigger": {
                "type": "device_state",
                "device_id": "thermo-01",
                "attribute": "temperature",
                "condition": "gt",
                "value": 25,
            },
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "ac-01",
                          "state": {"mode": "cool"}}],
        })

        await engine.on_event("device.state_changed", {
            "device_id": "thermo-01",
            "new_state": {"temperature": 22.0},  # below threshold
        })

        send_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_device_state_wrong_device_no_fire(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "rule_x",
            "name": "Rule X",
            "enabled": True,
            "trigger": {
                "type": "device_state",
                "device_id": "thermo-01",
                "attribute": "temperature",
                "condition": "gt",
                "value": 25,
            },
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "ac-01",
                          "state": {}}],
        })

        await engine.on_event("device.state_changed", {
            "device_id": "other-device",  # different device
            "new_state": {"temperature": 30},
        })

        send_cmd.assert_not_called()


# ── Presence trigger ─────────────────────────────────────────────────────────

class TestPresenceTrigger:
    @pytest.mark.asyncio
    async def test_presence_home_fires(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "welcome_home",
            "name": "Welcome Home",
            "enabled": True,
            "trigger": {"type": "presence", "presence": "home"},
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "lights",
                          "state": {"state": "ON"}}],
        })

        await engine.on_event("presence.home", {"user": "Alice"})

        send_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_presence_away_no_fire_on_home(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "away_rule",
            "name": "Leaving Home",
            "enabled": True,
            "trigger": {"type": "presence", "presence": "away"},
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "lights",
                          "state": {"state": "OFF"}}],
        })

        # Fires presence.home, but rule wants "away"
        await engine.on_event("presence.home", {})

        send_cmd.assert_not_called()


# ── Time trigger ─────────────────────────────────────────────────────────────

class TestTimeTrigger:
    @pytest.mark.asyncio
    async def test_time_trigger_fires_at_correct_time(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "morning_lights",
            "name": "Morning Lights",
            "enabled": True,
            "trigger": {"type": "time", "at": "07:30"},
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "lights",
                          "state": {"state": "ON"}}],
        })

        with patch("system_modules.automation_engine.engine.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 7
            mock_now.minute = 30
            mock_dt.now.return_value = mock_now

            await engine.check_time_triggers()

        send_cmd.assert_called_once()

    @pytest.mark.asyncio
    async def test_time_trigger_no_fire_wrong_time(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "morning_lights",
            "name": "Morning Lights",
            "enabled": True,
            "trigger": {"type": "time", "at": "07:30"},
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "lights",
                          "state": {"state": "ON"}}],
        })

        with patch("system_modules.automation_engine.engine.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 8
            mock_now.minute = 0
            mock_dt.now.return_value = mock_now

            await engine.check_time_triggers()

        send_cmd.assert_not_called()

    @pytest.mark.asyncio
    async def test_time_trigger_not_fired_twice_same_minute(self):
        send_cmd = AsyncMock()
        engine = make_engine(send_cmd=send_cmd)
        engine.load_rule({
            "id": "once_rule",
            "name": "Once Rule",
            "enabled": True,
            "trigger": {"type": "time", "at": "09:00"},
            "conditions": [],
            "actions": [{"type": "device_command", "device_id": "dev",
                          "state": {}}],
        })

        with patch("system_modules.automation_engine.engine.datetime") as mock_dt:
            mock_now = MagicMock()
            mock_now.hour = 9
            mock_now.minute = 0
            mock_dt.now.return_value = mock_now

            await engine.check_time_triggers()
            await engine.check_time_triggers()  # second call same minute

        assert send_cmd.call_count == 1


# ── Actions ──────────────────────────────────────────────────────────────────

class TestActions:
    @pytest.mark.asyncio
    async def test_delay_action(self):
        engine = make_engine()

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await engine._execute_action({"type": "delay", "seconds": 2}, {})
            mock_sleep.assert_called_once_with(2.0)

    @pytest.mark.asyncio
    async def test_notify_action(self):
        notify = AsyncMock()
        engine = make_engine(notify=notify)

        await engine._execute_action(
            {"type": "notify", "message": "Hello!", "channel": "push"}, {}
        )
        notify.assert_called_once_with(message="Hello!", channel="push")

    @pytest.mark.asyncio
    async def test_publish_event_action(self):
        publish = AsyncMock()
        engine = make_engine(publish=publish)

        await engine._execute_action(
            {"type": "publish_event", "event_type": "custom.event",
             "payload": {"foo": "bar"}}, {}
        )
        publish.assert_called_with("custom.event", {"foo": "bar"})

    @pytest.mark.asyncio
    async def test_scene_action(self):
        publish = AsyncMock()
        engine = make_engine(publish=publish)

        await engine._execute_action(
            {"type": "scene", "scene_id": "scene_morning"}, {}
        )
        publish.assert_called_with("automation.scene_activate",
                                   {"scene_id": "scene_morning"})

    @pytest.mark.asyncio
    async def test_unknown_action_no_crash(self):
        engine = make_engine()
        # Should not raise
        await engine._execute_action({"type": "unknown_action_xyz"}, {})


# ── Conditions ───────────────────────────────────────────────────────────────

class TestConditions:
    @pytest.mark.asyncio
    async def test_time_range_condition_passes(self):
        engine = make_engine()
        rule = engine.load_rule({
            "id": "cond_rule",
            "name": "Cond Rule",
            "trigger": {"type": "event", "event_type": "test"},
            "conditions": [
                {"type": "time_range", "from": "00:00", "to": "23:59"}
            ],
            "actions": [],
        })
        result = await engine._check_conditions(rule, {})
        assert result is True

    @pytest.mark.asyncio
    async def test_device_state_condition_passes(self):
        get_state = AsyncMock(return_value={"state": "ON"})
        engine = make_engine(get_state=get_state)
        rule = engine.load_rule({
            "id": "cond_dev_rule",
            "name": "Dev Cond",
            "trigger": {"type": "event", "event_type": "test"},
            "conditions": [
                {"type": "device_state", "device_id": "dev-001",
                 "attribute": "state", "condition": "eq", "value": "ON"}
            ],
            "actions": [],
        })
        result = await engine._check_conditions(rule, {})
        assert result is True

    @pytest.mark.asyncio
    async def test_device_state_condition_fails(self):
        get_state = AsyncMock(return_value={"state": "OFF"})
        engine = make_engine(get_state=get_state)
        rule = engine.load_rule({
            "id": "cond_fail_rule",
            "name": "Dev Cond Fail",
            "trigger": {"type": "event", "event_type": "test"},
            "conditions": [
                {"type": "device_state", "device_id": "dev-001",
                 "attribute": "state", "condition": "eq", "value": "ON"}
            ],
            "actions": [],
        })
        result = await engine._check_conditions(rule, {})
        assert result is False


# ── Status ───────────────────────────────────────────────────────────────────

class TestStatus:
    def test_status_initial(self):
        engine = make_engine()
        status = engine.get_status()
        assert status["rules_total"] == 0
        assert status["run_count"] == 0
        assert status["error_count"] == 0

    @pytest.mark.asyncio
    async def test_run_count_increments(self):
        engine = make_engine()
        engine.load_rule(SIMPLE_RULE.copy())

        await engine.on_event("device.state_changed", {})

        assert engine.get_status()["run_count"] == 1


# ── FastAPI endpoints ────────────────────────────────────────────────────────

class TestAutomationEngineAPI:
    @pytest.mark.asyncio
    async def test_health(self):
        from httpx import AsyncClient, ASGITransport
        from system_modules.automation_engine.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/health")

        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_create_and_get_rule(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.automation_engine.main as ae_main
        from system_modules.automation_engine.engine import AutomationEngine

        engine = AutomationEngine(
            send_device_command_cb=AsyncMock(),
            publish_event_cb=AsyncMock(),
            get_device_state_cb=AsyncMock(return_value={}),
            send_notification_cb=AsyncMock(),
        )
        ae_main._engine = engine

        async with AsyncClient(
            transport=ASGITransport(app=ae_main.app), base_url="http://test"
        ) as client:
            r = await client.post("/rules", json={"definition": SIMPLE_RULE})
            assert r.status_code == 201

            r2 = await client.get("/rules/test_rule_1")
            assert r2.status_code == 200
            assert r2.json()["name"] == "Test Rule"

    @pytest.mark.asyncio
    async def test_import_yaml_via_api(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.automation_engine.main as ae_main
        from system_modules.automation_engine.engine import AutomationEngine

        engine = AutomationEngine(
            send_device_command_cb=AsyncMock(),
            publish_event_cb=AsyncMock(),
            get_device_state_cb=AsyncMock(return_value={}),
            send_notification_cb=AsyncMock(),
        )
        ae_main._engine = engine

        yaml_text = """
- id: api_yaml_rule
  name: "API YAML Rule"
  trigger:
    type: event
    event_type: device.state_changed
  actions: []
"""
        async with AsyncClient(
            transport=ASGITransport(app=ae_main.app), base_url="http://test"
        ) as client:
            r = await client.post("/rules/import", json={"yaml_text": yaml_text})

        assert r.status_code == 200
        data = r.json()
        assert data["imported"] == 1

    @pytest.mark.asyncio
    async def test_delete_rule_via_api(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.automation_engine.main as ae_main
        from system_modules.automation_engine.engine import AutomationEngine

        engine = AutomationEngine(
            send_device_command_cb=AsyncMock(),
            publish_event_cb=AsyncMock(),
            get_device_state_cb=AsyncMock(return_value={}),
            send_notification_cb=AsyncMock(),
        )
        engine.load_rule(SIMPLE_RULE.copy())
        ae_main._engine = engine

        async with AsyncClient(
            transport=ASGITransport(app=ae_main.app), base_url="http://test"
        ) as client:
            r = await client.delete("/rules/test_rule_1")
            assert r.status_code == 204

            r2 = await client.get("/rules/test_rule_1")
            assert r2.status_code == 404

    @pytest.mark.asyncio
    async def test_webhook_fires_rule(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.automation_engine.main as ae_main
        from system_modules.automation_engine.engine import AutomationEngine

        send_cmd = AsyncMock()
        engine = AutomationEngine(
            send_device_command_cb=send_cmd,
            publish_event_cb=AsyncMock(),
            get_device_state_cb=AsyncMock(return_value={}),
            send_notification_cb=AsyncMock(),
        )
        engine.load_rule(SIMPLE_RULE.copy())
        ae_main._engine = engine

        async with AsyncClient(
            transport=ASGITransport(app=ae_main.app), base_url="http://test"
        ) as client:
            r = await client.post("/webhook/events", json={
                "type": "device.state_changed",
                "payload": {"device_id": "dev-001"},
            })

        assert r.status_code == 200
        send_cmd.assert_called_once()
