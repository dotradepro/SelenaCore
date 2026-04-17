"""
core/config.py — configuration loading from core.yaml + .env
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Platform
    platform_api_url: str = "https://selenehome.tech/api/v1"
    platform_device_hash: str = ""
    mock_platform: bool = False

    # Core
    core_port: int = 80
    core_data_dir: str = "/var/lib/selena"
    core_secure_dir: str = "/secure"
    core_log_level: str = "INFO"
    debug: bool = False

    # UI
    ui_port: int = 80
    ui_https: bool = True

    # Agent
    agent_check_interval: int = 30
    agent_max_restore_attempts: int = 3

    # Docker (legacy — modules now communicate via WebSocket bus)
    docker_socket: str = "/var/run/docker.sock"
    module_container_image: str = "smarthome-modules:latest"
    sandbox_image: str = "smarthome-sandbox:latest"

    # OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    tuya_client_id: str = ""
    tuya_client_secret: str = ""

    # Tailscale
    tailscale_auth_key: str = ""

    @property
    def db_url(self) -> str:
        data_dir = Path(self.core_data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{data_dir}/selena.db"

    @property
    def secure_dir_path(self) -> Path:
        return Path(self.core_secure_dir)


def _load_yaml_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_settings: CoreSettings | None = None
_yaml_config: dict[str, Any] = {}


def get_settings() -> CoreSettings:
    global _settings
    if _settings is None:
        _settings = CoreSettings()
    return _settings


def get_yaml_config() -> dict[str, Any]:
    global _yaml_config
    if not _yaml_config:
        config_path = os.environ.get(
            "SELENA_CONFIG", "/opt/selena-core/config/core.yaml"
        )
        _yaml_config = _load_yaml_config(config_path)
    return _yaml_config


def migrate_ollama_url_key(config_path: str | os.PathLike | None = None) -> bool:
    """One-shot migration: llm.ollama_url → voice.providers.ollama.url.

    Atomic: writes the new YAML to a tmpfile and replaces the original in
    one ``os.replace`` call. On any exception the original file is left
    untouched — the container boots on the legacy key (ollama_client.py
    still reads ``llm.ollama_url`` as a transition fallback).

    Returns True if the file was rewritten, False otherwise (nothing to
    do, or an error we swallowed). Idempotent: running it twice is safe.

    Deleted once the transition fallback in ollama_client.py is removed
    (see CHANGELOG: the legacy key won't be read past version 0.5.x).
    """
    import logging
    import tempfile

    logger = logging.getLogger(__name__)

    path = Path(config_path or os.environ.get(
        "SELENA_CONFIG", "/opt/selena-core/config/core.yaml"
    ))
    if not path.is_file():
        return False

    try:
        with path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("ollama_url migration: could not read %s: %s", path, exc)
        return False

    if not isinstance(raw, dict):
        return False

    llm_section = raw.get("llm") if isinstance(raw.get("llm"), dict) else None
    llm_url = llm_section.get("ollama_url") if llm_section else None

    voice = raw.setdefault("voice", {})
    providers = voice.setdefault("providers", {})
    ollama_cfg = providers.setdefault("ollama", {})
    voice_url = ollama_cfg.get("url")

    changed = False
    if llm_url and not voice_url:
        ollama_cfg["url"] = llm_url
        changed = True

    # Unconditional cleanup — kills the old key even if both coexisted
    # (hand-merge / partial migration). Saves us a second release to prune.
    if llm_section is not None and "ollama_url" in llm_section:
        llm_section.pop("ollama_url", None)
        if not llm_section:
            raw.pop("llm", None)
        changed = True

    if not changed:
        return False

    try:
        # Atomic write: tmp → os.replace
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".core_yaml_mig_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp_path, str(path))
            logger.info("Migrated llm.ollama_url → voice.providers.ollama.url")
            return True
        except Exception:
            try: os.unlink(tmp_path)
            except OSError: pass
            raise
    except Exception as exc:
        logger.warning("ollama_url migration: write failed: %s", exc)
        return False
