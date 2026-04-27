"""tests/test_update_manager.py — pytest tests for update_manager module."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_manager(
    tmp_path: Path,
    *,
    publish: AsyncMock | None = None,
    current_version: str = "0.4.142-rc",
    channel: str = "rc",
):
    from system_modules.update_manager.updater import UpdateManager

    install_dir = tmp_path / "install"
    install_dir.mkdir()
    backup_dir = tmp_path / "backups"
    staging = tmp_path / "staging"
    cache = tmp_path / "cache"
    state_file = tmp_path / "state.json"
    return UpdateManager(
        publish_event_cb=publish or AsyncMock(),
        current_version=current_version,
        repo="test-owner/test-repo",
        channel=channel,
        install_dir=install_dir,
        backup_dir=backup_dir,
        staging_dir=staging,
        cache_dir=cache,
        state_file=state_file,
        check_interval_sec=9999,
    )


def _make_release(tag: str = "v0.4.150-rc", *, prerelease: bool = True):
    from system_modules.update_manager.sources.github_releases import Release

    return Release(
        tag=tag,
        version=tag.lstrip("v"),
        name=f"Selena {tag}",
        body="### Changed\n- thing 1\n- thing 2",
        published_at="2026-04-25T10:00:00Z",
        prerelease=prerelease,
        tarball_url=f"https://example.test/{tag}.tar.gz",
        sha256_url=f"https://example.test/{tag}.tar.gz.sha256",
        meta_url=None,
        size_bytes=1024,
    )


# ── Version comparison ────────────────────────────────────────────────────────


class TestVersionComparison:
    def test_newer_version_gt(self):
        from system_modules.update_manager.updater import UpdateManager

        assert UpdateManager._version_gt("1.2.0", "1.1.0") is True

    def test_same_version_not_gt(self):
        from system_modules.update_manager.updater import UpdateManager

        assert UpdateManager._version_gt("1.0.0", "1.0.0") is False

    def test_older_version_not_gt(self):
        from system_modules.update_manager.updater import UpdateManager

        assert UpdateManager._version_gt("0.9.0", "1.0.0") is False

    def test_v_prefix_stripped(self):
        from system_modules.update_manager.updater import UpdateManager

        assert UpdateManager._version_gt("v1.2.0", "v1.1.0") is True

    def test_pre_release_suffix_ignored(self):
        from system_modules.update_manager.updater import UpdateManager

        # pre-release/build metadata after - or + is dropped before compare
        assert UpdateManager._version_gt("0.4.143-rc", "0.4.142-rc+0644435") is True
        assert UpdateManager._version_gt("0.4.142", "0.4.142+abc") is False

    def test_invalid_version_handled(self):
        from system_modules.update_manager.updater import UpdateManager

        result = UpdateManager._version_gt("bad", "1.0.0")
        assert isinstance(result, bool)


# ── Status snapshot ───────────────────────────────────────────────────────────


class TestStatus:
    def test_initial_status(self, tmp_path):
        mgr = _make_manager(tmp_path, current_version="0.4.142-rc")
        s = mgr.get_status()
        assert s["current_version"] == "0.4.142-rc"
        assert s["state"] == "idle"
        assert s["update_available"] is False
        assert s["latest_version"] is None
        assert s["channel"] == "rc"
        assert s["repo"] == "test-owner/test-repo"
        assert s["has_backup"] is False


# ── Check (mocks GithubReleasesSource.fetch_releases) ─────────────────────────


class TestCheck:
    @pytest.mark.asyncio
    async def test_check_update_available_publishes_event(self, tmp_path):
        publish = AsyncMock()
        mgr = _make_manager(tmp_path, publish=publish, current_version="0.4.142-rc")

        newer = _make_release("v0.4.150-rc")
        with patch.object(
            mgr._source, "fetch_releases", AsyncMock(return_value=[newer])
        ):
            result = await mgr.check()

        assert result["update_available"] is True
        assert result["tag"] == "v0.4.150-rc"
        from system_modules.update_manager.updater import UpdateState

        assert mgr.state == UpdateState.UPDATE_AVAILABLE

        published_types = [c.args[0] for c in publish.call_args_list]
        assert "update.available" in published_types

    @pytest.mark.asyncio
    async def test_check_up_to_date(self, tmp_path):
        mgr = _make_manager(tmp_path, current_version="0.4.150-rc")
        same = _make_release("v0.4.150-rc")
        with patch.object(mgr._source, "fetch_releases", AsyncMock(return_value=[same])):
            result = await mgr.check()
        assert result["update_available"] is False
        from system_modules.update_manager.updater import UpdateState

        assert mgr.state == UpdateState.UP_TO_DATE

    @pytest.mark.asyncio
    async def test_check_no_releases(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr._source, "fetch_releases", AsyncMock(return_value=[])):
            result = await mgr.check()
        assert result["update_available"] is False
        assert "no releases" in result.get("reason", "")

    @pytest.mark.asyncio
    async def test_check_propagates_fetch_error(self, tmp_path):
        from system_modules.update_manager.updater import UpdateState

        mgr = _make_manager(tmp_path)
        with patch.object(
            mgr._source, "fetch_releases", AsyncMock(side_effect=RuntimeError("boom"))
        ):
            with pytest.raises(RuntimeError, match="boom"):
                await mgr.check()
        assert mgr.state == UpdateState.ERROR
        assert mgr._error is not None


# ── Listing ───────────────────────────────────────────────────────────────────


class TestListing:
    @pytest.mark.asyncio
    async def test_list_versions_returns_dicts(self, tmp_path):
        mgr = _make_manager(tmp_path, current_version="0.4.142-rc")
        rel = _make_release("v0.4.150-rc")
        with patch.object(mgr._source, "fetch_releases", AsyncMock(return_value=[rel])):
            await mgr.check()
        listed = mgr.list_versions()
        assert len(listed) == 1
        assert listed[0]["tag"] == "v0.4.150-rc"
        assert listed[0]["prerelease"] is True

    @pytest.mark.asyncio
    async def test_get_version_details_by_tag(self, tmp_path):
        mgr = _make_manager(tmp_path)
        rel = _make_release("v0.4.150-rc")
        with patch.object(mgr._source, "fetch_releases", AsyncMock(return_value=[rel])):
            await mgr.check()
        d = mgr.get_version_details("v0.4.150-rc")
        assert d is not None and d["version"] == "0.4.150-rc"
        # also accepts unprefixed version
        d2 = mgr.get_version_details("0.4.150-rc")
        assert d2 is not None
        # unknown tag returns None
        assert mgr.get_version_details("nope") is None


# ── Install (mocks download / sha256 / dispatch_external) ─────────────────────


class TestInstall:
    @pytest.mark.asyncio
    async def test_install_version_dispatches_external(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer
        from system_modules.update_manager.updater import UpdateState

        mgr = _make_manager(tmp_path, current_version="0.4.142-rc")
        rel = _make_release("v0.4.150-rc")
        mgr._releases = [rel]

        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", tmp_path / "install_lock")
        monkeypatch.setattr(installer, "UPDATE_FLAG_PATH", tmp_path / "no_flag")
        monkeypatch.setattr(
            "system_modules.update_manager.updater.INSTALL_LOCK_PATH",
            tmp_path / "install_lock",
        )

        async def _fake_download(release, dest, progress_cb=None, chunk_size=65536):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"fake-tarball")
            return dest

        async def _fake_verify(path, url):
            return "f" * 64

        def _fake_extract(path, staging):
            staging.mkdir(parents=True, exist_ok=True)
            (staging / "marker").write_text("ok")
            return staging

        async def _fake_meta(release):
            return {"min_python": "3.8", "needs_db_migration": False}

        run_calls = []

        def _fake_run(cmd, **kwargs):
            run_calls.append(cmd)

            class _R:
                returncode = 0

            return _R()

        with patch.object(mgr._source, "fetch_meta", AsyncMock(side_effect=_fake_meta)), \
             patch.object(mgr._source, "download_tarball", AsyncMock(side_effect=_fake_download)), \
             patch.object(mgr._source, "verify_sha256", AsyncMock(side_effect=_fake_verify)), \
             patch.object(mgr._source, "extract", side_effect=_fake_extract), \
             patch("system_modules.update_manager.installer.subprocess.run", side_effect=_fake_run):
            result = await mgr.install_version("v0.4.150-rc")

        assert result["ok"] is True
        assert result["unit"].startswith("selena-update-")
        assert mgr.state == UpdateState.APPLYING
        # dispatch_external invoked with sudo systemd-run + tag + install
        assert run_calls and run_calls[0][0] == "sudo"
        assert "systemd-run" in run_calls[0]
        assert "v0.4.150-rc" in run_calls[0]
        assert "install" in run_calls[0]

    @pytest.mark.asyncio
    async def test_install_unknown_tag(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="unknown release tag"):
            await mgr.install_version("v999.0.0")

    @pytest.mark.asyncio
    async def test_install_sha256_mismatch_publishes_failed(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer
        from system_modules.update_manager.updater import UpdateState

        publish = AsyncMock()
        mgr = _make_manager(tmp_path, publish=publish, current_version="0.4.142-rc")
        mgr._releases = [_make_release("v0.4.150-rc")]

        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", tmp_path / "lock_path")
        monkeypatch.setattr(installer, "UPDATE_FLAG_PATH", tmp_path / "no_flag")
        monkeypatch.setattr(
            "system_modules.update_manager.updater.INSTALL_LOCK_PATH",
            tmp_path / "lock_path",
        )

        async def _fake_download(release, dest, progress_cb=None, chunk_size=65536):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return dest

        async def _fake_verify(*args, **kwargs):
            raise ValueError("sha256 mismatch: expected aa got bb")

        async def _fake_meta(*a, **kw):
            return {"min_python": "3.8"}

        with patch.object(mgr._source, "fetch_meta", AsyncMock(side_effect=_fake_meta)), \
             patch.object(mgr._source, "download_tarball", AsyncMock(side_effect=_fake_download)), \
             patch.object(mgr._source, "verify_sha256", AsyncMock(side_effect=_fake_verify)):
            with pytest.raises(ValueError, match="sha256 mismatch"):
                await mgr.install_version("v0.4.150-rc")

        assert mgr.state == UpdateState.ERROR
        published_types = [c.args[0] for c in publish.call_args_list]
        assert "update.failed" in published_types


# ── Cloud-triggered apply_update_from_url ─────────────────────────────────────


class TestApplyUpdateFromUrl:
    @pytest.mark.asyncio
    async def test_validates_url_and_sha(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="requires url"):
            await mgr.apply_update_from_url("", "abc", "1.0.0")
        with pytest.raises(ValueError, match="64-char"):
            await mgr.apply_update_from_url("http://x", "shorthash", "1.0.0")

    @pytest.mark.asyncio
    async def test_dispatches_external_on_match(self, tmp_path, monkeypatch):
        import hashlib

        from system_modules.update_manager import installer

        publish = AsyncMock()
        mgr = _make_manager(tmp_path, publish=publish)

        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", tmp_path / "lock_path")
        monkeypatch.setattr(installer, "UPDATE_FLAG_PATH", tmp_path / "no_flag")
        monkeypatch.setattr(
            "system_modules.update_manager.updater.INSTALL_LOCK_PATH",
            tmp_path / "lock_path",
        )

        payload = b"hello-tarball"
        sha = hashlib.sha256(payload).hexdigest()

        async def _fake_stream(url, dest):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(payload)

        run_calls = []

        def _fake_run(cmd, **kwargs):
            run_calls.append(cmd)

            class _R:
                returncode = 0

            return _R()

        with patch(
            "system_modules.update_manager.updater.UpdateManager._stream_url_to_file",
            AsyncMock(side_effect=_fake_stream),
        ), patch(
            "system_modules.update_manager.sources.github_releases.GithubReleasesSource.extract"
        ) as mock_extract, patch(
            "system_modules.update_manager.installer.subprocess.run",
            side_effect=_fake_run,
        ):
            mock_extract.return_value = tmp_path / "staging" / "1.0.0"
            r = await mgr.apply_update_from_url("https://x/y.tar.gz", sha, "1.0.0")

        assert r["ok"] is True
        assert run_calls and "install" in run_calls[0]


# ── Channel persistence ───────────────────────────────────────────────────────


class TestChannel:
    def test_channel_default_when_no_state(self, tmp_path):
        mgr = _make_manager(tmp_path, channel="rc")
        assert mgr.channel == "rc"

    def test_set_channel_persists(self, tmp_path):
        mgr = _make_manager(tmp_path, channel="rc")
        mgr.set_channel("stable")
        assert mgr.channel == "stable"
        assert json.loads(mgr._state_file.read_text())["channel"] == "stable"

    def test_invalid_channel_rejected(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match="invalid channel"):
            mgr.set_channel("nightly")

    def test_set_check_interval_min(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with pytest.raises(ValueError, match=">= 60"):
            mgr.set_check_interval(30)
        mgr.set_check_interval(120)
        assert mgr._check_interval == 120


# ── Rollback ──────────────────────────────────────────────────────────────────


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_dispatches_external(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer

        mgr = _make_manager(tmp_path)
        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", tmp_path / "lock_path")
        monkeypatch.setattr(
            "system_modules.update_manager.updater.INSTALL_LOCK_PATH",
            tmp_path / "lock_path",
        )

        run_calls = []

        def _fake_run(cmd, **kwargs):
            run_calls.append(cmd)

            class _R:
                returncode = 0

            return _R()

        with patch(
            "system_modules.update_manager.installer.subprocess.run",
            side_effect=_fake_run,
        ):
            r = await mgr.rollback()

        assert r["ok"] is True
        assert run_calls and "rollback" in run_calls[0]


# ── API endpoints (main.py FastAPI app) ───────────────────────────────────────


class TestUpdateAPI:
    @pytest.fixture
    def _app(self, tmp_path, monkeypatch):
        import system_modules.update_manager.main as um_main

        mgr = _make_manager(tmp_path)
        um_main._manager = mgr
        return um_main.app, mgr

    @pytest.mark.asyncio
    async def test_health(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, _ = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_status(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, mgr = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/status")
        assert r.status_code == 200
        body = r.json()
        assert body["repo"] == mgr._repo
        assert body["channel"] in ("rc", "stable")

    @pytest.mark.asyncio
    async def test_versions_empty(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, _ = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/versions")
        assert r.status_code == 200
        assert r.json()["versions"] == []

    @pytest.mark.asyncio
    async def test_install_requires_tag(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, _ = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/install", json={})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_config_invalid_channel(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, _ = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/config", json={"channel": "nightly"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_config_channel_persists(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, mgr = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/config", json={"channel": "stable"})
        assert r.status_code == 200
        assert mgr.channel == "stable"

    @pytest.mark.asyncio
    async def test_widget_served(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, _ = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/widget.html")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_served(self, _app):
        from httpx import ASGITransport, AsyncClient

        app, _ = _app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/settings.html")
        assert r.status_code == 200
