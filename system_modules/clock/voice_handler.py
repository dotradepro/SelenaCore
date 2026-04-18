"""system_modules/clock/voice_handler.py — voice intent → ClockService glue."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .service import ClockService

logger = logging.getLogger(__name__)


_DURATION_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
}


def _parse_duration_seconds(params: dict[str, Any]) -> int | None:
    """Pull (value, unit) → seconds out of regex named groups."""
    raw_value = params.get("value")
    raw_unit = (params.get("unit") or "min").lower().strip()
    if raw_value is None:
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None
    multiplier = _DURATION_UNITS.get(raw_unit)
    if multiplier is None:
        # Try to strip trailing 's'
        multiplier = _DURATION_UNITS.get(raw_unit.rstrip("s"))
    if multiplier is None:
        return None
    return value * multiplier


def _parse_alarm_time(params: dict[str, Any]) -> tuple[int, int] | None:
    """Extract (hour, minute) from regex groups (hour, minute, ampm)."""
    raw_h = params.get("hour")
    raw_m = params.get("minute")
    ampm = (params.get("ampm") or "").lower().strip()
    if raw_h is None:
        return None
    try:
        hour = int(raw_h)
        minute = int(raw_m) if raw_m is not None else 0
    except (TypeError, ValueError):
        return None
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


class ClockVoiceHandler:
    def __init__(self, service: ClockService) -> None:
        self._service = service

    async def handle(self, intent: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch to the right ClockService method.

        Returns an action_context dict suitable for ``speak_action`` so the
        LLM can generate a natural-language reply, or None if the intent
        could not be processed.
        """
        try:
            if intent == "clock.set_alarm":
                return await self._set_alarm(params)
            if intent == "clock.set_timer":
                return await self._set_timer(params)
            if intent == "clock.set_reminder":
                return await self._set_reminder(params)
            if intent == "clock.list_alarms":
                return await self._list_alarms()
            if intent in ("clock.stop_alarm", "clock.cancel_alarm"):
                # Merged 2026-04-18: both verbs map to one intent.
                # Handler dismisses ringing alarms first; if none are
                # ringing, deletes the next scheduled alarm.
                return await self._stop_or_cancel_alarm()
            if intent == "clock.cancel_timer":
                return await self._cancel_timer()
        except Exception as exc:
            logger.exception("ClockVoiceHandler error for intent %s: %s", intent, exc)
        return None

    async def _set_alarm(self, params: dict[str, Any]) -> dict[str, Any] | None:
        parsed = _parse_alarm_time(params)
        if parsed is None:
            return {"action": "alarm_failed", "reason": "invalid_time"}
        hour, minute = parsed
        alarm = await self._service.create_alarm(hour=hour, minute=minute, label="")
        return {
            "action": "alarm_created",
            "time": f"{hour:02d}:{minute:02d}",
            "alarm_id": alarm["id"],
        }

    async def _set_timer(self, params: dict[str, Any]) -> dict[str, Any] | None:
        seconds = _parse_duration_seconds(params)
        if seconds is None or seconds <= 0:
            return {"action": "timer_failed", "reason": "invalid_duration"}
        timer = await self._service.create_timer(duration_sec=seconds, label="")
        return {
            "action": "timer_started",
            "duration_sec": seconds,
            "timer_id": timer["id"],
        }

    async def _set_reminder(self, params: dict[str, Any]) -> dict[str, Any] | None:
        seconds = _parse_duration_seconds(params)
        what = (params.get("what") or "").strip()
        if seconds is None or seconds <= 0 or not what:
            return {"action": "reminder_failed", "reason": "invalid_input"}
        due_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        reminder = await self._service.create_reminder(due_at=due_at, label=what)
        return {
            "action": "reminder_created",
            "label": what,
            "in_seconds": seconds,
            "reminder_id": reminder["id"],
        }

    async def _list_alarms(self) -> dict[str, Any]:
        alarms = await self._service.list_alarms()
        enabled = [a for a in alarms if a["enabled"]]
        return {
            "action": "alarms_listed",
            "count": len(enabled),
            "alarms": [
                {"time": f"{a['hour']:02d}:{a['minute']:02d}", "label": a["label"]}
                for a in enabled
            ],
        }

    async def _stop_or_cancel_alarm(self) -> dict[str, Any]:
        """Unified handler for 'stop the alarm' / 'cancel the alarm'.

        Voice users use these verbs interchangeably and the classifier
        cannot reliably tell them apart. Behaviour is context-aware:

          1. If any alarm is ringing RIGHT NOW → dismiss it (primary
             intent when user says "stop the alarm" during wake-up).
          2. Otherwise → delete the soonest enabled alarm from the
             schedule (primary intent when user says "cancel the
             morning alarm" during the day).
        """
        # Priority 1: silence any currently-ringing alarm.
        ringing = list(self._service._ringing_alarms)
        if ringing:
            for alarm_id in ringing:
                await self._service.dismiss_alarm(alarm_id)
            return {"action": "alarm_dismissed", "count": len(ringing)}

        # Priority 2: nothing ringing → delete the next scheduled alarm.
        alarms = await self._service.list_alarms()
        target = next((a for a in alarms if a["enabled"]), None)
        if target is None:
            return {"action": "alarm_cancel_failed", "reason": "none"}
        await self._service.delete_alarm(target["id"])
        return {
            "action": "alarm_cancelled",
            "time": f"{target['hour']:02d}:{target['minute']:02d}",
        }

    async def _cancel_timer(self) -> dict[str, Any]:
        timers = await self._service.list_timers()
        running = [t for t in timers if t["state"] == "running"]
        for t in running:
            await self._service.delete_timer(t["id"])
        return {"action": "timer_cancelled", "count": len(running)}
