"""
tests/test_version.py — Version module tests
"""
from __future__ import annotations

from unittest.mock import patch

from core.version import MAJOR, MINOR, LABEL, get_version


class TestVersion:
    def test_version_format(self):
        version = get_version()
        assert isinstance(version, str)
        # Should start with MAJOR.MINOR
        assert version.startswith(f"{MAJOR}.{MINOR}.")

    def test_version_with_git(self):
        with patch("core.version._get_patch", return_value=42), \
             patch("core.version._get_commit_hash", return_value="abc1234"):
            v = get_version()
            assert "0.3.42" in v
            assert "abc1234" in v
            if LABEL:
                assert LABEL in v

    def test_version_no_git_with_file(self):
        with patch("core.version._get_patch", return_value=0), \
             patch("core.version._get_commit_hash", return_value=""), \
             patch("core.version._read_version_file", return_value="0.3.100-beta+deadbeef"):
            v = get_version()
            assert v == "0.3.100-beta+deadbeef"

    def test_version_last_resort(self):
        with patch("core.version._get_patch", return_value=0), \
             patch("core.version._get_commit_hash", return_value=""), \
             patch("core.version._read_version_file", return_value=""):
            v = get_version()
            assert v == f"{MAJOR}.{MINOR}.0-{LABEL}" if LABEL else f"{MAJOR}.{MINOR}.0"

    def test_version_imported_constant(self):
        from core.version import VERSION
        assert isinstance(VERSION, str)
        assert len(VERSION) > 0
