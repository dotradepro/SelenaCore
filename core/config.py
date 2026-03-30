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
    platform_api_url: str = "https://smarthome-lk.com/api/v1"
    platform_device_hash: str = ""
    mock_platform: bool = False

    # Core
    core_port: int = 7070
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

    # Docker
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
