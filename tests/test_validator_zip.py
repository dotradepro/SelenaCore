"""
tests/test_validator_zip.py — Module ZIP archive validation tests
"""
from __future__ import annotations

import json
import zipfile
import pytest
from pathlib import Path

from core.module_loader.validator import validate_zip, validate_manifest, ValidationResult


class TestValidateZip:
    def _make_zip(self, tmp_path: Path, manifest: dict, name: str = "module.zip") -> Path:
        zip_path = tmp_path / name
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("main.py", "print('hello')")
        return zip_path

    def test_valid_zip(self, tmp_path):
        manifest = {
            "name": "my-module",
            "version": "1.0.0",
            "type": "UI",
            "api_version": "1",
            "permissions": ["device.read"],
        }
        result = validate_zip(self._make_zip(tmp_path, manifest))
        assert result.valid is True

    def test_missing_file(self):
        result = validate_zip(Path("/nonexistent/module.zip"))
        assert result.valid is False
        assert any("not found" in e.lower() for e in result.errors)

    def test_not_a_zip(self, tmp_path):
        f = tmp_path / "notzip.zip"
        f.write_text("this is not a zip file")
        result = validate_zip(f)
        assert result.valid is False
        assert any("zip" in e.lower() for e in result.errors)

    def test_missing_manifest(self, tmp_path):
        zip_path = tmp_path / "no_manifest.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("main.py", "print('hello')")
        result = validate_zip(zip_path)
        assert result.valid is False
        assert any("manifest" in e.lower() for e in result.errors)

    def test_invalid_manifest_json(self, tmp_path):
        zip_path = tmp_path / "bad_json.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("manifest.json", "{invalid json!}")
        result = validate_zip(zip_path)
        assert result.valid is False

    def test_zip_with_bad_manifest_data(self, tmp_path):
        manifest = {
            "name": "BAD NAME!",
            "version": "not-semver",
            "type": "UNKNOWN",
            "api_version": "1",
            "permissions": [],
        }
        result = validate_zip(self._make_zip(tmp_path, manifest))
        assert result.valid is False
        assert len(result.errors) > 0


class TestValidationResult:
    def test_result_structure(self):
        r = ValidationResult(valid=True, errors=[], manifest={"name": "test"})
        assert r.valid is True
        assert r.errors == []
        assert r.manifest == {"name": "test"}

    def test_invalid_result(self):
        r = ValidationResult(valid=False, errors=["something wrong"])
        assert r.valid is False
        assert r.manifest is None
