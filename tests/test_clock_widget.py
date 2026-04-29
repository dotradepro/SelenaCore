"""tests/test_clock_widget.py — clock widget endpoint + config endpoints."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_module(state: dict) -> tuple[FastAPI, "ClockModule"]:  # noqa: F821
    from system_modules.clock.module import ClockModule

    mod = ClockModule()
    fake_svc = AsyncMock()
    fake_svc.get_state = AsyncMock(return_value=state)
    mod._service = fake_svc

    app = FastAPI()
    app.include_router(mod.get_router(), prefix="")
    return app, mod


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point config_writer at a tmp YAML so POST tests don't touch /opt."""
    cfg_path = tmp_path / "core.yaml"
    import core.config_writer as cw
    monkeypatch.setattr(cw, "_CONFIG_PATH", cfg_path)
    return cfg_path


# ── Widget state payload ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_widget_payload_shape_empty(isolated_config):
    app, _ = _make_module({
        "next_alarm": None,
        "active_timers": [],
        "pending_reminders_count": 0,
        "ringing_alarms": [],
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/widget/data/state")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {
        "now_iso", "timezone", "format", "style",
        "next_alarm", "active_timers_count",
        "pending_reminders_count", "ringing_alarms_count",
    }
    assert body["next_alarm"] is None
    assert body["active_timers_count"] == 0
    assert body["pending_reminders_count"] == 0
    assert body["ringing_alarms_count"] == 0
    assert body["style"] == "digital"
    assert body["format"] == "24h"


@pytest.mark.asyncio
async def test_widget_payload_next_alarm(isolated_config):
    app, _ = _make_module({
        "next_alarm": {"hour": 6, "minute": 30, "label": "Wake up", "next_run": "2026-04-30T06:30:00+00:00"},
        "active_timers": [],
        "pending_reminders_count": 0,
        "ringing_alarms": [],
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/widget/data/state")).json()
    assert body["next_alarm"]["hour"] == 6
    assert body["next_alarm"]["minute"] == 30
    assert body["next_alarm"]["label"] == "Wake up"


@pytest.mark.asyncio
async def test_widget_payload_ringing_count(isolated_config):
    app, _ = _make_module({
        "next_alarm": None,
        "active_timers": [],
        "pending_reminders_count": 0,
        "ringing_alarms": ["alarm-1", "alarm-2"],
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/widget/data/state")).json()
    assert body["ringing_alarms_count"] == 2


@pytest.mark.asyncio
async def test_widget_payload_active_timer_count(isolated_config):
    app, _ = _make_module({
        "next_alarm": None,
        "active_timers": [{"id": "t1", "state": "running"}],
        "pending_reminders_count": 0,
        "ringing_alarms": [],
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/widget/data/state")).json()
    assert body["active_timers_count"] == 1


@pytest.mark.asyncio
async def test_widget_payload_pending_reminder_count(isolated_config):
    app, _ = _make_module({
        "next_alarm": None,
        "active_timers": [],
        "pending_reminders_count": 3,
        "ringing_alarms": [],
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/widget/data/state")).json()
    assert body["pending_reminders_count"] == 3


# ── Config endpoints ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_widget_config_get_defaults(isolated_config):
    app, _ = _make_module({"next_alarm": None, "active_timers": [], "pending_reminders_count": 0, "ringing_alarms": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/widget/data/config")
    assert r.status_code == 200
    body = r.json()
    assert body["style"] == "digital"
    assert body["format"] == "24h"
    assert isinstance(body["timezone"], str) and body["timezone"]


@pytest.mark.asyncio
async def test_widget_config_post_persists(isolated_config: Path):
    app, _ = _make_module({"next_alarm": None, "active_timers": [], "pending_reminders_count": 0, "ringing_alarms": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/widget/data/config", json={
            "style": "analog", "format": "12h", "timezone": "Europe/Kyiv",
        })
        assert r.status_code == 200
        assert r.json() == {"style": "analog", "format": "12h", "timezone": "Europe/Kyiv"}

        r2 = await c.get("/widget/data/config")
        assert r2.json() == {"style": "analog", "format": "12h", "timezone": "Europe/Kyiv"}

    assert isolated_config.exists()
    import yaml
    saved = yaml.safe_load(isolated_config.read_text())
    assert saved["clock"]["style"] == "analog"
    assert saved["clock"]["format"] == "12h"
    assert saved["clock"]["timezone"] == "Europe/Kyiv"


@pytest.mark.asyncio
async def test_widget_config_post_validates_timezone(isolated_config):
    app, _ = _make_module({"next_alarm": None, "active_timers": [], "pending_reminders_count": 0, "ringing_alarms": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/widget/data/config", json={"timezone": "Mars/Olympus"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_widget_config_post_validates_style(isolated_config):
    app, _ = _make_module({"next_alarm": None, "active_timers": [], "pending_reminders_count": 0, "ringing_alarms": []})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/widget/data/config", json={"style": "banana"})
    assert r.status_code == 400
