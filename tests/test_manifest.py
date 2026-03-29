"""
tests/test_manifest.py — Integrity agent manifest tests
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from agent.manifest import sha256_file, sha256_string, check_files, load_manifest, verify_manifest_hash


class TestSha256:
    def test_sha256_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h = sha256_file(f)
        assert len(h) == 64
        assert h == sha256_file(f)  # deterministic

    def test_sha256_file_missing(self):
        h = sha256_file("/nonexistent/file.txt")
        assert h == ""

    def test_sha256_string(self):
        h = sha256_string("hello")
        assert len(h) == 64
        assert h == sha256_string("hello")  # deterministic
        assert h != sha256_string("world")


class TestCheckFiles:
    def test_no_changes(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("print('hello')")
        manifest = {str(f1): sha256_file(f1)}
        assert check_files(manifest) == []

    def test_file_changed(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("print('hello')")
        manifest = {str(f1): sha256_file(f1)}
        f1.write_text("print('modified')")
        changes = check_files(manifest)
        assert len(changes) == 1
        assert changes[0]["path"] == str(f1)

    def test_file_deleted(self, tmp_path):
        f1 = tmp_path / "a.py"
        f1.write_text("content")
        manifest = {str(f1): sha256_file(f1)}
        f1.unlink()
        changes = check_files(manifest)
        assert len(changes) == 1
        assert changes[0]["actual"] == ""


class TestManifestPersistence:
    def test_load_missing_manifest(self):
        with pytest.raises(FileNotFoundError):
            load_manifest()

    def test_verify_manifest_hash(self, tmp_path):
        manifest_path = tmp_path / "core.manifest"
        master_hash_path = tmp_path / "master.hash"

        manifest = {"file.py": "abc123"}
        manifest_json = json.dumps(manifest, sort_keys=True, indent=2)
        manifest_path.write_text(manifest_json)
        master_hash_path.write_text(sha256_string(manifest_json))

        import agent.manifest as m
        orig_manifest = m.MANIFEST_PATH
        orig_master = m.MASTER_HASH_PATH
        m.MANIFEST_PATH = str(manifest_path)
        m.MASTER_HASH_PATH = str(master_hash_path)

        assert verify_manifest_hash() is True

        # Tamper with manifest
        manifest_path.write_text('{"file.py": "tampered"}')
        assert verify_manifest_hash() is False

        m.MANIFEST_PATH = orig_manifest
        m.MASTER_HASH_PATH = orig_master
