"""
core/config_writer.py — Atomic read/write for core.yaml configuration.

Provides thread-safe, atomic update of YAML configuration.
All setup endpoints persist their choices through this module.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH: Path | None = None


def _get_config_path() -> Path:
    global _CONFIG_PATH
    if _CONFIG_PATH is None:
        _CONFIG_PATH = Path(
            os.environ.get("SELENA_CONFIG", "/opt/selena-core/config/core.yaml")
        )
    return _CONFIG_PATH


def read_config() -> dict[str, Any]:
    """Read current core.yaml. Returns empty dict if file missing."""
    path = _get_config_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error("Failed to read config %s: %s", path, exc)
        return {}


def write_config(config: dict[str, Any]) -> None:
    """Write full config dict to core.yaml atomically (write tmp → rename)."""
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=".core_yaml_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        os.replace(tmp_path, str(path))
        logger.info("Config written to %s", path)
    except Exception as exc:
        logger.error("Failed to write config: %s", exc)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def update_config(section: str, key: str, value: Any) -> dict[str, Any]:
    """Update a single key in a config section. Returns updated config."""
    config = read_config()
    config.setdefault(section, {})[key] = value
    write_config(config)
    return config


def update_section(section: str, data: dict[str, Any]) -> dict[str, Any]:
    """Merge data into a config section. Returns updated config."""
    config = read_config()
    config.setdefault(section, {}).update(data)
    write_config(config)
    return config


def get_value(section: str, key: str, default: Any = None) -> Any:
    """Read a single value from config."""
    config = read_config()
    return config.get(section, {}).get(key, default)
