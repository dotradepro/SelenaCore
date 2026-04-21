"""Tests for importers.plejd.run — in-memory SQLite, synthetic site."""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.registry.models import Base, Device
from system_modules.device_control.importers import plejd as plejd_importer
from system_modules.device_control.plejd.cloud import PlejdCloudDevice, PlejdSite


@pytest_asyncio.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _site(devices: list[PlejdCloudDevice], *, title: str = "Home") -> PlejdSite:
    return PlejdSite(
        site_id="sid-1",
        title=title,
        crypto_key=bytes(range(16)),
        devices=devices,
    )


class _VaultStub:
    def __init__(self) -> None:
        self.stored: list[tuple[str, bytes, str]] = []
    async def store(self, site_id, key, title):
        self.stored.append((site_id, key, title))


class _BusStub:
    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self.watched: list[str] = []
    async def publish(self, topic, payload):
        self.published.append((topic, payload))
    async def add_watcher(self, did):
        self.watched.append(did)


# ── Green path ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_creates_one_row_per_selected_output(db_factory):
    site = _site([
        PlejdCloudDevice("AA:BB:CC:DD:EE:01", 11, "Kitchen", "DIM-02", "Kitchen", True),
        PlejdCloudDevice("AA:BB:CC:DD:EE:02", 12, "Hall",    "REL-01", "Hall",    False),
    ])
    vault = _VaultStub()
    bus = _BusStub()

    result = await plejd_importer.run(
        site=site,
        selected_output_addresses=[11, 12],
        import_id="imp-A",
        db_session_factory=db_factory,
        store_site_key=vault.store,
        publish=bus.publish,
        add_watcher=bus.add_watcher,
    )
    assert len(result.created) == 2
    assert not result.skipped
    # Site key persisted exactly once regardless of device count.
    assert len(vault.stored) == 1
    assert vault.stored[0][0] == "sid-1"
    assert vault.stored[0][1] == bytes(range(16))

    async with db_factory() as s:
        rows = list((await s.execute(select(Device))).scalars())
    assert len(rows) == 2
    by_addr = {}
    for r in rows:
        meta = json.loads(r.meta)
        by_addr[meta["plejd"]["output_address"]] = (r, meta)

    dim_row, dim_meta = by_addr[11]
    assert dim_row.protocol == "plejd_native"
    assert dim_row.entity_type == "light"
    assert dim_row.location == "Kitchen"
    assert "brightness" in dim_row.get_capabilities()
    assert dim_meta["plejd"]["device_type"] == "DIM-02"
    assert dim_meta["plejd"]["dimmable"] is True
    assert dim_meta["plejd_import"]["import_id"] == "imp-A"

    rel_row, rel_meta = by_addr[12]
    assert rel_row.entity_type == "light"
    assert "brightness" not in rel_row.get_capabilities()
    assert rel_meta["plejd"]["dimmable"] is False

    assert len(bus.published) == 2
    assert all(t == "device.registered" for t, _ in bus.published)
    assert len(bus.watched) == 2


@pytest.mark.asyncio
async def test_run_skips_unselected(db_factory):
    site = _site([
        PlejdCloudDevice("AA:BB:CC:DD:EE:01", 1, "X", "DIM-02", None, True),
        PlejdCloudDevice("AA:BB:CC:DD:EE:02", 2, "Y", "DIM-02", None, True),
    ])
    result = await plejd_importer.run(
        site=site,
        selected_output_addresses=[1],
        import_id="imp-B",
        db_session_factory=db_factory,
    )
    assert len(result.created) == 1
    async with db_factory() as s:
        rows = list((await s.execute(select(Device))).scalars())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_run_is_idempotent_across_calls(db_factory):
    site = _site([
        PlejdCloudDevice("AA:BB:CC:DD:EE:01", 5, "Bulb", "DIM-02", None, True),
    ])
    await plejd_importer.run(
        site=site, selected_output_addresses=[5],
        import_id="imp-1", db_session_factory=db_factory,
    )
    second = await plejd_importer.run(
        site=site, selected_output_addresses=[5],
        import_id="imp-2", db_session_factory=db_factory,
    )
    assert not second.created
    assert len(second.skipped) == 1
    assert second.skipped[0]["reason"] == "already imported"
    async with db_factory() as s:
        rows = list((await s.execute(select(Device))).scalars())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_site_key_is_not_stored_in_device_meta(db_factory):
    """The crypto_key lives in secrets_vault only; leaking it into
    Device.meta would persist a plaintext key in the (unencrypted)
    registry DB."""
    site = _site([
        PlejdCloudDevice("AA:BB:CC:DD:EE:01", 3, "X", "DIM-02", None, True),
    ])
    await plejd_importer.run(
        site=site, selected_output_addresses=[3],
        import_id="imp-Q", db_session_factory=db_factory,
    )
    async with db_factory() as s:
        rows = list((await s.execute(select(Device))).scalars())
    meta = json.loads(rows[0].meta)
    assert "crypto_key" not in meta["plejd"]
    assert "key" not in meta["plejd"]
    # The raw bytes of the key must not appear anywhere in the meta JSON.
    assert bytes(range(16)).hex() not in rows[0].meta


@pytest.mark.asyncio
async def test_no_vault_write_if_nothing_created(db_factory):
    """If the site has no selected devices, we must not persist the
    site key — that would leave a zombie vault record for a site we
    never imported."""
    site = _site([
        PlejdCloudDevice("AA:BB:CC:DD:EE:01", 1, "X", "DIM-02", None, True),
    ])
    vault = _VaultStub()
    result = await plejd_importer.run(
        site=site, selected_output_addresses=[],   # nothing selected
        import_id="imp-E", db_session_factory=db_factory,
        store_site_key=vault.store,
    )
    assert not result.created
    assert not vault.stored
