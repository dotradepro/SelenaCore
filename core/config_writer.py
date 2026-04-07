"""
core/config_writer.py — Atomic read/write for core.yaml configuration.

Provides thread-safe, atomic update of YAML configuration.
All setup endpoints persist their choices through this module.
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH: Path | None = None
_config_lock = threading.Lock()


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
        with _config_lock:
            with path.open("r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error("Failed to read config %s: %s", path, exc)
        return {}


def write_config(config: dict[str, Any]) -> None:
    """Write full config dict to core.yaml atomically (write tmp → rename)."""
    path = _get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _config_lock:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".core_yaml_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, str(path))
            logger.debug("Config written to %s", path)
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


def update_many(updates: list[tuple[str, str, Any]]) -> dict[str, Any]:
    """Apply multiple (section, key, value) updates in a single read-write cycle."""
    config = read_config()
    for section, key, value in updates:
        config.setdefault(section, {})[key] = value
    write_config(config)
    return config


def get_value(section: str, key: str, default: Any = None) -> Any:
    """Read a single value from config."""
    config = read_config()
    return config.get(section, {}).get(key, default)


def get_nested(path: str, default: Any = None) -> Any:
    """Read a value by dotted path, e.g. 'voice.tts.models_dir'."""
    config = read_config()
    node: Any = config
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def update_nested(path: str, value: Any) -> dict[str, Any]:
    """Update a value by dotted path, creating intermediate dicts as needed."""
    config = read_config()
    parts = path.split(".")
    node: Any = config
    for part in parts[:-1]:
        if part not in node or not isinstance(node[part], dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    write_config(config)
    return config
