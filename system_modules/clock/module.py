"""system_modules/clock/module.py — ClockModule (SystemModule).

In-process system module — alarms, timers, reminders, world clock, stopwatch.
Mounted at /api/ui/modules/clock/ by the Plugin Manager.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule

from .service import ClockService
from .voice_handler import ClockVoiceHandler

logger = logging.getLogger(__name__)


# ── Pydantic request bodies ────────────────────────────────────────────────


class AlarmCreateBody(BaseModel):
    hour: int
    minute: int
    label: str = ""
    repeat_days: list[int] = []
    enabled: bool = True
    snooze_minutes: int = 9
    sound: str = "default"


class AlarmPatchBody(BaseModel):
    hour: int | None = None
    minute: int | None = None
    label: str | None = None
    repeat_days: list[int] | None = None
    enabled: bool | None = None
    snooze_minutes: int | None = None
    sound: str | None = None


class TimerCreateBody(BaseModel):
    duration_sec: int
    label: str = ""


class ReminderCreateBody(BaseModel):
    due_at: str  # ISO 8601
    label: str = ""


class CityCreateBody(BaseModel):
    label: str
    tz_name: str


class ClockModule(SystemModule):
    name = "clock"

    OWNED_INTENTS = [
        "clock.set_alarm",
        "clock.set_timer",
        "clock.set_reminder",
        "clock.list_alarms",
        "clock.cancel_alarm",
        "clock.stop_alarm",
        "clock.cancel_timer",
    ]

    _OWNED_INTENT_META: dict[str, dict] = {
        "clock.set_alarm": dict(
            noun_class="CLOCK", verb="create", priority=100,
            description="Set an alarm for a specific wall-clock time (HH:MM), optionally repeating.",
        ),
        "clock.set_timer": dict(
            noun_class="CLOCK", verb="create", priority=100,
            description="Start a countdown timer for N seconds / minutes / hours.",
        ),
        "clock.set_reminder": dict(
            noun_class="CLOCK", verb="create", priority=100,
            description="Schedule a one-off reminder with a label at a specific future time.",
        ),
        "clock.list_alarms": dict(
            noun_class="CLOCK", verb="query", priority=100,
            description="Read out the list of currently-enabled alarms.",
        ),
        "clock.cancel_alarm": dict(
            noun_class="CLOCK", verb="cancel", priority=100,
            description="Cancel / delete an existing alarm by label or position.",
        ),
        "clock.stop_alarm": dict(
            noun_class="CLOCK", verb="cancel", priority=100,
            description="Silence the alarm that is ringing right now (snooze or dismiss).",
        ),
        "clock.cancel_timer": dict(
            noun_class="CLOCK", verb="cancel", priority=100,
            description="Stop a running timer before it fires.",
        ),
    }

    def __init__(self) -> None:
        super().__init__()
        self._service: ClockService | None = None
        self._voice: ClockVoiceHandler | None = None

    async def start(self) -> None:
        self._service = ClockService(self._session_factory, self.publish)
        await self._service.start()
        self._voice = ClockVoiceHandler(self._service)

        self.subscribe(["voice.intent"], self._on_event)

        # Register clock.* intents (static catalog). Idempotent.
        await self._claim_intent_ownership()

        await self.publish("module.started", {"name": self.name})
        logger.info("Clock module started")

    async def stop(self) -> None:
        if self._service:
            await self._service.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})
        logger.info("Clock module stopped")

    # ── EventBus handler ────────────────────────────────────────────────────

    async def _on_event(self, event: Any) -> None:
        if event.type != "voice.intent":
            return
        intent = event.payload.get("intent", "")
        if not intent.startswith("clock."):
            return
        if self._voice is None:
            return
        ctx = await self._voice.handle(intent, event.payload.get("params", {}))
        if ctx:
            await self.speak_action(intent, ctx)

    # ── Router ──────────────────────────────────────────────────────────────

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        # Widget / settings HTML
        svc._register_html_routes(router, __file__)
        svc._register_health_endpoint(router)

        def _service() -> ClockService:
            if svc._service is None:
                raise HTTPException(status_code=503, detail="ClockService not started")
            return svc._service

        # ── Snapshot for widget ────────────────────────────────────────────
        @router.get("/state")
        async def get_state() -> dict:
            return await _service().get_state()

        # ── Alarms ─────────────────────────────────────────────────────────
        @router.get("/alarms")
        async def list_alarms() -> dict:
            return {"alarms": await _service().list_alarms()}

        @router.post("/alarms")
        async def create_alarm(body: AlarmCreateBody) -> dict:
            if not (0 <= body.hour <= 23 and 0 <= body.minute <= 59):
                raise HTTPException(status_code=400, detail="Invalid time")
            for d in body.repeat_days:
                if not (0 <= d <= 6):
                    raise HTTPException(status_code=400, detail="repeat_days must be 0-6")
            return await _service().create_alarm(
                hour=body.hour,
                minute=body.minute,
                label=body.label,
                repeat_days=body.repeat_days,
                snooze_minutes=body.snooze_minutes,
                sound=body.sound,
            )

        @router.patch("/alarms/{alarm_id}")
        async def update_alarm(alarm_id: str, body: AlarmPatchBody) -> dict:
            patch = {k: v for k, v in body.model_dump().items() if v is not None}
            updated = await _service().update_alarm(alarm_id, patch)
            if updated is None:
                raise HTTPException(status_code=404, detail="Alarm not found")
            return updated

        @router.delete("/alarms/{alarm_id}")
        async def delete_alarm(alarm_id: str) -> dict:
            ok = await _service().delete_alarm(alarm_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Alarm not found")
            return {"ok": True}

        @router.post("/alarms/{alarm_id}/snooze")
        async def snooze_alarm(alarm_id: str) -> dict:
            ok = await _service().snooze_alarm(alarm_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Alarm not found")
            return {"ok": True}

        @router.post("/alarms/{alarm_id}/dismiss")
        async def dismiss_alarm(alarm_id: str) -> dict:
            await _service().dismiss_alarm(alarm_id)
            return {"ok": True}

        # ── Timers ─────────────────────────────────────────────────────────
        @router.get("/timers")
        async def list_timers() -> dict:
            return {"timers": await _service().list_timers()}

        @router.post("/timers")
        async def create_timer(body: TimerCreateBody) -> dict:
            if body.duration_sec <= 0:
                raise HTTPException(status_code=400, detail="duration_sec must be > 0")
            return await _service().create_timer(body.duration_sec, body.label)

        @router.post("/timers/{timer_id}/pause")
        async def pause_timer(timer_id: str) -> dict:
            data = await _service().pause_timer(timer_id)
            if data is None:
                raise HTTPException(status_code=400, detail="Cannot pause timer")
            return data

        @router.post("/timers/{timer_id}/resume")
        async def resume_timer(timer_id: str) -> dict:
            data = await _service().resume_timer(timer_id)
            if data is None:
                raise HTTPException(status_code=400, detail="Cannot resume timer")
            return data

        @router.delete("/timers/{timer_id}")
        async def delete_timer(timer_id: str) -> dict:
            ok = await _service().delete_timer(timer_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Timer not found")
            return {"ok": True}

        # ── Reminders ──────────────────────────────────────────────────────
        @router.get("/reminders")
        async def list_reminders() -> dict:
            return {"reminders": await _service().list_reminders()}

        @router.post("/reminders")
        async def create_reminder(body: ReminderCreateBody) -> dict:
            try:
                due = datetime.fromisoformat(body.due_at.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid due_at format")
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            return await _service().create_reminder(due, body.label)

        @router.delete("/reminders/{reminder_id}")
        async def delete_reminder(reminder_id: str) -> dict:
            ok = await _service().delete_reminder(reminder_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Reminder not found")
            return {"ok": True}

        # ── World clock ────────────────────────────────────────────────────
        @router.get("/world-clock")
        async def list_cities() -> dict:
            return {"cities": await _service().list_cities()}

        @router.post("/world-clock")
        async def add_city(body: CityCreateBody) -> dict:
            if not body.label or not body.tz_name:
                raise HTTPException(status_code=400, detail="label and tz_name required")
            return await _service().add_city(body.label, body.tz_name)

        @router.delete("/world-clock/{city_id}")
        async def delete_city(city_id: str) -> dict:
            ok = await _service().delete_city(city_id)
            if not ok:
                raise HTTPException(status_code=404, detail="City not found")
            return {"ok": True}

        # ── Stopwatch ──────────────────────────────────────────────────────
        @router.get("/stopwatch")
        async def get_stopwatch() -> dict:
            return await _service().get_stopwatch()

        @router.post("/stopwatch/start")
        async def stopwatch_start() -> dict:
            return await _service().stopwatch_start()

        @router.post("/stopwatch/pause")
        async def stopwatch_pause() -> dict:
            return await _service().stopwatch_pause()

        @router.post("/stopwatch/lap")
        async def stopwatch_lap() -> dict:
            return await _service().stopwatch_lap()

        @router.post("/stopwatch/reset")
        async def stopwatch_reset() -> dict:
            return await _service().stopwatch_reset()

        return router
