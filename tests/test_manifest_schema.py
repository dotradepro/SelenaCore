"""
tests/test_manifest_schema.py — Pydantic ModuleManifest schema (Phase 0).

Covers the dashboard-recraft additions: required `room` field, widget kind/template
consistency, data_endpoints/actions structure, and template-vs-size envelopes.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.module_loader.manifest_schema import ModuleManifest
from core.module_loader.validator import validate_manifest


def _base_manifest(**overrides):
    base = {
        "name": "test-module",
        "version": "1.0.0",
        "type": "SYSTEM",
        "api_version": "1.0",
        "permissions": ["device.read"],
        "room": "system",
    }
    base.update(overrides)
    return base


class TestRequiredFields:
    def test_minimal_valid(self):
        m = ModuleManifest.model_validate(_base_manifest())
        assert m.name == "test-module"
        assert m.room == "system"

    def test_missing_room_rejected(self):
        manifest = _base_manifest()
        del manifest["room"]
        result = validate_manifest(manifest)
        assert result.valid is False
        assert any("room" in e for e in result.errors)

    def test_empty_room_rejected(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(_base_manifest(room=""))


class TestNameVersion:
    def test_bad_name(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(_base_manifest(name="BAD NAME"))

    def test_bad_semver(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(_base_manifest(version="1.0"))

    def test_empty_api_version(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(_base_manifest(api_version=""))


class TestTypeAndPermissions:
    def test_unknown_type(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(_base_manifest(type="UNKNOWN"))

    def test_unknown_permission(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(_base_manifest(permissions=["bogus.perm"]))

    def test_integration_only_permission_on_system(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(
                _base_manifest(permissions=["device.read", "secrets.oauth"])
            )

    def test_integration_only_permission_on_integration(self):
        m = ModuleManifest.model_validate(
            _base_manifest(type="INTEGRATION", permissions=["secrets.oauth"])
        )
        assert "secrets.oauth" in m.permissions


class TestPortDeprecation:
    def test_system_with_port_rejected(self):
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(_base_manifest(port=8080))

    def test_integration_with_port_allowed(self):
        m = ModuleManifest.model_validate(_base_manifest(type="INTEGRATION", port=8080))
        assert m.port == 8080


class TestWidget:
    def test_kind_template_requires_template_field(self):
        manifest = _base_manifest(ui={"widget": {"kind": "template"}})
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_kind_template_with_template_ok(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "metric", "size": "1x1"}}
        )
        m = ModuleManifest.model_validate(manifest)
        assert m.ui.widget.kind == "template"
        assert m.ui.widget.template == "metric"

    def test_kind_custom_default(self):
        manifest = _base_manifest(ui={"widget": {"file": "widget.html", "size": "2x2"}})
        m = ModuleManifest.model_validate(manifest)
        assert m.ui.widget.kind == "custom"

    def test_template_metric_too_large(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "metric", "size": "4x2"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_template_control_panel_too_small(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "control-panel", "size": "1x1"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_template_sparkline_too_tall(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "sparkline", "size": "2x4"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_template_sparkline_ok_at_4x2(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "sparkline", "size": "4x2"}}
        )
        m = ModuleManifest.model_validate(manifest)
        assert m.ui.widget.template == "sparkline"

    def test_template_toggle_list_too_small(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "toggle-list", "size": "1x1"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_template_status_too_large(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "status", "size": "4x4"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_template_weather_ok_at_4x2(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "weather", "size": "4x2"}}
        )
        m = ModuleManifest.model_validate(manifest)
        assert m.ui.widget.template == "weather"

    def test_template_weather_too_small(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "weather", "size": "1x1"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_template_media_too_small(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "media", "size": "1x1"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_template_media_ok_at_4x2(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "media", "size": "4x2"}}
        )
        m = ModuleManifest.model_validate(manifest)
        assert m.ui.widget.template == "media"

    def test_template_presence_ok_at_2x1(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "presence", "size": "2x1"}}
        )
        m = ModuleManifest.model_validate(manifest)
        assert m.ui.widget.template == "presence"

    def test_template_presence_too_narrow(self):
        manifest = _base_manifest(
            ui={"widget": {"kind": "template", "template": "presence", "size": "1x2"}}
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_invalid_size_format(self):
        manifest = _base_manifest(ui={"widget": {"size": "huge"}})
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)


class TestDataEndpointsAndActions:
    def test_data_endpoints_path_required(self):
        manifest = _base_manifest(
            ui={
                "widget": {
                    "kind": "template",
                    "template": "metric",
                    "size": "1x1",
                    "data_endpoints": {"state": {}},
                }
            }
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)

    def test_data_endpoints_well_formed(self):
        manifest = _base_manifest(
            ui={
                "widget": {
                    "kind": "template",
                    "template": "toggle-list",
                    "size": "2x2",
                    "data_endpoints": {"state": {"path": "/widget/data/state", "cache_ttl_s": 5}},
                    "actions": {"toggle": {"path": "/widget/action/toggle"}},
                }
            }
        )
        m = ModuleManifest.model_validate(manifest)
        assert m.ui.widget.data_endpoints["state"].path == "/widget/data/state"
        assert m.ui.widget.actions["toggle"].path == "/widget/action/toggle"

    def test_refresh_poll_interval_must_be_positive(self):
        manifest = _base_manifest(
            ui={
                "widget": {
                    "kind": "template",
                    "template": "metric",
                    "size": "1x1",
                    "refresh": {"poll_interval_s": 0},
                }
            }
        )
        with pytest.raises(ValidationError):
            ModuleManifest.model_validate(manifest)


class TestSystemManifestsLoadable:
    """Smoke test: every shipped system_modules manifest must validate."""

    def test_all_system_manifests_valid(self):
        from pathlib import Path
        import json

        root = Path(__file__).resolve().parent.parent / "system_modules"
        manifests = sorted(root.glob("*/manifest.json"))
        assert manifests, "no system_modules/*/manifest.json files found"

        failures: list[str] = []
        for path in manifests:
            data = json.loads(path.read_text())
            try:
                ModuleManifest.model_validate(data)
            except ValidationError as exc:
                failures.append(f"{path.parent.name}: {exc}")
        assert not failures, "\n".join(failures)
