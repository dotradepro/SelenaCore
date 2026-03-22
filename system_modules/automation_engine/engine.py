"""
system_modules/automation_engine/engine.py — AutomationEngine business logic

Rule schema (YAML / dict):
  id: unique_rule_id
  name: "Human name"
  enabled: true
  trigger:
    type: time | cron | event | device_state | presence
    # --- time ---
    at: "HH:MM"          # daily at given time
    # --- cron ---
    cron: "0 8 * * *"    # standard cron expression
    # --- event ---
    event_type: "device.state_changed"
    # --- device_state ---
    device_id: "uuid..."
    attribute: "temperature"
    condition: "gt"       # gt | lt | eq | ne | gte | lte | changed
    value: 25.0
    # --- presence ---
    presence: "home" | "away"  # from presence_detection module events
  conditions:            # optional list (AND logic)
    - type: time_range
      from: "08:00"
      to:   "22:00"
    - type: device_state
      device_id: "uuid..."
      attribute: "state"
      condition: "eq"
      value: "on"
  actions:               # list, executed in order
    - type: device_command
      device_id: "uuid..."
      state: { "state": "ON", "brightness": 255 }
    - type: scene
      scene_id: "scene_morning"
    - type: notify
      message: "Temperature is too high!"
      channel: "push"    # push | voice | telegram
    - type: delay
      seconds: 5
    - type: publish_event
      event_type: "automation.custom_event"
      payload: {}
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone, time as dt_time
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ── Condition evaluators ─────────────────────────────────────────────────────

def _eval_condition(actual: Any, condition: str, expected: Any) -> bool:
    """Compare actual value against expected using the given condition string."""
    try:
        if condition == "eq":
            return actual == expected
        if condition == "ne":
            return actual != expected
        if condition == "gt":
            return float(actual) > float(expected)
        if condition == "lt":
            return float(actual) < float(expected)
        if condition == "gte":
            return float(actual) >= float(expected)
        if condition == "lte":
            return float(actual) <= float(expected)
        if condition == "changed":
            return True  # attribute changed — caller already confirmed
    except (TypeError, ValueError):
        pass
    return False


def _now_in_range(from_str: str, to_str: str) -> bool:
    """Return True if current local time is within [from_str, to_str] (HH:MM)."""
    try:
        h1, m1 = (int(x) for x in from_str.split(":"))
        h2, m2 = (int(x) for x in to_str.split(":"))
        now = datetime.now()
        t_now = dt_time(now.hour, now.minute)
        t_from = dt_time(h1, m1)
        t_to = dt_time(h2, m2)
        if t_from <= t_to:
            return t_from <= t_now <= t_to
        else:
            # overnight range e.g. 22:00 – 06:00
            return t_now >= t_from or t_now <= t_to
    except Exception:
        return True  # if parse fails, don't block execution


# ── Automation Rule ──────────────────────────────────────────────────────────

class AutomationRule:
    """A single automation rule parsed from a dict/YAML definition."""

    def __init__(self, definition: dict) -> None:
        self.id: str = definition.get("id") or str(uuid.uuid4())
        self.name: str = definition.get("name", self.id)
        self.enabled: bool = definition.get("enabled", True)
        self.trigger: dict = definition.get("trigger", {})
        self.conditions: list[dict] = definition.get("conditions", [])
        self.actions: list[dict] = definition.get("actions", [])
        self._raw: dict = definition

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "trigger": self.trigger,
            "conditions": self.conditions,
            "actions": self.actions,
        }


# ── AutomationEngine ─────────────────────────────────────────────────────────

class AutomationEngine:
    """Loads rules, evaluates triggers and conditions, executes actions."""

    def __init__(
        self,
        send_device_command_cb: Callable,
        publish_event_cb: Callable,
        get_device_state_cb: Callable,
        send_notification_cb: Callable,
        data_dir: str = "/var/lib/selena/automation",
    ) -> None:
        self._send_device_command = send_device_command_cb
        self._publish_event = publish_event_cb
        self._get_device_state = get_device_state_cb
        self._send_notification = send_notification_cb
        self._data_dir = data_dir
        self._rules: dict[str, AutomationRule] = {}
        self._run_count: int = 0
        self._error_count: int = 0
        self._last_triggered: dict[str, str] = {}  # rule_id → ISO timestamp
        # Store previous device states for "changed" condition
        self._device_state_cache: dict[str, dict] = {}

    # ── Rule management ───────────────────────────────────────────────────────

    def load_rule(self, definition: dict) -> AutomationRule:
        rule = AutomationRule(definition)
        self._rules[rule.id] = rule
        logger.info(f"Loaded rule: {rule.id} '{rule.name}'")
        return rule

    def load_rules_from_yaml(self, yaml_text: str) -> list[AutomationRule]:
        try:
            parsed = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML: {exc}") from exc

        if isinstance(parsed, dict) and "automations" in parsed:
            items = parsed["automations"]
        elif isinstance(parsed, list):
            items = parsed
        else:
            raise ValueError("YAML must be a list or have 'automations' key")

        rules = []
        for item in items:
            rule = self.load_rule(item)
            rules.append(rule)
        return rules

    def get_rule(self, rule_id: str) -> AutomationRule | None:
        return self._rules.get(rule_id)

    def list_rules(self) -> list[dict]:
        return [r.to_dict() for r in self._rules.values()]

    def delete_rule(self, rule_id: str) -> bool:
        if rule_id in self._rules:
            del self._rules[rule_id]
            return True
        return False

    def enable_rule(self, rule_id: str, enabled: bool) -> bool:
        rule = self._rules.get(rule_id)
        if rule:
            rule.enabled = enabled
            return True
        return False

    # ── Event dispatch ────────────────────────────────────────────────────────

    async def on_event(self, event_type: str, payload: dict) -> None:
        """Entry point for events from the Event Bus webhook."""
        for rule in list(self._rules.values()):
            if not rule.enabled:
                continue
            trigger = rule.trigger
            t_type = trigger.get("type")

            if t_type == "event" and trigger.get("event_type") == event_type:
                if await self._check_conditions(rule, payload):
                    await self._execute(rule, {"event": {"type": event_type, "payload": payload}})

            elif t_type == "device_state" and event_type == "device.state_changed":
                await self._check_device_state_trigger(rule, trigger, payload)

            elif t_type == "presence" and event_type in ("presence.home", "presence.away"):
                wanted = trigger.get("presence", "")
                detected = "home" if event_type == "presence.home" else "away"
                if wanted == detected:
                    if await self._check_conditions(rule, payload):
                        await self._execute(rule, {"presence": detected})

    async def _check_device_state_trigger(
        self,
        rule: AutomationRule,
        trigger: dict,
        payload: dict,
    ) -> None:
        device_id = trigger.get("device_id")
        if device_id and payload.get("device_id") != device_id:
            return

        attribute = trigger.get("attribute")
        condition = trigger.get("condition", "changed")
        threshold = trigger.get("value")

        new_state = payload.get("new_state") or payload.get("state") or {}
        if attribute:
            actual = new_state.get(attribute)
            if actual is None:
                return
            if not _eval_condition(actual, condition, threshold):
                return

        if await self._check_conditions(rule, payload):
            await self._execute(rule, {"device_state": new_state})

    # ── Time-based trigger ────────────────────────────────────────────────────

    async def check_time_triggers(self) -> None:
        """Called every minute by the time-check loop."""
        now = datetime.now()
        current_hhmm = f"{now.hour:02d}:{now.minute:02d}"

        for rule in list(self._rules.values()):
            if not rule.enabled:
                continue
            trigger = rule.trigger
            t_type = trigger.get("type")

            if t_type == "time":
                rule_time = trigger.get("at", "")
                if rule_time == current_hhmm:
                    last = self._last_triggered.get(rule.id, "")
                    if last == current_hhmm:
                        continue  # already fired this minute
                    if await self._check_conditions(rule, {}):
                        await self._execute(rule, {"time": current_hhmm})

    # ── Condition checking ────────────────────────────────────────────────────

    async def _check_conditions(self, rule: AutomationRule, context: dict) -> bool:
        """Evaluate all rule conditions (AND logic). Empty list → True."""
        for cond in rule.conditions:
            cond_type = cond.get("type")
            if cond_type == "time_range":
                if not _now_in_range(cond.get("from", "00:00"), cond.get("to", "23:59")):
                    return False
            elif cond_type == "device_state":
                try:
                    state = await self._get_device_state(cond["device_id"])
                    actual = state.get(cond["attribute"])
                    if not _eval_condition(actual, cond["condition"], cond.get("value")):
                        return False
                except Exception as exc:
                    logger.warning(f"Condition check failed: {exc}")
                    return False
        return True

    # ── Action execution ──────────────────────────────────────────────────────

    async def _execute(self, rule: AutomationRule, context: dict) -> None:
        """Execute all actions in a rule sequentially."""
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        self._last_triggered[rule.id] = f"{datetime.now().hour:02d}:{datetime.now().minute:02d}"
        self._run_count += 1

        logger.info(f"Automation '{rule.name}' triggered")
        await self._publish_event("automation.triggered", {
            "rule_id": rule.id,
            "rule_name": rule.name,
            "timestamp": now_iso,
            "context": context,
        })

        for action in rule.actions:
            try:
                await self._execute_action(action, context)
            except Exception as exc:
                self._error_count += 1
                logger.error(f"Action failed in rule '{rule.name}': {exc}")

    async def _execute_action(self, action: dict, context: dict) -> None:
        a_type = action.get("type")

        if a_type == "device_command":
            await self._send_device_command(
                action["device_id"],
                action.get("state", {}),
            )

        elif a_type == "delay":
            await asyncio.sleep(float(action.get("seconds", 1)))

        elif a_type == "notify":
            await self._send_notification(
                message=action.get("message", ""),
                channel=action.get("channel", "push"),
            )

        elif a_type == "publish_event":
            await self._publish_event(
                action.get("event_type", "automation.action"),
                action.get("payload", {}),
            )

        elif a_type == "scene":
            # Scenes are fired as events — scene module picks them up
            await self._publish_event("automation.scene_activate", {
                "scene_id": action.get("scene_id", ""),
            })

        else:
            logger.warning(f"Unknown action type: {a_type}")

    # ── Status ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        enabled = sum(1 for r in self._rules.values() if r.enabled)
        return {
            "rules_total": len(self._rules),
            "rules_enabled": enabled,
            "run_count": self._run_count,
            "error_count": self._error_count,
            "last_triggered": self._last_triggered,
        }
