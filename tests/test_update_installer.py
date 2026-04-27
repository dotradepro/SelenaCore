"""tests/test_update_installer.py — pre-flight + dispatch helpers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ── sanitize_unit_tag ──────────────────────────────────────────────────────


class TestSanitize:
    def test_dotted_tag_normalized(self):
        from system_modules.update_manager.installer import sanitize_unit_tag

        # Dots are not in [a-zA-Z0-9-], so they collapse to underscore.
        assert sanitize_unit_tag("v0.4.150-rc") == "v0_4_150-rc"

    def test_plus_replaced(self):
        from system_modules.update_manager.installer import sanitize_unit_tag

        # semver build metadata uses '+'; not a valid systemd unit char
        out = sanitize_unit_tag("v0.4.142-rc+0644435")
        assert "+" not in out
        assert "0644435" in out

    def test_shell_metas_neutralized(self):
        from system_modules.update_manager.installer import sanitize_unit_tag

        out = sanitize_unit_tag("v0.4 ; rm -rf /")
        for ch in (";", " ", "/"):
            assert ch not in out


# ── precheck ────────────────────────────────────────────────────────────────


class TestPrecheck:
    def test_python_too_old(self, tmp_path):
        from system_modules.update_manager.installer import precheck

        ok, reason = precheck(
            release_size_bytes=1000,
            install_dir=tmp_path,
            meta={"min_python": "99.99"},
            staging_root=tmp_path,
        )
        assert not ok and "Python" in reason

    def test_install_lock_blocks(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer

        lock = tmp_path / "lock"
        lock.write_text("held")
        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", lock)
        monkeypatch.setattr(installer, "UPDATE_FLAG_PATH", tmp_path / "no_flag")

        ok, reason = installer.precheck(
            release_size_bytes=1000,
            install_dir=tmp_path,
            meta={"min_python": f"{sys.version_info.major}.{sys.version_info.minor}"},
            staging_root=tmp_path,
        )
        assert not ok and "lock" in reason.lower()

    def test_update_flag_blocks(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer

        flag = tmp_path / "flag"
        flag.write_text("in progress")
        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", tmp_path / "no_lock")
        monkeypatch.setattr(installer, "UPDATE_FLAG_PATH", flag)

        ok, reason = installer.precheck(
            release_size_bytes=1000,
            install_dir=tmp_path,
            meta={"min_python": f"{sys.version_info.major}.{sys.version_info.minor}"},
            staging_root=tmp_path,
        )
        assert not ok and "flag" in reason.lower()

    def test_disk_space_too_small(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer

        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", tmp_path / "no_lock")
        monkeypatch.setattr(installer, "UPDATE_FLAG_PATH", tmp_path / "no_flag")

        # Mock disk_usage to report nearly-full filesystem.
        class FakeUsage:
            def __init__(self, free):
                self.total = 100 * 1024 * 1024
                self.used = self.total - free
                self.free = free

        monkeypatch.setattr(
            installer.shutil, "disk_usage", lambda path: FakeUsage(free=1024)
        )
        ok, reason = installer.precheck(
            release_size_bytes=10 * 1024 * 1024,
            install_dir=tmp_path,
            meta={"min_python": f"{sys.version_info.major}.{sys.version_info.minor}"},
            staging_root=tmp_path,
        )
        assert not ok and "disk space" in reason.lower()

    def test_passes_when_clean(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer

        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", tmp_path / "no_lock")
        monkeypatch.setattr(installer, "UPDATE_FLAG_PATH", tmp_path / "no_flag")

        class FakeUsage:
            free = 10 * 1024 * 1024 * 1024  # 10 GB
            total = free
            used = 0

        monkeypatch.setattr(
            installer.shutil, "disk_usage", lambda path: FakeUsage()
        )
        ok, reason = installer.precheck(
            release_size_bytes=1024,
            install_dir=tmp_path,
            meta={"min_python": f"{sys.version_info.major}.{sys.version_info.minor}"},
            staging_root=tmp_path,
        )
        assert ok and reason == ""


# ── lock acquire/release ─────────────────────────────────────────────────────


class TestInstallLock:
    def test_acquire_then_blocks_second(self, tmp_path, monkeypatch):
        from system_modules.update_manager import installer

        lock = tmp_path / "lock"
        monkeypatch.setattr(installer, "INSTALL_LOCK_PATH", lock)
        assert installer.acquire_install_lock("v1") is True
        assert lock.exists()
        assert installer.acquire_install_lock("v2") is False
        installer.release_install_lock()
        assert not lock.exists()


# ── dispatch_external (mocked subprocess.run) ────────────────────────────────


class TestDispatch:
    def test_install_command_shape(self, monkeypatch):
        from system_modules.update_manager import installer

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["check"] = kwargs.get("check")
            captured["shell"] = kwargs.get("shell")

            class _R:
                returncode = 0

            return _R()

        monkeypatch.setattr(installer.subprocess, "run", fake_run)

        unit = installer.dispatch_external(
            "v0.4.150-rc", action="install", apply_script="/x/apply.sh"
        )
        cmd = captured["cmd"]
        assert cmd[0] == "sudo"
        assert "systemd-run" in cmd
        assert any(c.startswith("--on-active=") for c in cmd)
        assert any(c.startswith("--unit=selena-update-") for c in cmd)
        assert "--no-block" in cmd
        assert cmd[-3:] == ["/x/apply.sh", "v0.4.150-rc", "install"]
        assert captured["check"] is True
        assert captured["shell"] is False
        assert unit.startswith("selena-update-")

    def test_rollback_action(self, monkeypatch):
        from system_modules.update_manager import installer

        captured = {}
        monkeypatch.setattr(
            installer.subprocess, "run", lambda c, **k: captured.update(cmd=c)
        )
        installer.dispatch_external("v1.0.0", action="rollback", apply_script="/x.sh")
        assert captured["cmd"][-1] == "rollback"

    def test_invalid_action_raises(self):
        from system_modules.update_manager import installer

        with pytest.raises(ValueError, match="invalid action"):
            installer.dispatch_external("v1", action="format-disk")
