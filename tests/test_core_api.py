"""
tests/test_core_api.py — Core API endpoints + auth + rate limiting tests
"""
from __future__ import annotations

import pytest


# ---- Health endpoint ----

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "mode" in data
        assert "uptime" in data


# ---- Auth ----

class TestAuth:
    @pytest.mark.asyncio
    async def test_missing_token_returns_401(self, client):
        resp = await client.get("/api/v1/devices")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self, client):
        resp = await client.get(
            "/api/v1/devices",
            headers={"Authorization": "Bearer bad-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_dev_token_allowed(self, client, auth_headers):
        resp = await client.get("/api/v1/devices", headers=auth_headers)
        assert resp.status_code == 200


# ---- Devices ----

class TestDevices:
    @pytest.mark.asyncio
    async def test_list_devices_empty(self, client, auth_headers):
        resp = await client.get("/api/v1/devices", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "devices" in data
        assert isinstance(data["devices"], list)

    @pytest.mark.asyncio
    async def test_create_and_get_device(self, client, auth_headers):
        payload = {
            "name": "Test Light",
            "type": "actuator",
            "protocol": "test",
            "capabilities": ["on_off"],
            "meta": {"address": "192.168.1.99"},
        }
        create_resp = await client.post(
            "/api/v1/devices", json=payload, headers=auth_headers,
        )
        assert create_resp.status_code == 201
        device = create_resp.json()
        assert device["name"] == "Test Light"
        assert "device_id" in device

        device_id = device["device_id"]
        get_resp = await client.get(
            f"/api/v1/devices/{device_id}", headers=auth_headers,
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Test Light"

    @pytest.mark.asyncio
    async def test_get_nonexistent_device_404(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/devices/nonexistent-id", headers=auth_headers,
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_device_state(self, client, auth_headers):
        payload = {
            "name": "Lamp",
            "type": "actuator",
            "protocol": "test",
            "capabilities": ["on_off"],
            "meta": {},
        }
        create_resp = await client.post(
            "/api/v1/devices", json=payload, headers=auth_headers,
        )
        device_id = create_resp.json()["device_id"]

        patch_resp = await client.patch(
            f"/api/v1/devices/{device_id}/state",
            json={"state": {"on": True}},
            headers=auth_headers,
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["state"]["on"] is True

    @pytest.mark.asyncio
    async def test_delete_device(self, client, auth_headers):
        payload = {
            "name": "Deletable",
            "type": "sensor",
            "protocol": "test",
            "capabilities": [],
            "meta": {},
        }
        create_resp = await client.post(
            "/api/v1/devices", json=payload, headers=auth_headers,
        )
        device_id = create_resp.json()["device_id"]

        del_resp = await client.delete(
            f"/api/v1/devices/{device_id}", headers=auth_headers,
        )
        assert del_resp.status_code == 204

        get_resp = await client.get(
            f"/api/v1/devices/{device_id}", headers=auth_headers,
        )
        assert get_resp.status_code == 404


# ---- Events ----

class TestEvents:
    @pytest.mark.asyncio
    async def test_publish_event(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/events/publish",
            json={
                "type": "device.state_changed",
                "source": "test-module",
                "payload": {"device_id": "123", "state": "on"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "event_id" in data
        assert data["type"] == "device.state_changed"

    @pytest.mark.asyncio
    async def test_publish_core_event_blocked(self, client, auth_headers):
        """Modules must not be able to publish core.* events."""
        resp = await client.post(
            "/api/v1/events/publish",
            json={
                "type": "core.shutdown",
                "source": "evil-module",
                "payload": {},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_subscribe_events(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/events/subscribe",
            json={
                "event_types": ["device.state_changed"],
                "webhook_url": "http://localhost:8100/hook",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "subscription_id" in data


# ---- System info ----

class TestSystem:
    @pytest.mark.asyncio
    async def test_system_info(self, client):
        resp = await client.get("/api/v1/system/info")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
