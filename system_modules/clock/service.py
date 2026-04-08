"""system_modules/clock/service.py — ClockService

Owns:
  - APScheduler (AsyncIOScheduler) instance for alarms / timers / reminders
  - CRUD on ClockAlarm / ClockTimer / ClockReminder / ClockWorldCity / ClockStopwatch
  - Firing callback that publishes voice.speak + clock.fired events

The whole module runs in-process, so we can call ORM directly via the
session_factory injected from SystemModule.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select

from core.registry.models import (
    ClockAlarm,
    ClockReminder,
    ClockStopwatch,
    ClockTimer,
    ClockWorldCity,
)

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.date import DateTrigger

    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("apscheduler not installed — clock module will not schedule jobs")


PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on read — re-attach UTC for arithmetic."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class ClockService:
    """Business logic + scheduling for the clock module."""

    def __init__(self, session_factory: Any, publish: PublishFn) -> None:
        self._session_factory = session_factory
        self._publish = publish
        self._scheduler: AsyncIOScheduler | None = None
        # Tracks alarms currently ringing (waiting for snooze/dismiss)
        self._ringing_alarms: set[str] = set()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if not APSCHEDULER_AVAILABLE:
            logger.error("apscheduler unavailable — clock scheduling disabled")
            return
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()
        await self._restore_jobs()
        logger.info("ClockService started")

    async def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("ClockService stopped")

    async def _restore_jobs(self) -> None:
        """Re-register APScheduler jobs for all enabled DB rows on boot."""
        async with self._session_factory() as session:
            alarms = (await session.execute(select(ClockAlarm))).scalars().all()
            for a in alarms:
                if a.enabled:
                    self._register_alarm_job(a)

            now = _utcnow()
            reminders = (
                await session.execute(
                    select(ClockReminder).where(ClockReminder.fired == False)  # noqa: E712
                )
            ).scalars().all()
            for r in reminders:
                due = _as_aware(r.due_at)
                if due and due > now:
                    self._register_reminder_job(r)
                else:
                    # Past due — fire immediately on next event-loop tick
                    asyncio.create_task(self._fire_reminder(r.id))

            timers = (
                await session.execute(
                    select(ClockTimer).where(ClockTimer.state == "running")
                )
            ).scalars().all()
            for t in timers:
                started = _as_aware(t.started_at)
                if started:
                    fire_at = started + timedelta(seconds=t.duration_sec)
                    if fire_at > now:
                        self._register_timer_job(t.id, fire_at)
                    else:
                        asyncio.create_task(self._fire_timer(t.id))

    # ── Job registration helpers ────────────────────────────────────────────

    def _register_alarm_job(self, alarm: ClockAlarm) -> None:
        if not self._scheduler:
            return
        job_id = f"alarm:{alarm.id}"
        self._scheduler.remove_job(job_id) if self._scheduler.get_job(job_id) else None

        repeat = alarm.get_repeat_days()
        if repeat:
            # APScheduler day_of_week: 0=mon..6=sun (matches our convention)
            dow = ",".join(str(d) for d in repeat)
            trigger = CronTrigger(day_of_week=dow, hour=alarm.hour, minute=alarm.minute)
        else:
            # One-shot — next occurrence of HH:MM (today or tomorrow)
            now = datetime.now()
            target = now.replace(
                hour=alarm.hour, minute=alarm.minute, second=0, microsecond=0
            )
            if target <= now:
                target += timedelta(days=1)
            trigger = DateTrigger(run_date=target)

        self._scheduler.add_job(
            self._fire_alarm,
            trigger,
            id=job_id,
            kwargs={"alarm_id": alarm.id},
            replace_existing=True,
        )
        logger.info("Alarm scheduled: %s @ %02d:%02d (repeat=%s)",
                    alarm.id, alarm.hour, alarm.minute, repeat)

    def _register_reminder_job(self, reminder: ClockReminder) -> None:
        if not self._scheduler:
            return
        job_id = f"reminder:{reminder.id}"
        due = _as_aware(reminder.due_at)
        if due is None:
            return
        self._scheduler.add_job(
            self._fire_reminder,
            DateTrigger(run_date=due),
            id=job_id,
            kwargs={"reminder_id": reminder.id},
            replace_existing=True,
        )

    def _register_timer_job(self, timer_id: str, fire_at: datetime) -> None:
        if not self._scheduler:
            return
        job_id = f"timer:{timer_id}"
        self._scheduler.add_job(
            self._fire_timer,
            DateTrigger(run_date=fire_at),
            id=job_id,
            kwargs={"timer_id": timer_id},
            replace_existing=True,
        )

    def _remove_job(self, job_id: str) -> None:
        if self._scheduler and self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)

    # ── Firing callbacks ────────────────────────────────────────────────────

    async def _fire_alarm(self, alarm_id: str) -> None:
        async with self._session_factory() as session:
            alarm = await session.get(ClockAlarm, alarm_id)
            if alarm is None or not alarm.enabled:
                return
            label = alarm.label or "Alarm"
            self._ringing_alarms.add(alarm_id)
            logger.info("Alarm fired: %s (%s)", alarm_id, label)

            # If non-recurring, disable after firing
            if not alarm.get_repeat_days():
                alarm.enabled = False
                await session.commit()
                self._remove_job(f"alarm:{alarm_id}")

        await self._publish("clock.fired", {
            "kind": "alarm",
            "id": alarm_id,
            "label": label,
        })
        await self._publish("voice.speak", {"text": f"Alarm: {label}"})
        await self._publish("clock.changed", {"kind": "alarm"})

    async def _fire_reminder(self, reminder_id: str) -> None:
        async with self._session_factory() as session:
            reminder = await session.get(ClockReminder, reminder_id)
            if reminder is None or reminder.fired:
                return
            label = reminder.label or "Reminder"
            reminder.fired = True
            await session.commit()

        await self._publish("clock.fired", {
            "kind": "reminder",
            "id": reminder_id,
            "label": label,
        })
        await self._publish("voice.speak", {"text": f"Reminder: {label}"})
        await self._publish("clock.changed", {"kind": "reminder"})

    async def _fire_timer(self, timer_id: str) -> None:
        async with self._session_factory() as session:
            timer = await session.get(ClockTimer, timer_id)
            if timer is None or timer.state != "running":
                return
            label = timer.label or "Timer"
            timer.state = "finished"
            await session.commit()

        await self._publish("clock.fired", {
            "kind": "timer",
            "id": timer_id,
            "label": label,
        })
        await self._publish("voice.speak", {"text": f"{label} finished"})
        await self._publish("clock.changed", {"kind": "timer"})

    # ── Alarm CRUD ──────────────────────────────────────────────────────────

    async def list_alarms(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(ClockAlarm))).scalars().all()
            return [_alarm_to_dict(a) for a in rows]

    async def create_alarm(
        self,
        hour: int,
        minute: int,
        label: str = "",
        repeat_days: list[int] | None = None,
        snooze_minutes: int = 0,
        sound: str = "default",
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            alarm = ClockAlarm(
                id=str(uuid.uuid4()),
                hour=hour,
                minute=minute,
                label=label,
                snooze_minutes=snooze_minutes,
                sound=sound,
                enabled=True,
            )
            alarm.set_repeat_days(repeat_days or [])
            session.add(alarm)
            await session.commit()
            await session.refresh(alarm)
            self._register_alarm_job(alarm)
            data = _alarm_to_dict(alarm)
        await self._publish("clock.changed", {"kind": "alarm"})
        return data

    async def update_alarm(self, alarm_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            alarm = await session.get(ClockAlarm, alarm_id)
            if alarm is None:
                return None
            if "label" in patch:
                alarm.label = patch["label"]
            if "hour" in patch:
                alarm.hour = int(patch["hour"])
            if "minute" in patch:
                alarm.minute = int(patch["minute"])
            if "repeat_days" in patch:
                alarm.set_repeat_days(list(patch["repeat_days"] or []))
            if "enabled" in patch:
                alarm.enabled = bool(patch["enabled"])
            if "snooze_minutes" in patch:
                alarm.snooze_minutes = int(patch["snooze_minutes"])
            if "sound" in patch:
                alarm.sound = patch["sound"]
            await session.commit()
            await session.refresh(alarm)
            # Re-register
            self._remove_job(f"alarm:{alarm_id}")
            if alarm.enabled:
                self._register_alarm_job(alarm)
            data = _alarm_to_dict(alarm)
        await self._publish("clock.changed", {"kind": "alarm"})
        return data

    async def delete_alarm(self, alarm_id: str) -> bool:
        async with self._session_factory() as session:
            alarm = await session.get(ClockAlarm, alarm_id)
            if alarm is None:
                return False
            await session.delete(alarm)
            await session.commit()
        self._remove_job(f"alarm:{alarm_id}")
        self._ringing_alarms.discard(alarm_id)
        await self._publish("clock.changed", {"kind": "alarm"})
        return True

    async def snooze_alarm(self, alarm_id: str) -> bool:
        async with self._session_factory() as session:
            alarm = await session.get(ClockAlarm, alarm_id)
            if alarm is None:
                return False
            minutes = alarm.snooze_minutes or 9
        self._ringing_alarms.discard(alarm_id)
        if self._scheduler:
            fire_at = _utcnow() + timedelta(minutes=minutes)
            self._scheduler.add_job(
                self._fire_alarm,
                DateTrigger(run_date=fire_at),
                id=f"snooze:{alarm_id}",
                kwargs={"alarm_id": alarm_id},
                replace_existing=True,
            )
        await self._publish("clock.changed", {"kind": "alarm"})
        return True

    async def dismiss_alarm(self, alarm_id: str) -> bool:
        self._ringing_alarms.discard(alarm_id)
        self._remove_job(f"snooze:{alarm_id}")
        await self._publish("clock.changed", {"kind": "alarm"})
        return True

    # ── Timer CRUD ──────────────────────────────────────────────────────────

    async def list_timers(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(ClockTimer))).scalars().all()
            return [_timer_to_dict(t) for t in rows]

    async def create_timer(self, duration_sec: int, label: str = "") -> dict[str, Any]:
        async with self._session_factory() as session:
            now = _utcnow()
            timer = ClockTimer(
                id=str(uuid.uuid4()),
                label=label,
                duration_sec=duration_sec,
                started_at=now,
                state="running",
            )
            session.add(timer)
            await session.commit()
            await session.refresh(timer)
            self._register_timer_job(timer.id, now + timedelta(seconds=duration_sec))
            data = _timer_to_dict(timer)
        await self._publish("clock.changed", {"kind": "timer"})
        return data

    async def pause_timer(self, timer_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            timer = await session.get(ClockTimer, timer_id)
            if timer is None or timer.state != "running" or timer.started_at is None:
                return None
            elapsed = (_utcnow() - _as_aware(timer.started_at)).total_seconds()
            remaining = max(0, int(timer.duration_sec - elapsed))
            timer.paused_remaining_sec = remaining
            timer.state = "paused"
            await session.commit()
            await session.refresh(timer)
            data = _timer_to_dict(timer)
        self._remove_job(f"timer:{timer_id}")
        await self._publish("clock.changed", {"kind": "timer"})
        return data

    async def resume_timer(self, timer_id: str) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            timer = await session.get(ClockTimer, timer_id)
            if timer is None or timer.state != "paused":
                return None
            remaining = timer.paused_remaining_sec or 0
            now = _utcnow()
            timer.started_at = now
            timer.duration_sec = remaining
            timer.paused_remaining_sec = None
            timer.state = "running"
            await session.commit()
            await session.refresh(timer)
            self._register_timer_job(timer_id, now + timedelta(seconds=remaining))
            data = _timer_to_dict(timer)
        await self._publish("clock.changed", {"kind": "timer"})
        return data

    async def delete_timer(self, timer_id: str) -> bool:
        async with self._session_factory() as session:
            timer = await session.get(ClockTimer, timer_id)
            if timer is None:
                return False
            await session.delete(timer)
            await session.commit()
        self._remove_job(f"timer:{timer_id}")
        await self._publish("clock.changed", {"kind": "timer"})
        return True

    # ── Reminder CRUD ───────────────────────────────────────────────────────

    async def list_reminders(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(ClockReminder))).scalars().all()
            return [_reminder_to_dict(r) for r in rows]

    async def create_reminder(
        self, due_at: datetime, label: str = "",
    ) -> dict[str, Any]:
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        async with self._session_factory() as session:
            reminder = ClockReminder(
                id=str(uuid.uuid4()),
                label=label,
                due_at=due_at,
                fired=False,
            )
            session.add(reminder)
            await session.commit()
            await session.refresh(reminder)
            self._register_reminder_job(reminder)
            data = _reminder_to_dict(reminder)
        await self._publish("clock.changed", {"kind": "reminder"})
        return data

    async def delete_reminder(self, reminder_id: str) -> bool:
        async with self._session_factory() as session:
            reminder = await session.get(ClockReminder, reminder_id)
            if reminder is None:
                return False
            await session.delete(reminder)
            await session.commit()
        self._remove_job(f"reminder:{reminder_id}")
        await self._publish("clock.changed", {"kind": "reminder"})
        return True

    # ── World clock ─────────────────────────────────────────────────────────

    async def list_cities(self) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ClockWorldCity).order_by(ClockWorldCity.sort_order)
                )
            ).scalars().all()
            return [_city_to_dict(c) for c in rows]

    async def add_city(self, label: str, tz_name: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            existing = (await session.execute(select(ClockWorldCity))).scalars().all()
            sort_order = max((c.sort_order for c in existing), default=-1) + 1
            city = ClockWorldCity(
                id=str(uuid.uuid4()),
                label=label,
                tz_name=tz_name,
                sort_order=sort_order,
            )
            session.add(city)
            await session.commit()
            await session.refresh(city)
            data = _city_to_dict(city)
        await self._publish("clock.changed", {"kind": "world_city"})
        return data

    async def delete_city(self, city_id: str) -> bool:
        async with self._session_factory() as session:
            city = await session.get(ClockWorldCity, city_id)
            if city is None:
                return False
            await session.delete(city)
            await session.commit()
        await self._publish("clock.changed", {"kind": "world_city"})
        return True

    # ── Stopwatch (singleton row id=1) ──────────────────────────────────────

    async def _get_stopwatch(self, session: Any) -> ClockStopwatch:
        sw = await session.get(ClockStopwatch, 1)
        if sw is None:
            sw = ClockStopwatch(id=1, state="idle", elapsed_ms=0)
            session.add(sw)
            await session.commit()
            await session.refresh(sw)
        return sw

    async def get_stopwatch(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            sw = await self._get_stopwatch(session)
            return _stopwatch_to_dict(sw)

    async def stopwatch_start(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            sw = await self._get_stopwatch(session)
            if sw.state != "running":
                sw.state = "running"
                sw.started_at = _utcnow()
            await session.commit()
            await session.refresh(sw)
            data = _stopwatch_to_dict(sw)
        await self._publish("clock.changed", {"kind": "stopwatch"})
        return data

    async def stopwatch_pause(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            sw = await self._get_stopwatch(session)
            if sw.state == "running" and sw.started_at is not None:
                run_ms = int((_utcnow() - _as_aware(sw.started_at)).total_seconds() * 1000)
                sw.elapsed_ms += run_ms
                sw.started_at = None
                sw.state = "paused"
            await session.commit()
            await session.refresh(sw)
            data = _stopwatch_to_dict(sw)
        await self._publish("clock.changed", {"kind": "stopwatch"})
        return data

    async def stopwatch_lap(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            sw = await self._get_stopwatch(session)
            total = sw.elapsed_ms
            if sw.state == "running" and sw.started_at is not None:
                total += int((_utcnow() - _as_aware(sw.started_at)).total_seconds() * 1000)
            laps = sw.get_laps()
            laps.append(total)
            sw.set_laps(laps)
            await session.commit()
            await session.refresh(sw)
            data = _stopwatch_to_dict(sw)
        await self._publish("clock.changed", {"kind": "stopwatch"})
        return data

    async def stopwatch_reset(self) -> dict[str, Any]:
        async with self._session_factory() as session:
            sw = await self._get_stopwatch(session)
            sw.state = "idle"
            sw.started_at = None
            sw.elapsed_ms = 0
            sw.set_laps([])
            await session.commit()
            await session.refresh(sw)
            data = _stopwatch_to_dict(sw)
        await self._publish("clock.changed", {"kind": "stopwatch"})
        return data

    # ── Snapshot for widget ─────────────────────────────────────────────────

    async def get_state(self) -> dict[str, Any]:
        """Compact snapshot used by the dashboard widget."""
        alarms = await self.list_alarms()
        timers = await self.list_timers()
        reminders = await self.list_reminders()

        # Next-firing alarm: cheapest = next APScheduler run_time of alarm:* jobs
        next_alarm: dict[str, Any] | None = None
        if self._scheduler:
            soonest_run = None
            soonest_id: str | None = None
            for job in self._scheduler.get_jobs():
                if not job.id.startswith("alarm:"):
                    continue
                if job.next_run_time is None:
                    continue
                if soonest_run is None or job.next_run_time < soonest_run:
                    soonest_run = job.next_run_time
                    soonest_id = job.id.split(":", 1)[1]
            if soonest_id:
                match = next((a for a in alarms if a["id"] == soonest_id), None)
                if match:
                    next_alarm = {
                        **match,
                        "next_run": soonest_run.isoformat() if soonest_run else None,
                    }

        active_timers = [t for t in timers if t["state"] == "running"]
        pending_reminders = [r for r in reminders if not r["fired"]]
        ringing = sorted(self._ringing_alarms)

        return {
            "next_alarm": next_alarm,
            "active_timers": active_timers,
            "pending_reminders_count": len(pending_reminders),
            "ringing_alarms": ringing,
        }


# ── Serializers ─────────────────────────────────────────────────────────────


def _alarm_to_dict(a: ClockAlarm) -> dict[str, Any]:
    return {
        "id": a.id,
        "label": a.label,
        "hour": a.hour,
        "minute": a.minute,
        "repeat_days": a.get_repeat_days(),
        "enabled": a.enabled,
        "snooze_minutes": a.snooze_minutes,
        "sound": a.sound,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _timer_to_dict(t: ClockTimer) -> dict[str, Any]:
    remaining = t.duration_sec
    if t.state == "running" and t.started_at is not None:
        elapsed = (_utcnow() - _as_aware(t.started_at)).total_seconds()
        remaining = max(0, int(t.duration_sec - elapsed))
    elif t.state == "paused":
        remaining = t.paused_remaining_sec or 0
    elif t.state == "finished":
        remaining = 0
    return {
        "id": t.id,
        "label": t.label,
        "duration_sec": t.duration_sec,
        "remaining_sec": remaining,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "state": t.state,
    }


def _reminder_to_dict(r: ClockReminder) -> dict[str, Any]:
    return {
        "id": r.id,
        "label": r.label,
        "due_at": r.due_at.isoformat() if r.due_at else None,
        "fired": r.fired,
    }


def _city_to_dict(c: ClockWorldCity) -> dict[str, Any]:
    return {
        "id": c.id,
        "label": c.label,
        "tz_name": c.tz_name,
        "sort_order": c.sort_order,
    }


def _stopwatch_to_dict(sw: ClockStopwatch) -> dict[str, Any]:
    return {
        "state": sw.state,
        "started_at": sw.started_at.isoformat() if sw.started_at else None,
        "elapsed_ms": sw.elapsed_ms,
        "laps": sw.get_laps(),
    }
