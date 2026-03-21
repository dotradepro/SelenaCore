"""
tests/test_core_api.py — Core API endpoints + auth + rate limiting tests
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch


# ---- Fixtures ----

@pytest.fixture
def core_app():
    """Return FastAPI test client for the Core API."""
    from core.main import app
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-module-token-xyz"}


# ---- Health endpoint ----

class TestHealth:
    def test_health_returns_ok(self, core_app):
        resp = core_app.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"


# ---- Auth ----

class TestAuth:
    def test_missing_token_returns_401(self, core_app):
        resp = core_app.get("/api/v1/devices")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, core_app):
        resp = core_app.get("/api/v1/devices", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401

    def test_valid_dev_token_allowed(self, core_app, auth_headers):
        resp = core_app.get("/api/v1/devices", headers=auth_headers)
        assert resp.status_code == 200


# ---- Devices ----

class TestDevices:
    def test_list_devices_empty(self, core_app, auth_headers):
        resp = core_app.get("/api/v1/devices", headers=auth_headers)
        assert resp.status_code == 200
        assert "devices" in resp.json()

    def test_create_and_get_device(self, core_app, auth_headers):
        payload = {
            "name": "Test Light",
            "device_type": "light",
            "protocol": "test",
            "address": "192.168.1.99",
            "state": "off",
        }
        create_resp = core_app.post("/api/v1/devices", json=payload, headers=auth_headers)
        assert create_resp.status_code in (200, 201)
        device = create_resp.json()
        assert device["name"] == "Test Light"

        device_id = device["id"]
        get_resp = core_app.get(f"/api/v1/devices/{device_id}", headers=auth_headers)
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Test Light"

    def test_get_nonexistent_device_404(self, core_app, auth_headers):
        resp = core_app.get("/api/v1/devices/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404


# ---- Events ----

class TestEvents:
    def test_publish_event(self, core_app, auth_headers):
        resp = core_app.post(
            "/api/v1/events/publish",
            json={"event_type": "device.state_changed", "payload": {"device_id": "123", "state": "on"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_publish_core_event_blocked(self, core_app, auth_headers):
        """Modules must not be able to publish core.* events."""
        resp = core_app.post(
            "/api/v1/events/publish",
            json={"event_type": "core.shutdown", "payload": {}},
            headers=auth_headers,
        )
        assert resp.status_code == 403


# ---- System info ----

class TestSystem:
    def test_system_info(self, core_app, auth_headers):
        resp = core_app.get("/api/v1/system/info", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "platform" in data or "version" in data or "uptime" in data
