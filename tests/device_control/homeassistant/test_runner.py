"""Runner tests — use an in-memory SQLite DB so we exercise the real
SQLAlchemy flow (transactions, flush, idempotent re-run). Side-effect
callables (publish, watcher, entity-change) are captured in lists so we
can assert on what got invoked.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.registry.models import Base, Device
from system_modules.device_control.importers.homeassistant import runner
from system_modules.device_control.importers.homeassistant.types import HADevice


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db_factory():
    """In-memory SQLite + SQLAlchemy session factory, same shape as prod
    uses (async_sessionmaker). Schema recreated per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest_asyncio.fixture
async def db_devices(db_factory):
    """Direct DB read helper for assertions."""
    async def _read_all() -> list[Device]:
        async with db_factory() as s:
            res = await s.execute(select(Device))
            return list(res.scalars())
    return _read_all


def _ha(integration: str, **extra) -> HADevice:
    base = dict(
        id=extra.pop("id", f"d-{integration}"),
        name=extra.pop("name", f"{integration}-device"),
        area=extra.pop("area", None),
        integration=integration,
        entry_id="entry-1",
        entry_data=extra.pop("entry_data", {}),
        entry_options={},
    )
    base.update(extra)
    return HADevice(**base)


class _Capture:
    """Record every side-effect invocation so tests can assert on them."""
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.watched: list[str] = []
        self.notified: list[tuple[str, str, str]] = []

    async def publish(self, topic, payload):
        self.published.append((topic, payload))

    async def add_watcher(self, device_id):
        self.watched.append(device_id)

    async def on_entity_changed(self, kind, dev_id, action):
        self.notified.append((kind, dev_id, action))


# ── Green-path creation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_creates_rows_for_green_devices(db_factory, db_devices):
    cap = _Capture()
    devices = [
        _ha("esphome", id="d1", area="Kitchen",
            entry_data={"host": "192.168.1.10", "port": 6053}),
        _ha("hue", id="d2", area="Bedroom",
            entry_data={"host": "192.168.1.254", "api_key": "tok"},
            identifiers=[["hue", "bridge/light-5"]]),
    ]
    result = await runner.run(
        devices=devices,
        selected_ids=["d1", "d2"],
        context={},
        import_id="imp-A",
        db_session_factory=db_factory,
        publish=cap.publish,
        add_watcher=cap.add_watcher,
        on_entity_changed=cap.on_entity_changed,
    )

    assert len(result.created) == 2
    assert not result.skipped
    assert not result.failed

    rows = await db_devices()
    assert len(rows) == 2
    protocols = sorted(r.protocol for r in rows)
    assert protocols == ["esphome", "philips_hue"]

    # HA stamp wired through meta.
    for r in rows:
        meta = json.loads(r.meta)
        assert meta["ha_import"]["import_id"] == "imp-A"
        assert meta["ha_import"]["ha_device_id"] in ("d1", "d2")
        assert "imported_at" in meta["ha_import"]

    # Side effects fired exactly once per device.
    assert len(cap.published) == 2
    assert len(cap.watched) == 2
    assert len(cap.notified) == 2
    for topic, payload in cap.published:
        assert topic == "device.registered"
        assert payload["source"] == "ha_import"
        assert payload["import_id"] == "imp-A"


@pytest.mark.asyncio
async def test_run_uses_area_as_location(db_factory, db_devices):
    devices = [_ha("esphome", id="d1", area="Attic",
                   entry_data={"host": "1.2.3.4"})]
    await runner.run(
        devices=devices,
        selected_ids=["d1"],
        context={},
        import_id="imp-B",
        db_session_factory=db_factory,
    )
    rows = await db_devices()
    assert rows[0].location == "Attic"


# ── Selection filtering ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_ignores_unselected_devices(db_factory, db_devices):
    devices = [
        _ha("esphome", id="d1", entry_data={"host": "1.1.1.1"}),
        _ha("esphome", id="d2", entry_data={"host": "2.2.2.2"}),
    ]
    result = await runner.run(
        devices=devices,
        selected_ids=["d1"],
        context={},
        import_id="imp-C",
        db_session_factory=db_factory,
    )
    assert len(result.created) == 1
    rows = await db_devices()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_run_skips_non_green_results_without_creating_rows(db_factory, db_devices):
    devices = [
        _ha("tuya", id="d1", identifiers=[["tuya", "bf1"]]),   # needs_user_input (no ctx)
        _ha("zwave_js", id="d2", identifiers=[["zwave_js", "n1"]]),  # unsupported
    ]
    result = await runner.run(
        devices=devices,
        selected_ids=["d1", "d2"],
        context={},
        import_id="imp-D",
        db_session_factory=db_factory,
    )
    assert not result.created
    assert len(result.skipped) == 2
    assert not (await db_devices())


# ── Idempotency ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_re_running_does_not_duplicate_rows(db_factory, db_devices):
    devices = [_ha("esphome", id="d1", entry_data={"host": "1.1.1.1"})]
    await runner.run(
        devices=devices,
        selected_ids=["d1"],
        context={},
        import_id="imp-1",
        db_session_factory=db_factory,
    )
    second = await runner.run(
        devices=devices,
        selected_ids=["d1"],
        context={},
        import_id="imp-2",
        db_session_factory=db_factory,
    )
    assert not second.created
    assert len(second.skipped) == 1
    assert second.skipped[0]["reason"] == "already imported"
    rows = await db_devices()
    assert len(rows) == 1   # still just the one


# ── Rollback ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rollback_deletes_only_rows_from_that_import(db_factory, db_devices):
    devices = [
        _ha("esphome", id="d1", entry_data={"host": "1.1.1.1"}),
        _ha("esphome", id="d2", entry_data={"host": "2.2.2.2"}),
    ]
    # Two separate imports.
    await runner.run(devices=[devices[0]], selected_ids=["d1"], context={},
                     import_id="imp-keep", db_session_factory=db_factory)
    await runner.run(devices=[devices[1]], selected_ids=["d2"], context={},
                     import_id="imp-drop", db_session_factory=db_factory)

    cap = _Capture()
    deleted = await runner.rollback(
        import_id="imp-drop",
        db_session_factory=db_factory,
        publish=cap.publish,
    )
    assert len(deleted) == 1
    rows = await db_devices()
    assert len(rows) == 1
    assert json.loads(rows[0].meta)["ha_import"]["import_id"] == "imp-keep"

    # Rollback publishes a device.deleted per removed row.
    assert cap.published and cap.published[0][0] == "device.deleted"


@pytest.mark.asyncio
async def test_rollback_is_idempotent(db_factory):
    out = await runner.rollback(import_id="never-existed",
                                db_session_factory=db_factory)
    assert out == []


# ── Tuya context passthrough ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tuya_context_produces_tuya_local_row(db_factory, db_devices):
    devices = [_ha("tuya", id="d1", name="Bulb",
                   identifiers=[["tuya", "bf123"]])]
    ctx = {
        "tuya_devices_by_id": {
            "bf123": {"id": "bf123", "local_key": "abc", "version": "3.3",
                      "category": "dj", "product_name": "RGB bulb"},
        },
    }
    result = await runner.run(
        devices=devices,
        selected_ids=["d1"],
        context=ctx,
        import_id="imp-T",
        db_session_factory=db_factory,
    )
    assert len(result.created) == 1
    rows = await db_devices()
    meta = json.loads(rows[0].meta)
    assert rows[0].protocol == "tuya_local"
    assert meta["tuya"]["local_key"] == "abc"
    assert meta["tuya"]["device_id"] == "bf123"
