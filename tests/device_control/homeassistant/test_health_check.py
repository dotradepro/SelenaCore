"""Health check tests — stub the driver_factory so we never touch real
drivers, and verify the checker updates ``meta.ha_import.health`` and
returns a balanced HealthResult."""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.registry.models import Base, Device
from system_modules.device_control.importers.homeassistant import (
    health_check,
    runner,
)
from system_modules.device_control.importers.homeassistant.types import HADevice


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _ha(id_: str) -> HADevice:
    return HADevice(
        id=id_, name=f"esp-{id_}", area=None,
        integration="esphome", entry_id="e",
        entry_data={"host": f"10.0.0.{id_[-1]}"},
        entry_options={},
    )


# ── Driver fakes ──────────────────────────────────────────────────────────


class _OkDriver:
    def __init__(self, device_id, protocol, meta):
        self.device_id = device_id
        self.disconnected = False
    async def connect(self):   return None
    async def get_state(self): return {"on": False}
    async def disconnect(self):
        self.disconnected = True


class _FailingDriver:
    def __init__(self, device_id, protocol, meta):
        pass
    async def connect(self):
        raise ConnectionError("host unreachable")
    async def disconnect(self): pass


class _HangingDriver:
    def __init__(self, device_id, protocol, meta):
        pass
    async def connect(self):
        await asyncio.sleep(10)   # will trip the timeout
    async def disconnect(self): pass


# ── Tests ─────────────────────────────────────────────────────────────────


async def _seed(db_factory, n: int, import_id: str):
    await runner.run(
        devices=[_ha(f"d{i}") for i in range(n)],
        selected_ids=[f"d{i}" for i in range(n)],
        context={},
        import_id=import_id,
        db_session_factory=db_factory,
    )


@pytest.mark.asyncio
async def test_all_reachable_marks_every_row_reachable(db_factory):
    await _seed(db_factory, 3, "imp-A")
    result = await health_check.run(
        import_id="imp-A",
        db_session_factory=db_factory,
        driver_factory=lambda d, p, m: _OkDriver(d, p, m),
        timeout=1.0,
    )
    assert len(result.reachable) == 3
    assert not result.unreachable
    async with db_factory() as s:
        rows = (await s.execute(select(Device))).scalars().all()
    for r in rows:
        assert json.loads(r.meta)["ha_import"]["health"] == "reachable"


@pytest.mark.asyncio
async def test_unreachable_gets_reason_and_does_not_delete_row(db_factory):
    await _seed(db_factory, 1, "imp-B")
    result = await health_check.run(
        import_id="imp-B",
        db_session_factory=db_factory,
        driver_factory=lambda d, p, m: _FailingDriver(d, p, m),
        timeout=1.0,
    )
    assert not result.reachable
    assert len(result.unreachable) == 1
    assert "host unreachable" in result.unreachable[0]["reason"]

    async with db_factory() as s:
        rows = (await s.execute(select(Device))).scalars().all()
    assert len(rows) == 1   # NOT deleted
    meta = json.loads(rows[0].meta)
    assert meta["ha_import"]["health"] == "unreachable"
    assert "host unreachable" in meta["ha_import"]["health_reason"]


@pytest.mark.asyncio
async def test_timeout_is_enforced_per_device(db_factory):
    await _seed(db_factory, 1, "imp-C")
    result = await health_check.run(
        import_id="imp-C",
        db_session_factory=db_factory,
        driver_factory=lambda d, p, m: _HangingDriver(d, p, m),
        timeout=0.05,   # 50 ms — faster than the 10 s hang
        concurrency=1,
    )
    assert len(result.unreachable) == 1
    assert "timeout" in result.unreachable[0]["reason"]


@pytest.mark.asyncio
async def test_unknown_import_id_returns_empty_result(db_factory):
    await _seed(db_factory, 1, "imp-real")
    result = await health_check.run(
        import_id="not-a-real-import",
        db_session_factory=db_factory,
        driver_factory=lambda d, p, m: _OkDriver(d, p, m),
    )
    assert not result.reachable and not result.unreachable


@pytest.mark.asyncio
async def test_only_probes_rows_from_matching_import(db_factory):
    """Seed two imports; health check for imp-1 must touch only its rows."""
    await _seed(db_factory, 2, "imp-1")
    # Seed another 1 under imp-2 — different ha_device_ids so idempotency
    # doesn't skip the writes.
    await runner.run(
        devices=[_ha("d9")],
        selected_ids=["d9"],
        context={},
        import_id="imp-2",
        db_session_factory=db_factory,
    )
    touched: list[str] = []
    class _Counter(_OkDriver):
        async def connect(self):
            touched.append(self.device_id)
    result = await health_check.run(
        import_id="imp-1",
        db_session_factory=db_factory,
        driver_factory=lambda d, p, m: _Counter(d, p, m),
        timeout=1.0,
    )
    assert len(result.reachable) == 2
    assert len(touched) == 2
