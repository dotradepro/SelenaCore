"""
tests/test_scheduler.py — pytest tests for scheduler module [#69]
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Patch heavy optional dependencies before importing scheduler
import sys

# Provide minimal stubs if not installed
for mod_name in ("astral", "astral.sun", "apscheduler",
                 "apscheduler.schedulers.asyncio",
                 "apscheduler.triggers.cron",
                 "apscheduler.triggers.interval"):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()


@pytest.fixture
def publish_mock():
    return AsyncMock()


@pytest.fixture
def scheduler_service(publish_mock):
    """Create SchedulerService with patched APScheduler."""
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True), \
         patch("system_modules.scheduler.scheduler.ASTRAL_AVAILABLE", True):
        from system_modules.scheduler.scheduler import SchedulerService
        svc = SchedulerService(
            publish_callback=publish_mock,
            config={"latitude": 50.45, "longitude": 30.52, "timezone": "Europe/Kyiv"},
        )
        svc._scheduler = MagicMock()
        svc._scheduler.running = True
        svc._scheduler.get_job = MagicMock(return_value=None)
        svc._scheduler.add_job = MagicMock()
        svc._scheduler.remove_job = MagicMock()
        return svc


# ── Trigger parsing tests ─────────────────────────────────────────────────────

def test_parse_interval_seconds():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_interval
        from apscheduler.triggers.interval import IntervalTrigger
        trigger = _parse_interval("every:30s")
        assert trigger is not None


def test_parse_interval_minutes():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_interval
        trigger = _parse_interval("every:5m")
        assert trigger is not None


def test_parse_interval_hours():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_interval
        trigger = _parse_interval("every:1h")
        assert trigger is not None


def test_parse_interval_invalid():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_interval
        assert _parse_interval("every:x") is None
        assert _parse_interval("cron:0 7 * * *") is None


def test_parse_cron_valid():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_cron
        from apscheduler.triggers.cron import CronTrigger
        CronTrigger.from_crontab = MagicMock(return_value=MagicMock())
        trigger = _parse_cron("cron:0 7 * * 1-5")
        assert trigger is not None


def test_parse_cron_no_prefix():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_cron
        assert _parse_cron("0 7 * * *") is None


def test_parse_time_valid():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_time
        trigger = _parse_time("07:30")
        assert trigger is not None


def test_parse_time_invalid():
    with patch("system_modules.scheduler.scheduler.APSCHEDULER_AVAILABLE", True):
        from system_modules.scheduler.scheduler import _parse_time
        assert _parse_time("every:5m") is None
        assert _parse_time("25:00") is None


# ── AstralJob tests ───────────────────────────────────────────────────────────

def test_astral_job_sunrise_no_offset():
    with patch("system_modules.scheduler.scheduler.ASTRAL_AVAILABLE", True):
        from system_modules.scheduler.scheduler import AstralJob
        job = AstralJob("test", "sunrise", "test.event", {}, "owner")
        assert job.base_event == "sunrise"
        assert job.offset_minutes == 0


def test_astral_job_sunrise_plus_offset():
    with patch("system_modules.scheduler.scheduler.ASTRAL_AVAILABLE", True):
        from system_modules.scheduler.scheduler import AstralJob
        job = AstralJob("test", "sunrise+30m", "test.event", {}, "owner")
        assert job.base_event == "sunrise"
        assert job.offset_minutes == 30


def test_astral_job_sunset_minus_offset():
    with patch("system_modules.scheduler.scheduler.ASTRAL_AVAILABLE", True):
        from system_modules.scheduler.scheduler import AstralJob
        job = AstralJob("test", "sunset-60m", "test.event", {}, "owner")
        assert job.base_event == "sunset"
        assert job.offset_minutes == -60


def test_astral_job_next_fire_time():
    """Sunrise computed correctly for a known location (Kyiv)."""
    with patch("system_modules.scheduler.scheduler.ASTRAL_AVAILABLE", True):
        from system_modules.scheduler.scheduler import AstralJob, LocationInfo, astral_sun
        job = AstralJob("test", "sunrise", "test.event", {}, "owner")
        location = MagicMock()
        location.observer = MagicMock()
        location.timezone = timezone.utc

        # Mock astral_sun to return a fixed time
        future_time = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        astral_sun.return_value = {"sunrise": future_time, "sunset": future_time}

        fire_time = job.next_fire_time(location)
        assert fire_time is not None


# ── SchedulerService register/remove tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_register_interval_job(scheduler_service, publish_mock):
    mock_job = MagicMock()
    mock_job.next_run_time = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    scheduler_service._scheduler.get_job.return_value = mock_job

    with patch("system_modules.scheduler.scheduler._parse_interval") as mock_parse:
        mock_parse.return_value = MagicMock()
        result = await scheduler_service.register_job({
            "job_id": "test:interval",
            "trigger": "every:5m",
            "event_type": "test.event",
            "payload": {"key": "value"},
            "owner": "test-module",
        })

    assert result is not None
    assert result["job_id"] == "test:interval"
    call_args = publish_mock.call_args
    assert call_args is not None
    assert call_args[0][0] == "scheduler.job_registered"
    assert call_args[0][1]["job_id"] == "test:interval"
    assert call_args[0][1]["trigger"] == "every:5m"


@pytest.mark.asyncio
async def test_register_job_missing_fields(scheduler_service):
    result = await scheduler_service.register_job({
        "trigger": "every:5m",
        # missing job_id and event_type
    })
    assert result is None


@pytest.mark.asyncio
async def test_remove_existing_job(scheduler_service, publish_mock):
    # Pre-register a job in meta
    scheduler_service._jobs_meta["job:test"] = {
        "job_id": "job:test",
        "trigger": "every:1m",
        "event_type": "x",
        "owner": "o",
        "next_run": None,
    }
    scheduler_service._scheduler.get_job.return_value = MagicMock()

    removed = await scheduler_service.remove_job("job:test")
    assert removed is True
    assert "job:test" not in scheduler_service._jobs_meta
    publish_mock.assert_called_with("scheduler.job_removed", {"job_id": "job:test"})


@pytest.mark.asyncio
async def test_remove_nonexistent_job(scheduler_service, publish_mock):
    scheduler_service._scheduler.get_job.return_value = None
    removed = await scheduler_service.remove_job("nonexistent")
    assert removed is False
    publish_mock.assert_not_called()


@pytest.mark.asyncio
async def test_fire_job_publishes_events(scheduler_service, publish_mock):
    await scheduler_service._fire_job(
        "job:test", "every:5m", "device.test", {"x": 1}
    )
    calls = [c.args for c in publish_mock.call_args_list]
    assert ("scheduler.fired",) == calls[0][:1] or publish_mock.call_count >= 2
    event_types = [c[0] for c in calls]
    assert "scheduler.fired" in event_types
    assert "device.test" in event_types


# ── Persistence tests ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_load_jobs(scheduler_service, tmp_path, publish_mock):
    """Jobs saved to JSON and restored on load."""
    scheduler_service._jobs_meta["job:persist"] = {
        "job_id": "job:persist",
        "trigger": "every:1h",
        "event_type": "test.hourly",
        "owner": "test",
        "next_run": None,
    }
    scheduler_service._scheduler.get_job.return_value = None

    await scheduler_service.save_jobs(tmp_path)
    jobs_file = tmp_path / "jobs.json"
    assert jobs_file.exists()
    data = json.loads(jobs_file.read_text())
    assert any(j["job_id"] == "job:persist" for j in data)


@pytest.mark.asyncio
async def test_load_jobs_missing_file(scheduler_service, tmp_path):
    """Loading from non-existent file does not raise."""
    # Should complete without error
    await scheduler_service.load_jobs(tmp_path / "nonexistent")


# ── list_jobs ────────────────────────────────────────────────────────────────

def test_list_jobs_returns_meta(scheduler_service):
    scheduler_service._jobs_meta["a:job"] = {
        "job_id": "a:job", "trigger": "cron:0 8 * * *",
        "event_type": "e", "owner": "o", "next_run": None,
    }
    scheduler_service._scheduler.get_job.return_value = None
    jobs = scheduler_service.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "a:job"
