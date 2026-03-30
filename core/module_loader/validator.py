"""
core/module_loader/validator.py — manifest.json validation on module installation
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = ["name", "version", "type", "api_version", "permissions"]

VALID_TYPES = {"SYSTEM", "UI", "INTEGRATION", "DRIVER", "AUTOMATION", "IMPORT_SOURCE"}
VALID_PROFILES = {"HEADLESS", "SETTINGS_ONLY", "ICON_SETTINGS", "FULL"}
VALID_RUNTIME = {"always_on", "on_demand", "scheduled"}

ALLOWED_PERMISSIONS = {
    "device.read",
    "device.write",
    "events.subscribe",
    "events.publish",
    "events.subscribe_all",
    "secrets.oauth",  # только для INTEGRATION
    "secrets.proxy",  # только для INTEGRATION
    "devices.read",
    "devices.control",
    "secrets.read",
    "modules.list",
}

INTEGRATION_ONLY_PERMISSIONS = {"secrets.oauth", "secrets.proxy"}

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$")


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    manifest: dict[str, Any] | None = None


def validate_manifest(manifest: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append(f"Missing required field: '{field}'")

    if errors:
        return ValidationResult(valid=False, errors=errors)

    # Name
    name = manifest.get("name", "")
    if not NAME_PATTERN.match(name):
        errors.append(
            f"Invalid name '{name}': must be lowercase alphanumeric with hyphens, 2-64 chars"
        )

    # Version (semver)
    version = manifest.get("version", "")
    if not VERSION_PATTERN.match(version):
        errors.append(f"Invalid version '{version}': must be semver (X.Y.Z)")

    # Type
    module_type = manifest.get("type", "")
    if module_type not in VALID_TYPES:
        errors.append(f"Invalid type '{module_type}': must be one of {VALID_TYPES}")

    # UI profile (optional)
    ui_profile = manifest.get("ui_profile")
    if ui_profile and ui_profile not in VALID_PROFILES:
        errors.append(f"Invalid ui_profile '{ui_profile}': must be one of {VALID_PROFILES}")

    # Runtime mode (optional)
    runtime_mode = manifest.get("runtime_mode", "always_on")
    if runtime_mode not in VALID_RUNTIME:
        errors.append(f"Invalid runtime_mode '{runtime_mode}': must be one of {VALID_RUNTIME}")

    # Port — deprecated (modules now communicate via WebSocket bus, not HTTP)
    # Kept for backward compatibility but ignored at runtime
    if "port" in manifest and module_type == "SYSTEM":
        errors.append(
            "SYSTEM modules must not specify 'port' — they run in-process"
        )

    # Permissions
    permissions = manifest.get("permissions", [])
    if not isinstance(permissions, list):
        errors.append("'permissions' must be a list")
    else:
        unknown = set(permissions) - ALLOWED_PERMISSIONS
        if unknown:
            errors.append(f"Unknown permissions: {unknown}")

        # Integration-only permissions check
        if module_type != "INTEGRATION":
            restricted = set(permissions) & INTEGRATION_ONLY_PERMISSIONS
            if restricted:
                errors.append(
                    f"Permissions {restricted} are only allowed for INTEGRATION type modules"
                )

    # API version
    api_version = manifest.get("api_version", "")
    if not api_version:
        errors.append("'api_version' must not be empty")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        manifest=manifest if not errors else None,
    )


def validate_zip(zip_path: Path) -> ValidationResult:
    """Validate a module ZIP archive — checks structure and manifest."""
    if not zip_path.exists():
        return ValidationResult(valid=False, errors=[f"File not found: {zip_path}"])

    if not zipfile.is_zipfile(zip_path):
        return ValidationResult(valid=False, errors=["Not a valid ZIP archive"])

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
        if "manifest.json" not in names:
            return ValidationResult(
                valid=False,
                errors=["Missing manifest.json in ZIP root"],
            )
        try:
            manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return ValidationResult(
                valid=False,
                errors=[f"Invalid manifest.json: {e}"],
            )

    return validate_manifest(manifest_data)
