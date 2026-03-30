"""
tests/test_api.py — Core API endpoint tests (health, devices, events)
"""
from __future__ import annotations

import pytest


class TestHealth:
    async def test_health_no_auth(self, client):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.3.0-beta"
        assert data["mode"] in ("normal", "safe_mode")
        assert data["integrity"] in ("ok", "violated", "restoring")
        assert isinstance(data["uptime"], int)


class TestDevicesAuth:
    async def test_unauthorized_no_token(self, client):
        resp = await client.get("/api/v1/devices")
        assert resp.status_code == 401

    async def test_unauthorized_bad_token(self, client):
        resp = await client.get(
            "/api/v1/devices",
            headers={"Authorization": "Bearer invalid-token-xxx"},
        )
        assert resp.status_code == 401


class TestDevicesCRUD:
    async def test_create_device(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/devices",
            headers=auth_headers,
            json={
                "name": "Test Sensor",
                "type": "sensor",
                "protocol": "mqtt",
                "capabilities": ["read_temperature"],
                "meta": {"location": "kitchen"},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Test Sensor"
        assert data["type"] == "sensor"
        assert data["device_id"] is not None
        assert data["state"] == {}

    async def test_list_devices(self, client, auth_headers):
        # Create one first
        await client.post(
            "/api/v1/devices",
            headers=auth_headers,
            json={"name": "Lamp", "type": "actuator", "protocol": "zigbee", "capabilities": [], "meta": {}},
        )
        resp = await client.get("/api/v1/devices", headers=auth_headers)
        assert resp.status_code == 200
        assert "devices" in resp.json()
        assert len(resp.json()["devices"]) >= 1

    async def test_get_device_not_found(self, client, auth_headers):
        resp = await client.get("/api/v1/devices/nonexistent-uuid", headers=auth_headers)
        assert resp.status_code == 404

    async def test_update_device_state(self, client, auth_headers):
        create_resp = await client.post(
            "/api/v1/devices",
            headers=auth_headers,
            json={"name": "Thermostat", "type": "actuator", "protocol": "zigbee", "capabilities": [], "meta": {}},
        )
        device_id = create_resp.json()["device_id"]

        patch_resp = await client.patch(
            f"/api/v1/devices/{device_id}/state",
            headers=auth_headers,
            json={"state": {"temperature": 22.5, "mode": "heat"}},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["state"]["temperature"] == 22.5

    async def test_delete_device(self, client, auth_headers):
        create_resp = await client.post(
            "/api/v1/devices",
            headers=auth_headers,
            json={"name": "Temp", "type": "sensor", "protocol": "mqtt", "capabilities": [], "meta": {}},
        )
        device_id = create_resp.json()["device_id"]

        del_resp = await client.delete(f"/api/v1/devices/{device_id}", headers=auth_headers)
        assert del_resp.status_code == 204

        get_resp = await client.get(f"/api/v1/devices/{device_id}", headers=auth_headers)
        assert get_resp.status_code == 404

    async def test_invalid_device_type(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/devices",
            headers=auth_headers,
            json={"name": "Bad", "type": "invalid_type", "protocol": "mqtt", "capabilities": [], "meta": {}},
        )
        assert resp.status_code == 422


class TestEvents:
    async def test_publish_event(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/events/publish",
            headers=auth_headers,
            json={
                "type": "device.custom_event",
                "source": "test-module",
                "payload": {"key": "value"},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["type"] == "device.custom_event"
        assert "event_id" in data
        assert "timestamp" in data

    async def test_core_event_forbidden(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/events/publish",
            headers=auth_headers,
            json={
                "type": "core.integrity_violation",
                "source": "evil-module",
                "payload": {},
            },
        )
        assert resp.status_code == 403
        assert "forbidden" in resp.json()["detail"].lower()

    async def test_subscribe_events(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/events/subscribe",
            headers=auth_headers,
            json={
                "event_types": ["device.state_changed", "device.offline"],
                "webhook_url": "http://localhost:8100/webhook/events",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "subscription_id" in data
        assert data["event_types"] == ["device.state_changed", "device.offline"]
