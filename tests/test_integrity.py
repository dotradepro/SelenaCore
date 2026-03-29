"""tests/test_integrity.py — pytest tests for Integrity Agent + API"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


# ── Manifest unit tests ─────────────────────────────────────────────────────

class TestManifest:
    """Test agent/manifest.py functions."""

    def test_sha256_file(self, tmp_path: Path) -> None:
        from agent.manifest import sha256_file

        f = tmp_path / "test.py"
        f.write_text("hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert sha256_file(str(f)) == expected

    def test_sha256_file_missing(self, tmp_path: Path) -> None:
        from agent.manifest import sha256_file

        assert sha256_file(str(tmp_path / "nonexistent")) == ""

    def test_sha256_string(self) -> None:
        from agent.manifest import sha256_string

        assert sha256_string("test") == hashlib.sha256(b"test").hexdigest()

    def test_create_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import manifest

        # Create fake core files
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        (core_dir / "main.py").write_text("# main")
        (core_dir / "config.py").write_text("# config")

        monkeypatch.setattr(manifest, "CORE_FILES_GLOB", str(core_dir / "*.py"))
        monkeypatch.setattr(manifest, "MANIFEST_PATH", str(tmp_path / "manifest.json"))
        monkeypatch.setattr(manifest, "MASTER_HASH_PATH", str(tmp_path / "master.hash"))

        result = manifest.create_manifest()
        assert len(result) == 2
        assert (tmp_path / "manifest.json").exists()
        assert (tmp_path / "master.hash").exists()

    def test_load_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import manifest

        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps({"a.py": "abc123"}))
        monkeypatch.setattr(manifest, "MANIFEST_PATH", str(mf))

        result = manifest.load_manifest()
        assert result == {"a.py": "abc123"}

    def test_load_manifest_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import manifest

        monkeypatch.setattr(manifest, "MANIFEST_PATH", str(tmp_path / "nope.json"))
        with pytest.raises(FileNotFoundError):
            manifest.load_manifest()

    def test_verify_manifest_hash_valid(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import manifest

        content = json.dumps({"test.py": "abc"})
        mf = tmp_path / "manifest.json"
        mf.write_text(content)
        mh = tmp_path / "master.hash"
        mh.write_text(manifest.sha256_string(content))

        monkeypatch.setattr(manifest, "MANIFEST_PATH", str(mf))
        monkeypatch.setattr(manifest, "MASTER_HASH_PATH", str(mh))

        assert manifest.verify_manifest_hash() is True

    def test_verify_manifest_hash_tampered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import manifest

        mf = tmp_path / "manifest.json"
        mf.write_text('{"file": "hash"}')
        mh = tmp_path / "master.hash"
        mh.write_text("wrong_hash")

        monkeypatch.setattr(manifest, "MANIFEST_PATH", str(mf))
        monkeypatch.setattr(manifest, "MASTER_HASH_PATH", str(mh))

        assert manifest.verify_manifest_hash() is False

    def test_check_files_no_changes(self, tmp_path: Path) -> None:
        from agent.manifest import check_files, sha256_file

        f = tmp_path / "core.py"
        f.write_text("# original")
        manifest = {str(f): sha256_file(str(f))}

        assert check_files(manifest) == []

    def test_check_files_with_changes(self, tmp_path: Path) -> None:
        from agent.manifest import check_files, sha256_file

        f = tmp_path / "core.py"
        f.write_text("# original")
        manifest = {str(f): "wrong_hash_value"}

        changed = check_files(manifest)
        assert len(changed) == 1
        assert changed[0]["path"] == str(f)
        assert changed[0]["expected"] == "wrong_hash_value"


# ── Integrity Agent logic tests ──────────────────────────────────────────────

class TestIntegrityAgent:

    @pytest.mark.asyncio
    async def test_run_check_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import integrity_agent, manifest

        # Setup valid manifest
        core_dir = tmp_path / "core"
        core_dir.mkdir()
        (core_dir / "main.py").write_text("# ok")

        monkeypatch.setattr(manifest, "CORE_FILES_GLOB", str(core_dir / "*.py"))
        monkeypatch.setattr(manifest, "MANIFEST_PATH", str(tmp_path / "manifest.json"))
        monkeypatch.setattr(manifest, "MASTER_HASH_PATH", str(tmp_path / "master.hash"))
        monkeypatch.setattr(integrity_agent, "STATE_FILE", tmp_path / "state.json")

        manifest.create_manifest()
        await integrity_agent.run_check()

        # Should write "ok" state
        if (tmp_path / "state.json").exists():
            state = json.loads((tmp_path / "state.json").read_text())
            assert state["status"] == "ok"

    @pytest.mark.asyncio
    async def test_run_check_detects_violation(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent import integrity_agent, manifest

        core_dir = tmp_path / "core"
        core_dir.mkdir()
        f = core_dir / "main.py"
        f.write_text("# original")

        monkeypatch.setattr(manifest, "CORE_FILES_GLOB", str(core_dir / "*.py"))
        monkeypatch.setattr(manifest, "MANIFEST_PATH", str(tmp_path / "manifest.json"))
        monkeypatch.setattr(manifest, "MASTER_HASH_PATH", str(tmp_path / "master.hash"))
        monkeypatch.setattr(integrity_agent, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(integrity_agent, "LOG_PATH", str(tmp_path / "integrity.log"))

        manifest.create_manifest()

        # Tamper with the file
        f.write_text("# HACKED")

        # Mock trigger_response to avoid external calls
        mock_trigger = AsyncMock()
        monkeypatch.setattr(integrity_agent, "trigger_response", mock_trigger)

        await integrity_agent.run_check()
        mock_trigger.assert_called_once()
        assert mock_trigger.call_args[0][0] == "files_changed"


# ── Integrity API endpoint tests ─────────────────────────────────────────────

class TestIntegrityAPI:

    @pytest.mark.asyncio
    async def test_integrity_status(self, client, auth_headers) -> None:
        resp = await client.get("/api/v1/integrity/status", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "check_interval_sec" in data
        assert data["check_interval_sec"] == 30
