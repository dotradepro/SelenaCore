"""
core/module_loader/manifest_schema.py — Pydantic v2 schema for module manifest.json.

Replaces the procedural validation in validator.py. The validator module wraps
this schema and converts ValidationError into the legacy ValidationResult shape
so existing callers don't change.

Schema reflects the dashboard-recraft (Phase 0): widget kinds (template|custom),
template selection, data_endpoints, actions, refresh, and the required `room`
field for room-tab filtering.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VALID_TYPES = {"SYSTEM", "UI", "INTEGRATION", "DRIVER", "AUTOMATION", "IMPORT_SOURCE"}
VALID_PROFILES = {"HEADLESS", "SETTINGS_ONLY", "ICON_SETTINGS", "FULL"}
VALID_RUNTIME = {"always_on", "on_demand", "scheduled"}

ALLOWED_PERMISSIONS = {
    "device.read",
    "device.write",
    "events.subscribe",
    "events.publish",
    "events.subscribe_all",
    "secrets.oauth",
    "secrets.proxy",
    "devices.read",
    "devices.control",
    "secrets.read",
    "modules.list",
}

INTEGRATION_ONLY_PERMISSIONS = {"secrets.oauth", "secrets.proxy"}

VALID_TEMPLATES = {"metric", "sparkline", "toggle-list", "control-panel", "status"}

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$")
SIZE_PATTERN = re.compile(r"^[1-9]\d*x[1-9]\d*$")


class DataEndpointSpec(BaseModel):
    """Maps a logical data key to a path on the module's HTTP surface.

    The dashboard hits ``GET /api/v1/modules/{name}/data/{key}``; core forwards
    the request to the module via Module Bus using ``path``.
    """
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    cache_ttl_s: float = Field(default=5.0, ge=0.0)


class ActionSpec(BaseModel):
    """Maps a logical action key to a path on the module's HTTP surface.

    The dashboard hits ``POST /api/v1/modules/{name}/action/{key}`` with a JSON
    body; core forwards to ``path`` on the module.
    """
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)


class RefreshSpec(BaseModel):
    """Refresh hints — event-driven preferred, polling as fallback."""
    model_config = ConfigDict(extra="forbid")

    events: list[str] = Field(default_factory=list)
    poll_interval_s: int | None = Field(default=None, ge=1)


class WidgetSpec(BaseModel):
    """Widget definition — either template-rendered or custom iframe."""
    model_config = ConfigDict(extra="allow")

    kind: Literal["template", "custom"] = "custom"
    template: Literal["metric", "sparkline", "toggle-list", "control-panel", "status"] | None = None

    file: str | None = None  # legacy iframe HTML path; required for kind=custom unless headless
    size: str | None = None
    min_size: str | None = None
    max_size: str | None = None

    data_endpoints: dict[str, DataEndpointSpec] | None = None
    actions: dict[str, ActionSpec] | None = None
    refresh: RefreshSpec | None = None

    @field_validator("size", "min_size", "max_size")
    @classmethod
    def _size_format(cls, v: str | None) -> str | None:
        if v is not None and not SIZE_PATTERN.match(v):
            raise ValueError(f"size must match WxH (e.g. '2x2'), got '{v}'")
        return v

    @model_validator(mode="after")
    def _kind_template_consistency(self) -> "WidgetSpec":
        if self.kind == "template" and self.template is None:
            raise ValueError("widget.template is required when widget.kind='template'")
        if self.kind == "template":
            self._enforce_template_size()
        return self

    def _enforce_template_size(self) -> None:
        """Templates have a recommended size envelope; reject obvious mismatches."""
        if self.size is None:
            return
        try:
            cols, rows = (int(x) for x in self.size.split("x"))
        except ValueError:
            return  # _size_format already rejected malformed strings

        # See docs/dashboard-recraft.md §3.3 for size envelopes per template.
        if self.template == "metric" and (cols > 2 or rows > 2):
            raise ValueError(f"template 'metric' max size is 2x2, got {self.size}")
        if self.template == "sparkline" and (cols > 4 or rows > 2):
            raise ValueError(f"template 'sparkline' max size is 4x2, got {self.size}")
        if self.template == "toggle-list" and (cols < 2 or rows < 2):
            raise ValueError(f"template 'toggle-list' min size is 2x2, got {self.size}")
        if self.template == "control-panel" and (cols < 2 or rows < 2):
            raise ValueError(f"template 'control-panel' min size is 2x2, got {self.size}")
        if self.template == "status" and (cols > 4 or rows > 2):
            raise ValueError(f"template 'status' max size is 4x2, got {self.size}")


class UISpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    icon: str | None = None
    widget: WidgetSpec | None = None
    settings: str | None = None


class ResourceSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    memory_mb: int | None = Field(default=None, ge=0)
    cpu: float | None = Field(default=None, ge=0.0)


class ModuleManifest(BaseModel):
    """Top-level manifest.json shape."""
    model_config = ConfigDict(extra="allow")

    name: str
    version: str
    type: str
    api_version: str
    permissions: list[str]

    description: str | None = None
    ui_profile: str | None = None
    runtime_mode: str = "always_on"
    group: str | None = None
    intents: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)

    # Dashboard recraft Phase 0: room is required for room-tab filtering.
    # Modules without a physical room (cloud-sync, integrity, watchdog) declare "system".
    room: str = Field(..., min_length=1)

    ui: UISpec | None = None
    resources: ResourceSpec | None = None
    oauth: Any | None = None

    author: str | None = None
    license: str | None = None
    homepage: str | None = None

    port: int | None = None  # deprecated for SYSTEM type; rejected by cross-field validator

    @field_validator("name")
    @classmethod
    def _name_format(cls, v: str) -> str:
        if not NAME_PATTERN.match(v):
            raise ValueError("must be lowercase alphanumeric with hyphens, 2-64 chars")
        return v

    @field_validator("version")
    @classmethod
    def _version_format(cls, v: str) -> str:
        if not VERSION_PATTERN.match(v):
            raise ValueError("must be semver (X.Y.Z)")
        return v

    @field_validator("type")
    @classmethod
    def _type_valid(cls, v: str) -> str:
        if v not in VALID_TYPES:
            raise ValueError(f"must be one of {sorted(VALID_TYPES)}")
        return v

    @field_validator("ui_profile")
    @classmethod
    def _profile_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_PROFILES:
            raise ValueError(f"must be one of {sorted(VALID_PROFILES)}")
        return v

    @field_validator("runtime_mode")
    @classmethod
    def _runtime_valid(cls, v: str) -> str:
        if v not in VALID_RUNTIME:
            raise ValueError(f"must be one of {sorted(VALID_RUNTIME)}")
        return v

    @field_validator("api_version")
    @classmethod
    def _api_version_nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("permissions")
    @classmethod
    def _permissions_known(cls, v: list[str]) -> list[str]:
        unknown = set(v) - ALLOWED_PERMISSIONS
        if unknown:
            raise ValueError(f"unknown permissions: {sorted(unknown)}")
        return v

    @model_validator(mode="after")
    def _cross_field(self) -> "ModuleManifest":
        if self.type != "INTEGRATION":
            restricted = set(self.permissions) & INTEGRATION_ONLY_PERMISSIONS
            if restricted:
                raise ValueError(
                    f"permissions {sorted(restricted)} are only allowed for INTEGRATION type modules"
                )
        if self.type == "SYSTEM" and self.port is not None:
            raise ValueError("SYSTEM modules must not specify 'port' — they run in-process")
        return self
