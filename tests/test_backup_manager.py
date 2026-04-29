"""
tests/test_backup_manager.py — unit tests for system_modules/backup_manager.

Covers the functional surface the user signed off on for phase 1:
  * categorized backup creation (core / core+secrets)
  * vault_key exclusion
  * SQLite Online Backup snapshot
  * separate retention pools for `selena_backup_*` and `selena_prerestore_*`
  * settings persistence + defaults merge
  * path-traversal protection in restore
  * scheduler register / unregister round-trip
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture()
def fake_root(tmp_path, monkeypatch):
    """Lay out a fake / hierarchy under tmp_path and reroute the module's
    constants there so we can exercise the real archive writer without
    touching the host filesystem."""
    var_lib = tmp_path / "var" / "lib" / "selena"
    etc = tmp_path / "etc" / "selena"
    secure = tmp_path / "secure"
    backups = var_lib / "backups"
    for d in (var_lib, etc, secure, backups):
        d.mkdir(parents=True, exist_ok=True)

    db_path = var_lib / "selena.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (k TEXT, v TEXT)")
    conn.execute("INSERT INTO t VALUES ('hello', 'world')")
    conn.commit()
    conn.close()

    (var_lib / "widget_layout.json").write_text(json.dumps({"widgets": []}))
    (var_lib / "modules").mkdir(exist_ok=True)
    (var_lib / "modules" / "scheduler").mkdir(exist_ok=True)
    (var_lib / "modules" / "scheduler" / "jobs.json").write_text("[]")
    (etc / "core.yaml").write_text("core: {}\n")
    (secure / "vault_key").write_bytes(b"\x00" * 32)
    (secure / "google_oauth.json").write_text(json.dumps({"token": "xyz"}))

    state_dir = tmp_path / "state"
    monkeypatch.setenv("BACKUP_DEST", str(backups))
    monkeypatch.setenv("BACKUP_MANAGER_STATE_DIR", str(state_dir))
    monkeypatch.setenv("PRERESTORE_RETENTION", "3")

    # Local-backup reads BACKUP_DEST / PRERESTORE_RETENTION from os.environ on
    # every call, so a fresh import is no longer required to pick up env vars.
    from system_modules.backup_manager import local_backup as lb
    from system_modules.backup_manager import state as st

    # Patch CATEGORY_PATHS / EXCLUDE_PATHS / SQLITE_PATH to point at fake_root.
    st.CATEGORY_PATHS["core"] = [
        str(db_path),
        str(var_lib / "widget_layout.json"),
        str(var_lib / "modules"),
        str(etc),
    ]
    st.CATEGORY_PATHS["secrets"] = [str(secure)]
    st.EXCLUDE_PATHS[:] = [str(secure / "vault_key")]
    lb.SQLITE_PATH = db_path
    # `from .state import EXCLUDE_PATHS` in local_backup binds a separate ref.
    lb.EXCLUDE_PATHS = st.EXCLUDE_PATHS

    yield {
        "root": tmp_path,
        "var_lib": var_lib,
        "etc": etc,
        "secure": secure,
        "backups": backups,
        "db": db_path,
        "lb": lb,
        "st": st,
    }


def test_create_backup_core_only(fake_root):
    lb = fake_root["lb"]
    st = fake_root["st"]
    archive = asyncio.run(lb.create_backup(
        paths=st.resolve_paths({"core": True, "secrets": False}),
    ))
    assert archive.exists()
    assert archive.name.startswith("selena_backup_")
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("selena.db") for n in names)
    assert any(n.endswith("widget_layout.json") for n in names)
    # secrets disabled — must not be present
    assert not any("/secure/" in n for n in names)


def test_create_backup_with_secrets_excludes_vault_key(fake_root):
    lb = fake_root["lb"]
    st = fake_root["st"]
    archive = asyncio.run(lb.create_backup(
        paths=st.resolve_paths({"core": True, "secrets": True}),
    ))
    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("google_oauth.json") for n in names)
    assert not any(n.endswith("vault_key") for n in names)


def test_sqlite_snapshot_is_consistent(fake_root):
    """Online backup should produce a fully readable SQLite file."""
    lb = fake_root["lb"]
    st = fake_root["st"]
    archive = asyncio.run(lb.create_backup(
        paths=st.resolve_paths({"core": True, "secrets": False}),
    ))
    with tempfile.TemporaryDirectory() as out:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(out, filter="data")
        # The DB inside the archive lives at <out><abs db path>
        extracted = Path(out + str(fake_root["db"]))
        assert extracted.exists()
        c = sqlite3.connect(extracted)
        rows = list(c.execute("SELECT k, v FROM t"))
        c.close()
        assert ("hello", "world") in rows


def test_retention_pools_are_separate(fake_root, monkeypatch):
    lb = fake_root["lb"]
    st = fake_root["st"]
    paths = st.resolve_paths({"core": True, "secrets": False})

    # Force monotonic timestamps so back-to-back create_backup calls
    # produce distinct filenames within the same second.
    counter = {"n": 0}

    def fake_ts():
        counter["n"] += 1
        return f"20260101T00{counter['n']:04d}Z"

    monkeypatch.setattr(lb, "_timestamp", fake_ts)

    async def run():
        for _ in range(7):
            await lb.create_backup(prefix=lb.REGULAR_PREFIX, paths=paths, max_backups=3)
        for _ in range(4):
            await lb.create_backup(prefix=lb.PRERESTORE_PREFIX, paths=paths)

    asyncio.run(run())

    backups = sorted(fake_root["backups"].glob("*.tar.gz"))
    regular = [b for b in backups if b.name.startswith(lb.REGULAR_PREFIX)]
    prerestore = [b for b in backups if b.name.startswith(lb.PRERESTORE_PREFIX)]
    assert len(regular) == 3
    assert len(prerestore) == 3


def test_list_backups_classifies_kind(fake_root, monkeypatch):
    lb = fake_root["lb"]
    st = fake_root["st"]
    paths = st.resolve_paths({"core": True, "secrets": False})

    counter = {"n": 0}
    def fake_ts():
        counter["n"] += 1
        return f"20260101T00{counter['n']:04d}Z"
    monkeypatch.setattr(lb, "_timestamp", fake_ts)

    async def run():
        await lb.create_backup(prefix=lb.REGULAR_PREFIX, paths=paths, max_backups=5)
        await lb.create_backup(prefix=lb.PRERESTORE_PREFIX, paths=paths)

    asyncio.run(run())
    rows = lb.list_backups(fake_root["backups"])
    kinds = {r["kind"] for r in rows}
    assert kinds == {"regular", "prerestore"}


def test_settings_load_save_defaults(fake_root):
    st = fake_root["st"]
    s = st.load_settings()
    assert s["categories"] == {"core": True, "secrets": True}
    assert s["max_backups"] == 5
    assert s["schedule"]["enabled"] is False

    saved = st.save_settings({
        "categories": {"core": False, "secrets": False},  # core forced True
        "schedule": {"enabled": True, "trigger": "every:5m"},
        "max_backups": 7,
    })
    assert saved["categories"]["core"] is True
    assert saved["categories"]["secrets"] is False
    assert saved["schedule"]["trigger"] == "every:5m"
    assert saved["max_backups"] == 7

    again = st.load_settings()
    assert again == saved


def test_settings_rejects_out_of_range_max_backups(fake_root):
    st = fake_root["st"]
    saved = st.save_settings({"max_backups": 9999})
    assert saved["max_backups"] == 5  # default kept


def test_restore_blocks_path_traversal(fake_root, tmp_path):
    lb = fake_root["lb"]
    bad = tmp_path / "evil.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        # Member with absolute escape path
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = 4
        import io
        tar.addfile(info, io.BytesIO(b"woot"))

    target = tmp_path / "target_root"
    target.mkdir()
    ok = asyncio.run(lb.restore_backup(bad, str(target)))
    # restore_backup catches the ValueError → returns False
    assert ok is False
    assert not (tmp_path / "escape.txt").exists()


def test_module_register_schedule_event(fake_root):
    sys.modules.pop("system_modules.backup_manager.module", None)
    from system_modules.backup_manager.module import (
        BackupManagerModule,
        SCHEDULER_FIRE_EVENT,
        SCHEDULER_JOB_ID,
    )

    mod = BackupManagerModule()
    bus = MagicMock()
    bus.subscribe_direct = MagicMock(return_value="sub-1")
    bus.unsubscribe_direct = MagicMock()
    bus.publish = AsyncMock()
    mod.setup(bus, MagicMock())

    fake_root["st"].save_settings({
        "schedule": {"enabled": True, "trigger": "every:1h"},
    })

    asyncio.run(mod._sync_schedule(fake_root["st"].load_settings()))
    register_calls = [
        c for c in bus.publish.await_args_list
        if c.kwargs.get("type") == "scheduler.register"
        or (c.args and c.args[0] == "scheduler.register")
    ]
    # publish() is called as positional kwargs via SystemModule.publish — inspect kwargs
    types_published = [c.kwargs.get("type") for c in bus.publish.await_args_list]
    assert "scheduler.register" in types_published


def test_module_unregister_when_disabled(fake_root):
    sys.modules.pop("system_modules.backup_manager.module", None)
    from system_modules.backup_manager.module import BackupManagerModule

    mod = BackupManagerModule()
    bus = MagicMock()
    bus.subscribe_direct = MagicMock(return_value="sub-2")
    bus.unsubscribe_direct = MagicMock()
    bus.publish = AsyncMock()
    mod.setup(bus, MagicMock())

    fake_root["st"].save_settings({
        "schedule": {"enabled": False, "trigger": "every:1h"},
    })

    asyncio.run(mod._sync_schedule(fake_root["st"].load_settings()))
    types_published = [c.kwargs.get("type") for c in bus.publish.await_args_list]
    assert "scheduler.unregister" in types_published
