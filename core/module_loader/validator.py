"""
core/module_loader/validator.py — manifest.json validation on module installation

Thin wrapper around the Pydantic schema in manifest_schema.py. Returns the legacy
ValidationResult shape so existing callers (loader.py, ZIP-install paths, tests)
do not need to change.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.module_loader.manifest_schema import (
    ALLOWED_PERMISSIONS,
    INTEGRATION_ONLY_PERMISSIONS,
    VALID_PROFILES,
    VALID_RUNTIME,
    VALID_TYPES,
    ModuleManifest,
)

# Top-level fields that must exist before Pydantic even sees the manifest. Kept
# explicit so missing-field errors are reported with the legacy phrasing the
# loader expects.
REQUIRED_FIELDS = ["name", "version", "type", "api_version", "permissions", "room"]


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    manifest: dict[str, Any] | None = None


def _format_pydantic_errors(exc: ValidationError) -> list[str]:
    """Render a ValidationError as a list of human-readable strings."""
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) if err["loc"] else "<root>"
        msg = err["msg"]
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, "):]
        out.append(f"{loc}: {msg}")
    return out


def validate_manifest(manifest: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append(f"Missing required field: '{field}'")

    if errors:
        return ValidationResult(valid=False, errors=errors)

    try:
        ModuleManifest.model_validate(manifest)
    except ValidationError as exc:
        return ValidationResult(valid=False, errors=_format_pydantic_errors(exc))

    return ValidationResult(valid=True, errors=[], manifest=manifest)


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


__all__ = [
    "ALLOWED_PERMISSIONS",
    "INTEGRATION_ONLY_PERMISSIONS",
    "VALID_PROFILES",
    "VALID_RUNTIME",
    "VALID_TYPES",
    "ValidationResult",
    "validate_manifest",
    "validate_zip",
]
