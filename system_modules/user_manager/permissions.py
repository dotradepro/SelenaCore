"""
system_modules/user_manager/permissions.py — Role permissions CRUD.

Roles: owner | admin | user | guest
Each role has a JSON-serialised RolePermissions config stored in SQLite.
Defaults are applied when no custom config exists in the DB.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


@dataclass
class RolePermissions:
    # Devices
    devices_view: bool = True
    devices_control: bool = True
    # Scenes / automations
    scenes_run: str = "all"          # "all" | "approved" | "none"
    # Module access
    modules_configure: bool = False
    # User management
    users_manage: bool = False
    roles_configure: bool = False    # owner only
    # System
    system_reboot: bool = False
    system_update: bool = False
    integrity_logs_view: bool = False
    # Voice
    voice_commands: str = "all"      # "all" | "basic" | "none"
    # Fine-grained allow-lists (empty list = no restriction applied)
    allowed_device_types: list[str] = field(default_factory=list)
    allowed_widget_ids: list[str] = field(default_factory=list)


DEFAULT_PERMISSIONS: dict[str, RolePermissions] = {
    "owner": RolePermissions(
        devices_view=True,
        devices_control=True,
        scenes_run="all",
        modules_configure=True,
        users_manage=True,
        roles_configure=True,
        system_reboot=True,
        system_update=True,
        integrity_logs_view=True,
        voice_commands="all",
    ),
    "admin": RolePermissions(
        devices_view=True,
        devices_control=True,
        scenes_run="all",
        modules_configure=True,
        users_manage=True,
        roles_configure=False,
        system_reboot=True,
        system_update=True,
        integrity_logs_view=True,
        voice_commands="all",
    ),
    "user": RolePermissions(
        devices_view=True,
        devices_control=True,
        scenes_run="all",
        modules_configure=False,
        users_manage=False,
        roles_configure=False,
        system_reboot=False,
        system_update=False,
        integrity_logs_view=False,
        voice_commands="all",
    ),
    "guest": RolePermissions(
        devices_view=True,
        devices_control=False,
        scenes_run="approved",
        modules_configure=False,
        users_manage=False,
        roles_configure=False,
        system_reboot=False,
        system_update=False,
        integrity_logs_view=False,
        voice_commands="basic",
    ),
}


def _perms_from_dict(d: dict[str, Any]) -> RolePermissions:
    valid_fields = set(RolePermissions.__dataclass_fields__.keys())
    filtered = {k: v for k, v in d.items() if k in valid_fields}
    return RolePermissions(**filtered)


class PermissionsManager:
    """Read and write per-role permission configs from SQLite."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def ensure_tables(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS role_config (
                    role        TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at  REAL NOT NULL
                )
            """))

    async def get(self, role: str) -> RolePermissions:
        """Return the effective permissions for a role.

        Falls back to DEFAULT_PERMISSIONS if no custom config is stored.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT config_json FROM role_config WHERE role = :role"),
                {"role": role},
            )
            row = result.fetchone()

        if row:
            try:
                d = json.loads(row[0])
                return _perms_from_dict(d)
            except Exception:
                logger.exception("Corrupt role_config for role=%s — using default", role)

        return DEFAULT_PERMISSIONS.get(role, RolePermissions())

    async def set(self, role: str, perms: RolePermissions) -> None:
        """Persist custom permissions for a role (upsert)."""
        config_json = json.dumps(asdict(perms))
        now = time.time()
        async with self._engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO role_config (role, config_json, updated_at)
                VALUES (:role, :config_json, :updated_at)
                ON CONFLICT(role) DO UPDATE
                    SET config_json = excluded.config_json,
                        updated_at  = excluded.updated_at
            """), {"role": role, "config_json": config_json, "updated_at": now})
        logger.info("Permissions updated for role=%s", role)

    async def get_all(self) -> dict[str, RolePermissions]:
        """Return effective permissions for all known roles."""
        from system_modules.user_manager.profiles import VALID_ROLES
        return {role: await self.get(role) for role in sorted(VALID_ROLES)}
