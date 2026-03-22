"""
system_modules/scheduler/scheduler.py — бизнес-логика планировщика задач

Поддерживаемые триггеры:
  cron:<expression>      стандартный cron (apscheduler)
  every:<N>s|m|h         периодический интервал
  sunrise[+/-<N>m]       на восходе солнца ± смещение
  sunset[+/-<N>m]        на закате ± смещение
  <HH:MM>                конкретное время каждый день (→ cron:0 <MM> <HH> * * *)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from astral import LocationInfo
    from astral.sun import sun as astral_sun

    ASTRAL_AVAILABLE = True
except ImportError:
    ASTRAL_AVAILABLE = False
    logger.warning("astral not installed — sunrise/sunset triggers disabled")

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.warning("apscheduler not installed — scheduler module will not function")


_INTERVAL_RE = re.compile(r"^every:(\d+)(s|m|h)$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
_ASTRAL_RE = re.compile(r"^(sunrise|sunset)([+-]\d+m)?$")


def _parse_interval(trigger_str: str) -> IntervalTrigger | None:
    """Parse 'every:5m' → APScheduler IntervalTrigger."""
    if not APSCHEDULER_AVAILABLE:
        return None
    m = _INTERVAL_RE.match(trigger_str)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2)
    seconds = {"s": value, "m": value * 60, "h": value * 3600}[unit]
    return IntervalTrigger(seconds=seconds)


def _parse_cron(trigger_str: str) -> CronTrigger | None:
    """Parse 'cron:0 7 * * 1-5' → APScheduler CronTrigger."""
    if not APSCHEDULER_AVAILABLE:
        return None
    if not trigger_str.startswith("cron:"):
        return None
    expr = trigger_str[5:]
    try:
        return CronTrigger.from_crontab(expr)
    except Exception as e:
        logger.error(f"Invalid cron expression '{expr}': {e}")
        return None


def _parse_time(trigger_str: str) -> CronTrigger | None:
    """Parse 'HH:MM' → CronTrigger for that time daily."""
    if not APSCHEDULER_AVAILABLE:
        return None
    m = _TIME_RE.match(trigger_str)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return CronTrigger(hour=hour, minute=minute)


class AstralJob:
    """Represents a sunrise/sunset based job that recalculates daily."""

    __slots__ = ("job_id", "trigger_str", "event_type", "payload", "owner",
                 "offset_minutes", "base_event")

    def __init__(
        self,
        job_id: str,
        trigger_str: str,
        event_type: str,
        payload: dict,
        owner: str,
    ) -> None:
        self.job_id = job_id
        self.trigger_str = trigger_str
        self.event_type = event_type
        self.payload = payload
        self.owner = owner

        m = _ASTRAL_RE.match(trigger_str)
        if not m:
            raise ValueError(f"Invalid astral trigger: {trigger_str}")
        self.base_event = m.group(1)  # "sunrise" or "sunset"
        offset_str = m.group(2) or "+0m"
        sign = 1 if offset_str[0] == "+" else -1
        self.offset_minutes = sign * int(offset_str[1:-1])

    def next_fire_time(self, location: LocationInfo | None) -> datetime | None:
        if not ASTRAL_AVAILABLE or location is None:
            return None
        try:
            s = astral_sun(location.observer, date=date.today(), tzinfo=location.timezone)
            base_time = s[self.base_event]
            fire_time = base_time + timedelta(minutes=self.offset_minutes)
            now = datetime.now(tz=fire_time.tzinfo)
            if fire_time <= now:
                # Already past today — schedule for tomorrow
                s = astral_sun(
                    location.observer,
                    date=date.today() + timedelta(days=1),
                    tzinfo=location.timezone,
                )
                base_time = s[self.base_event]
                fire_time = base_time + timedelta(minutes=self.offset_minutes)
            return fire_time
        except Exception as e:
            logger.error(f"Astral calculation failed for {self.job_id}: {e}")
            return None


class SchedulerService:
    """Central scheduler service managing all timed jobs."""

    def __init__(self, publish_callback: Any, config: dict) -> None:
        self._publish = publish_callback
        self._config = config
        self._scheduler: AsyncIOScheduler | None = None
        self._astral_jobs: dict[str, AstralJob] = {}
        self._jobs_meta: dict[str, dict] = {}  # job_id → meta info
        self._location: LocationInfo | None = None
        self._astral_recalc_task: asyncio.Task | None = None

    def _build_location(self) -> LocationInfo | None:
        if not ASTRAL_AVAILABLE:
            return None
        lat = self._config.get("latitude")
        lon = self._config.get("longitude")
        tz = self._config.get("timezone", "UTC")
        if lat is None or lon is None:
            return None
        try:
            return LocationInfo(
                name="home",
                region="",
                timezone=tz,
                latitude=float(lat),
                longitude=float(lon),
            )
        except Exception as e:
            logger.error(f"Failed to build location: {e}")
            return None

    async def start(self) -> None:
        if not APSCHEDULER_AVAILABLE:
            logger.error("apscheduler not available — scheduler disabled")
            return
        self._location = self._build_location()
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()

        # Daily recalculation of astral jobs at midnight
        self._scheduler.add_job(
            self._recalculate_astral_jobs,
            CronTrigger(hour=0, minute=1),
            id="__astral_recalc__",
            replace_existing=True,
        )
        logger.info("SchedulerService started")

    async def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
        logger.info("SchedulerService stopped")

    async def register_job(self, job_data: dict) -> dict | None:
        """Register a new scheduled job. Returns job meta or None on error."""
        job_id = job_data.get("job_id")
        trigger_str = job_data.get("trigger", "")
        event_type = job_data.get("event_type", "")
        payload = job_data.get("payload", {})
        owner = job_data.get("owner", "unknown")

        if not job_id or not trigger_str or not event_type:
            logger.error(f"register_job: missing required fields in {job_data}")
            return None

        if not APSCHEDULER_AVAILABLE or self._scheduler is None:
            logger.error("Scheduler not running")
            return None

        # Remove existing job with same ID
        await self.remove_job(job_id)

        try:
            if _ASTRAL_RE.match(trigger_str):
                return await self._register_astral_job(
                    job_id, trigger_str, event_type, payload, owner
                )

            aps_trigger = (
                _parse_interval(trigger_str)
                or _parse_cron(trigger_str)
                or _parse_time(trigger_str)
            )
            if aps_trigger is None:
                logger.error(f"Cannot parse trigger: {trigger_str}")
                return None

            self._scheduler.add_job(
                self._fire_job,
                aps_trigger,
                id=job_id,
                kwargs={"job_id": job_id, "trigger": trigger_str,
                        "event_type": event_type, "payload": payload},
                replace_existing=True,
            )
            aps_job = self._scheduler.get_job(job_id)
            next_run = aps_job.next_run_time.isoformat() if aps_job and aps_job.next_run_time else None

        except Exception as e:
            logger.error(f"Failed to register job {job_id}: {e}")
            return None

        meta = {
            "job_id": job_id,
            "trigger": trigger_str,
            "event_type": event_type,
            "owner": owner,
            "next_run": next_run,
        }
        self._jobs_meta[job_id] = meta
        logger.info(f"Job registered: {job_id} ({trigger_str}) next={next_run}")

        await self._publish("scheduler.job_registered", {
            "job_id": job_id,
            "trigger": trigger_str,
            "next_run": next_run,
        })
        return meta

    async def _register_astral_job(
        self,
        job_id: str,
        trigger_str: str,
        event_type: str,
        payload: dict,
        owner: str,
    ) -> dict | None:
        if not ASTRAL_AVAILABLE or self._location is None:
            logger.error("astral or location unavailable — cannot register astral job")
            return None

        astral_job = AstralJob(job_id, trigger_str, event_type, payload, owner)
        fire_time = astral_job.next_fire_time(self._location)
        if fire_time is None:
            return None

        self._astral_jobs[job_id] = astral_job
        self._scheduler.add_job(  # type: ignore[union-attr]
            self._fire_astral_job,
            "date",
            run_date=fire_time,
            id=job_id,
            kwargs={"job_id": job_id},
            replace_existing=True,
        )

        meta = {
            "job_id": job_id,
            "trigger": trigger_str,
            "event_type": event_type,
            "owner": owner,
            "next_run": fire_time.isoformat(),
        }
        self._jobs_meta[job_id] = meta
        logger.info(f"Astral job registered: {job_id} fires at {fire_time}")

        await self._publish("scheduler.job_registered", {
            "job_id": job_id,
            "trigger": trigger_str,
            "next_run": fire_time.isoformat(),
        })
        return meta

    async def _fire_job(
        self, job_id: str, trigger: str, event_type: str, payload: dict
    ) -> None:
        fired_at = datetime.utcnow().isoformat()
        logger.info(f"Job fired: {job_id} ({trigger})")
        await self._publish("scheduler.fired", {
            "job_id": job_id,
            "trigger": trigger,
            "fired_at": fired_at,
        })
        await self._publish(event_type, payload)

    async def _fire_astral_job(self, job_id: str) -> None:
        astral_job = self._astral_jobs.get(job_id)
        if not astral_job:
            return
        await self._fire_job(
            job_id, astral_job.trigger_str,
            astral_job.event_type, astral_job.payload
        )
        # Reschedule for tomorrow
        fire_time = astral_job.next_fire_time(self._location)
        if fire_time and self._scheduler:
            self._scheduler.add_job(
                self._fire_astral_job,
                "date",
                run_date=fire_time,
                id=job_id,
                kwargs={"job_id": job_id},
                replace_existing=True,
            )
            if job_id in self._jobs_meta:
                self._jobs_meta[job_id]["next_run"] = fire_time.isoformat()

    async def remove_job(self, job_id: str) -> bool:
        if self._scheduler and self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        self._astral_jobs.pop(job_id, None)
        removed = job_id in self._jobs_meta
        self._jobs_meta.pop(job_id, None)

        if removed:
            logger.info(f"Job removed: {job_id}")
            await self._publish("scheduler.job_removed", {"job_id": job_id})
        return removed

    def list_jobs(self) -> list[dict]:
        jobs = []
        for job_id, meta in self._jobs_meta.items():
            aps_job = self._scheduler.get_job(job_id) if self._scheduler else None
            next_run = meta.get("next_run")
            if aps_job and aps_job.next_run_time:
                next_run = aps_job.next_run_time.isoformat()
            jobs.append({**meta, "next_run": next_run})
        return jobs

    async def _recalculate_astral_jobs(self) -> None:
        """Called daily at 00:01 to reschedule sunrise/sunset jobs for new day."""
        logger.info("Recalculating astral jobs for today")
        self._location = self._build_location()
        for job_id, astral_job in list(self._astral_jobs.items()):
            fire_time = astral_job.next_fire_time(self._location)
            if fire_time and self._scheduler:
                self._scheduler.add_job(
                    self._fire_astral_job,
                    "date",
                    run_date=fire_time,
                    id=job_id,
                    kwargs={"job_id": job_id},
                    replace_existing=True,
                )
                if job_id in self._jobs_meta:
                    self._jobs_meta[job_id]["next_run"] = fire_time.isoformat()

    def update_config(self, new_config: dict) -> None:
        self._config = new_config
        self._location = self._build_location()

    async def save_jobs(self, data_dir: Path) -> None:
        """Persist jobs list to JSON for restart recovery."""
        data_dir.mkdir(parents=True, exist_ok=True)
        jobs_file = data_dir / "jobs.json"
        jobs_to_save = [
            {
                "job_id": meta["job_id"],
                "trigger": meta["trigger"],
                "event_type": meta["event_type"],
                "owner": meta["owner"],
                "payload": self._get_job_payload(meta["job_id"]),
            }
            for meta in self._jobs_meta.values()
        ]
        jobs_file.write_text(json.dumps(jobs_to_save, indent=2))
        logger.debug(f"Saved {len(jobs_to_save)} jobs to {jobs_file}")

    def _get_job_payload(self, job_id: str) -> dict:
        """Retrieve original payload from APScheduler job kwargs."""
        if self._scheduler is None:
            return {}
        aps_job = self._scheduler.get_job(job_id)
        if aps_job and aps_job.kwargs:
            return aps_job.kwargs.get("payload", {})
        astral_job = self._astral_jobs.get(job_id)
        if astral_job:
            return astral_job.payload
        return {}

    async def load_jobs(self, data_dir: Path) -> None:
        """Load persisted jobs from JSON after restart."""
        jobs_file = data_dir / "jobs.json"
        if not jobs_file.exists():
            return
        try:
            jobs = json.loads(jobs_file.read_text())
            for job in jobs:
                await self.register_job(job)
            logger.info(f"Loaded {len(jobs)} persisted jobs")
        except Exception as e:
            logger.error(f"Failed to load persisted jobs: {e}")
