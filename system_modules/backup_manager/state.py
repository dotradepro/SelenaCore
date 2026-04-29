"""Persisted backup_manager settings + canonical category→paths mapping."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


CATEGORY_PATHS: dict[str, list[str]] = {
    "core": [
        "/var/lib/selena/selena.db",
        "/var/lib/selena/widget_layout.json",
        "/var/lib/selena/modules",
        "/etc/selena",
    ],
    "secrets": [
        "/secure",
    ],
}

EXCLUDE_PATHS: list[str] = ["/secure/vault_key"]

DEFAULT_SETTINGS: dict[str, Any] = {
    "categories": {"core": True, "secrets": True},
    "schedule": {"enabled": False, "trigger": "cron:0 3 * * *"},
    "max_backups": 5,
}

def _state_dir() -> Path:
    return Path(
        os.environ.get(
            "BACKUP_MANAGER_STATE_DIR",
            "/var/lib/selena/modules/backup_manager",
        )
    )


def _settings_file() -> Path:
    return _state_dir() / "settings.json"


def load_settings() -> dict[str, Any]:
    """Load persisted settings, falling back to defaults for missing keys."""
    f = _settings_file()
    if not f.exists():
        return _deepcopy_defaults()
    try:
        raw = json.loads(f.read_text())
    except Exception as exc:
        logger.warning("backup_manager: settings.json unreadable (%s) — using defaults", exc)
        return _deepcopy_defaults()
    return _merge_defaults(raw)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Persist settings (after merging with defaults). Returns the merged dict."""
    merged = _merge_defaults(settings)
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    _settings_file().write_text(json.dumps(merged, indent=2))
    return merged


def resolve_paths(categories: dict[str, bool]) -> list[str]:
    """Return the flat list of paths corresponding to the enabled categories.

    `core` is always included even if the caller passes False — it's the
    minimum viable backup. `secrets` honours the flag.
    """
    paths: list[str] = list(CATEGORY_PATHS["core"])
    if categories.get("secrets", True):
        paths.extend(CATEGORY_PATHS["secrets"])
    return paths


def _deepcopy_defaults() -> dict[str, Any]:
    return {
        "categories": dict(DEFAULT_SETTINGS["categories"]),
        "schedule": dict(DEFAULT_SETTINGS["schedule"]),
        "max_backups": DEFAULT_SETTINGS["max_backups"],
    }


def _merge_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    base = _deepcopy_defaults()
    cats = raw.get("categories") or {}
    if isinstance(cats, dict):
        # core is always on
        base["categories"]["core"] = True
        if "secrets" in cats:
            base["categories"]["secrets"] = bool(cats["secrets"])
    sched = raw.get("schedule") or {}
    if isinstance(sched, dict):
        if "enabled" in sched:
            base["schedule"]["enabled"] = bool(sched["enabled"])
        trig = sched.get("trigger")
        if isinstance(trig, str) and trig.strip():
            base["schedule"]["trigger"] = trig.strip()
    if isinstance(raw.get("max_backups"), int) and 1 <= raw["max_backups"] <= 50:
        base["max_backups"] = raw["max_backups"]
    return base
