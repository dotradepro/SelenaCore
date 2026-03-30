"""
tests/test_config.py — config.py + config_writer.py tests
"""
from __future__ import annotations

import os
import pytest
import yaml
from pathlib import Path
from unittest.mock import patch


# ── CoreSettings ─────────────────────────────────────────────────────────────

class TestCoreSettings:
    def test_defaults(self):
        from core.config import CoreSettings
        s = CoreSettings()
        assert s.core_port == 7070
        assert s.ui_port == 80
        assert s.core_log_level == "INFO"
        assert s.debug is False

    def test_db_url_uses_data_dir(self, tmp_path):
        from core.config import CoreSettings
        s = CoreSettings(core_data_dir=str(tmp_path))
        assert str(tmp_path) in s.db_url
        assert s.db_url.startswith("sqlite+aiosqlite:///")

    def test_secure_dir_path(self):
        from core.config import CoreSettings
        s = CoreSettings(core_secure_dir="/tmp/test-secure")
        assert s.secure_dir_path == Path("/tmp/test-secure")


# ── YAML config loading ─────────────────────────────────────────────────────

class TestYamlConfig:
    def test_load_missing_file(self):
        from core.config import _load_yaml_config
        result = _load_yaml_config("/nonexistent/path.yaml")
        assert result == {}

    def test_load_valid_yaml(self, tmp_path):
        from core.config import _load_yaml_config
        cfg = tmp_path / "test.yaml"
        cfg.write_text("system:\n  language: uk\n  timezone: Europe/Kyiv\n")
        result = _load_yaml_config(cfg)
        assert result["system"]["language"] == "uk"

    def test_load_empty_yaml(self, tmp_path):
        from core.config import _load_yaml_config
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("")
        result = _load_yaml_config(cfg)
        assert result == {}


# ── config_writer ────────────────────────────────────────────────────────────

class TestConfigWriter:
    @pytest.fixture(autouse=True)
    def _setup_config_path(self, tmp_path):
        import core.config_writer as cw
        cw._CONFIG_PATH = tmp_path / "core.yaml"
        yield
        cw._CONFIG_PATH = None

    def test_read_empty(self):
        from core.config_writer import read_config
        assert read_config() == {}

    def test_write_and_read(self):
        from core.config_writer import write_config, read_config
        data = {"system": {"language": "uk"}, "core": {"port": 7070}}
        write_config(data)
        result = read_config()
        assert result["system"]["language"] == "uk"
        assert result["core"]["port"] == 7070

    def test_update_config(self):
        from core.config_writer import update_config, read_config
        update_config("system", "language", "en")
        update_config("system", "timezone", "UTC")
        cfg = read_config()
        assert cfg["system"]["language"] == "en"
        assert cfg["system"]["timezone"] == "UTC"

    def test_update_section(self):
        from core.config_writer import update_section, read_config
        update_section("voice", {"wake_word": "hey_selena", "stt_model": "base"})
        cfg = read_config()
        assert cfg["voice"]["wake_word"] == "hey_selena"
        assert cfg["voice"]["stt_model"] == "base"

    def test_get_value(self):
        from core.config_writer import update_config, get_value
        update_config("hardware", "gpu_detected", True)
        assert get_value("hardware", "gpu_detected") is True
        assert get_value("hardware", "nonexistent", "default") == "default"
        assert get_value("missing_section", "key", 42) == 42

    def test_atomic_write(self, tmp_path):
        """Verify write creates real file, not temp file."""
        from core.config_writer import write_config, _get_config_path
        write_config({"test": True})
        path = _get_config_path()
        assert path.exists()
        # No .tmp files should remain
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0
