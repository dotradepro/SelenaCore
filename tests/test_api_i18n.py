"""Integration tests for /api/i18n/* endpoints (v0.4.0 A4a deliverable)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest_asyncio.fixture
async def client():
    """Wire the FastAPI app directly; no live server needed."""
    from core.main import app  # noqa: E402
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ─── /api/i18n/common ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_common_en_has_core_keys(client):
    r = await client.get("/api/i18n/common?lang=en")
    assert r.status_code == 200
    bundle = r.json()
    # Core common strings any widget relies on
    for key in ("save", "cancel", "loading", "error"):
        assert key in bundle
    assert bundle["save"] == "Save"


@pytest.mark.asyncio
async def test_common_uk_overrides_en(client):
    r = await client.get("/api/i18n/common?lang=uk")
    assert r.status_code == 200
    bundle = r.json()
    # Ukrainian translations present
    assert bundle["save"] == "Зберегти"
    assert bundle["cancel"] == "Скасувати"


@pytest.mark.asyncio
async def test_common_unknown_lang_falls_back_to_en(client):
    r = await client.get("/api/i18n/common?lang=xx")
    assert r.status_code == 200
    bundle = r.json()
    # No Polish/xx file exists under common/ → every key stays English
    assert bundle["save"] == "Save"


@pytest.mark.asyncio
async def test_common_invalid_lang_format_rejected(client):
    # Upper case / numbers / too long → 422 from pydantic
    r = await client.get("/api/i18n/common?lang=ENGLISH")
    assert r.status_code == 422


# ─── /api/i18n/bundle/{module} ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bundle_voice_core_en(client):
    r = await client.get("/api/i18n/bundle/voice-core?lang=en")
    assert r.status_code == 200
    bundle = r.json()
    # Voice-core module strings present (pilot migration landed them)
    assert "title" in bundle        # Voice & AI Settings
    assert "wake_title" in bundle
    # Common strings merged in too
    assert "save" in bundle


@pytest.mark.asyncio
async def test_bundle_voice_core_uk_translates_module_strings(client):
    r = await client.get("/api/i18n/bundle/voice-core?lang=uk")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["title"] == "Голос та AI"
    assert bundle["save"] == "Зберегти"


@pytest.mark.asyncio
async def test_bundle_rejects_invalid_module_name(client):
    # Path traversal attempt — module_name must match MODULE_NAME_RE.
    r = await client.get("/api/i18n/bundle/..etc?lang=en")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bundle_rejects_spaces_in_module_name(client):
    r = await client.get("/api/i18n/bundle/my%20module?lang=en")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bundle_unknown_module_returns_common_only(client):
    """Unknown module name is still technically valid per the regex; the
    endpoint then serves just the common tier (useful for new user-modules
    that haven't registered locales yet)."""
    r = await client.get("/api/i18n/bundle/nonexistent-module?lang=en")
    assert r.status_code == 200
    bundle = r.json()
    # Common strings always present
    assert "save" in bundle


@pytest.mark.asyncio
async def test_bundle_all_15_system_modules_uk(client):
    """Smoke-test every migrated system module serves a non-trivial UK bundle."""
    modules = [
        "voice-core", "device-control", "automation-engine", "climate",
        "media-player", "weather-service", "presence-detection", "user-manager",
        "device-watchdog", "energy-monitor", "lights-switches",
        "notification-router", "protocol-bridge", "scheduler", "update-manager",
    ]
    for mod in modules:
        r = await client.get(f"/api/i18n/bundle/{mod}?lang=uk")
        assert r.status_code == 200, f"{mod}: HTTP {r.status_code}"
        bundle = r.json()
        assert len(bundle) >= 30, f"{mod}: only {len(bundle)} keys merged"
        # Common must always be there
        assert "save" in bundle, f"{mod}: common strings missing"
