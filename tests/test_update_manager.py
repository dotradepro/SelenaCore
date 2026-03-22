"""tests/test_update_manager.py — pytest tests for update_manager module"""
from __future__ import annotations

import hashlib
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_manager(publish=None, current_version="1.0.0", manifest_url="http://update.test/manifest.json"):
    from system_modules.update_manager.updater import UpdateManager
    return UpdateManager(
        publish_event_cb=publish or AsyncMock(),
        current_version=current_version,
        manifest_url=manifest_url,
        install_dir=tempfile.mkdtemp(),
        backup_dir=tempfile.mkdtemp() + "_backup",
        check_interval_sec=9999,
    )


def make_manifest_response(version: str, download_url: str = "http://update.test/pkg.zip", sha256: str = "") -> dict:
    return {
        "version": version,
        "download_url": download_url,
        "sha256": sha256,
        "notes": "Test release",
    }


def make_http_response(data: dict, status: int = 200):
    req = httpx.Request("GET", "http://update.test/manifest.json")
    return httpx.Response(status_code=status, json=data, request=req)


def make_zip_bytes(files: dict[str, bytes]) -> bytes:
    """Create a minimal zip archive in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


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

    def test_major_version_gt(self):
        from system_modules.update_manager.updater import UpdateManager
        assert UpdateManager._version_gt("2.0.0", "1.9.9") is True

    def test_prefix_v_stripped(self):
        from system_modules.update_manager.updater import UpdateManager
        assert UpdateManager._version_gt("v1.2.0", "v1.1.0") is True

    def test_invalid_version_handled(self):
        from system_modules.update_manager.updater import UpdateManager
        # Should not raise
        result = UpdateManager._version_gt("bad", "1.0.0")
        assert isinstance(result, bool)


# ── Check ──────────────────────────────────────────────────────────────────────

class TestCheck:
    @pytest.mark.asyncio
    async def test_check_update_available(self):
        publish = AsyncMock()
        mgr = make_manager(publish=publish, current_version="1.0.0")

        manifest = make_manifest_response("1.1.0")
        mock_resp = make_http_response(manifest)

        with patch("httpx.AsyncClient") as mc:
            instance = AsyncMock()
            mc.return_value.__aenter__.return_value = instance
            instance.get.return_value = mock_resp
            result = await mgr.check()

        assert result["update_available"] is True
        assert result["version"] == "1.1.0"

        calls = [c[0][0] for c in publish.call_args_list]
        assert "update.available" in calls

    @pytest.mark.asyncio
    async def test_check_up_to_date(self):
        mgr = make_manager(current_version="1.1.0")

        manifest = make_manifest_response("1.0.0")
        mock_resp = make_http_response(manifest)

        with patch("httpx.AsyncClient") as mc:
            instance = AsyncMock()
            mc.return_value.__aenter__.return_value = instance
            instance.get.return_value = mock_resp
            result = await mgr.check()

        assert result["update_available"] is False

    @pytest.mark.asyncio
    async def test_check_no_manifest_url(self):
        from system_modules.update_manager.updater import UpdateManager
        mgr = UpdateManager(publish_event_cb=AsyncMock(), manifest_url="")
        result = await mgr.check()
        assert result["update_available"] is False

    @pytest.mark.asyncio
    async def test_check_http_error_sets_error_state(self):
        from system_modules.update_manager.updater import UpdateState
        mgr = make_manager()
        with patch("httpx.AsyncClient") as mc:
            instance = AsyncMock()
            mc.return_value.__aenter__.return_value = instance
            instance.get.side_effect = httpx.ConnectError("connection refused")
            with pytest.raises(httpx.ConnectError):
                await mgr.check()
        assert mgr.state == UpdateState.ERROR
        assert mgr._error is not None


# ── Extract ───────────────────────────────────────────────────────────────────

class TestExtract:
    def test_extract_zip(self, tmp_path):
        mgr = make_manager()
        zip_bytes = make_zip_bytes({"hello.txt": b"Hello, world!"})
        pkg = tmp_path / "pkg.zip"
        pkg.write_bytes(zip_bytes)
        dest = tmp_path / "extracted"
        dest.mkdir()
        mgr._extract(pkg, dest)
        assert (dest / "hello.txt").exists()
        assert (dest / "hello.txt").read_bytes() == b"Hello, world!"

    def test_extract_unsupported_format(self, tmp_path):
        mgr = make_manager()
        bad_file = tmp_path / "pkg.txt"
        bad_file.write_text("not an archive")
        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(ValueError, match="Unsupported"):
            mgr._extract(bad_file, dest)


# ── Download (SHA256 verification) ────────────────────────────────────────────

class TestDownload:
    @pytest.mark.asyncio
    async def test_download_sha256_match(self, tmp_path):
        from system_modules.update_manager.updater import UpdateState
        zip_bytes = make_zip_bytes({"file.txt": b"content"})
        sha256 = hashlib.sha256(zip_bytes).hexdigest()

        mgr = make_manager()
        mgr._latest = {
            "version": "1.1.0",
            "download_url": "http://update.test/pkg.zip",
            "sha256": sha256,
        }

        class FakeStreamCtx:
            """Async context manager returned by client.stream()."""
            def raise_for_status(self): pass
            async def aiter_bytes(self, chunk_size=65536):
                yield zip_bytes
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        with patch("httpx.AsyncClient") as mc:
            instance = MagicMock()
            mc.return_value.__aenter__ = AsyncMock(return_value=instance)
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            instance.stream.return_value = FakeStreamCtx()
            path = await mgr.download()

        assert path.exists()
        assert mgr.state == UpdateState.DOWNLOADED
        path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_download_sha256_mismatch_raises(self):
        zip_bytes = make_zip_bytes({"file.txt": b"content"})
        mgr = make_manager()
        mgr._latest = {
            "version": "1.1.0",
            "download_url": "http://update.test/pkg.zip",
            "sha256": "badhash" * 8,  # wrong hash
        }

        class FakeStreamCtx:
            def raise_for_status(self): pass
            async def aiter_bytes(self, chunk_size=65536):
                yield zip_bytes
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        with patch("httpx.AsyncClient") as mc:
            instance = MagicMock()
            mc.return_value.__aenter__ = AsyncMock(return_value=instance)
            mc.return_value.__aexit__ = AsyncMock(return_value=False)
            instance.stream.return_value = FakeStreamCtx()
            with pytest.raises(ValueError, match="SHA256 mismatch"):
                await mgr.download()

    @pytest.mark.asyncio
    async def test_download_no_latest_raises(self):
        mgr = make_manager()
        mgr._latest = None
        with pytest.raises(RuntimeError, match="check"):
            await mgr.download()


# ── Apply ─────────────────────────────────────────────────────────────────────

class TestApply:
    @pytest.mark.asyncio
    async def test_apply_extracts_files(self, tmp_path):
        from system_modules.update_manager.updater import UpdateState
        publish = AsyncMock()
        mgr = make_manager(publish=publish, current_version="1.0.0")
        mgr._install_dir = tmp_path / "install"
        mgr._backup_dir = tmp_path / "backup"
        mgr._latest = {"version": "1.1.0"}

        zip_bytes = make_zip_bytes({"app.py": b"print('updated')"})
        pkg = tmp_path / "pkg.zip"
        pkg.write_bytes(zip_bytes)

        await mgr.apply(pkg)
        assert mgr._current_version == "1.1.0"
        assert mgr.state == UpdateState.APPLIED
        assert (mgr._install_dir / "app.py").exists()

        calls = [c[0][0] for c in publish.call_args_list]
        assert "update.applied" in calls

    @pytest.mark.asyncio
    async def test_apply_creates_backup(self, tmp_path):
        from system_modules.update_manager.updater import UpdateState
        mgr = make_manager(current_version="1.0.0")
        mgr._install_dir = tmp_path / "install"
        mgr._backup_dir = tmp_path / "backup"
        mgr._latest = {"version": "1.1.0"}

        # Create existing install
        mgr._install_dir.mkdir()
        (mgr._install_dir / "old_file.py").write_text("old")

        zip_bytes = make_zip_bytes({"new_file.py": b"new"})
        pkg = tmp_path / "pkg.zip"
        pkg.write_bytes(zip_bytes)

        await mgr.apply(pkg)
        # Backup created with old file
        assert (mgr._backup_dir / "old_file.py").exists()


# ── Rollback ──────────────────────────────────────────────────────────────────

class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_restores_backup(self, tmp_path):
        from system_modules.update_manager.updater import UpdateState
        publish = AsyncMock()
        mgr = make_manager(publish=publish)
        mgr._install_dir = tmp_path / "install"
        mgr._backup_dir = tmp_path / "backup"

        # Create backup
        mgr._backup_dir.mkdir()
        (mgr._backup_dir / "backup_file.py").write_text("backup")
        # Create current (different) install
        mgr._install_dir.mkdir()
        (mgr._install_dir / "current_file.py").write_text("current")

        await mgr.rollback()

        assert mgr.state == UpdateState.ROLLED_BACK
        assert (mgr._install_dir / "backup_file.py").exists()
        assert not (mgr._install_dir / "current_file.py").exists()

        calls = [c[0][0] for c in publish.call_args_list]
        assert "update.rolled_back" in calls

    @pytest.mark.asyncio
    async def test_rollback_no_backup_raises(self, tmp_path):
        mgr = make_manager()
        mgr._backup_dir = tmp_path / "nonexistent_backup"
        with pytest.raises(RuntimeError, match="backup"):
            await mgr.rollback()


# ── Start/Stop ─────────────────────────────────────────────────────────────────

class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        mgr = make_manager()
        with patch.object(mgr, "_check_loop", new=AsyncMock()):
            await mgr.start()
            assert mgr._task is not None
            await mgr.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        mgr = make_manager()
        await mgr.stop()


# ── Status ────────────────────────────────────────────────────────────────────

class TestStatus:
    def test_initial_status(self):
        mgr = make_manager(current_version="1.0.0")
        s = mgr.get_status()
        assert s["current_version"] == "1.0.0"
        assert s["state"] == "idle"
        assert s["update_available"] is False
        assert s["latest_version"] is None


# ── API ───────────────────────────────────────────────────────────────────────

class TestUpdateAPI:
    def _make_app(self):
        import system_modules.update_manager.main as um_main
        mgr = make_manager()
        um_main._manager = mgr
        return um_main.app, mgr

    @pytest.mark.asyncio
    async def test_health(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/health")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_status_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/update/status")
        assert r.status_code == 200
        assert "current_version" in r.json()

    @pytest.mark.asyncio
    async def test_check_endpoint_no_manifest(self):
        from httpx import AsyncClient, ASGITransport
        import system_modules.update_manager.main as um_main
        mgr = make_manager(manifest_url="")
        um_main._manager = mgr
        async with AsyncClient(transport=ASGITransport(app=um_main.app), base_url="http://test") as c:
            r = await c.post("/update/check")
        assert r.status_code == 200
        assert r.json()["update_available"] is False

    @pytest.mark.asyncio
    async def test_rollback_no_backup(self):
        from httpx import AsyncClient, ASGITransport
        import tempfile
        app, mgr = self._make_app()
        mgr._backup_dir = Path(tempfile.mkdtemp() + "_no_backup_here")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.post("/update/rollback")
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_widget_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/widget.html")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_settings_served(self):
        from httpx import AsyncClient, ASGITransport
        app, _ = self._make_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            r = await c.get("/settings.html")
        assert r.status_code == 200
