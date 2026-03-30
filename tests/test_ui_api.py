"""
tests/test_ui_api.py — UI API endpoint tests (no auth required)
"""
from __future__ import annotations

import pytest


class TestUISystem:
    async def test_system_no_auth_needed(self, client):
        resp = await client.get("/api/ui/system")
        assert resp.status_code == 200
        data = resp.json()
        assert "core" in data
        assert "hardware" in data
        assert data["core"]["status"] == "ok"
        assert data["core"]["version"] == "0.3.0-beta"
        assert isinstance(data["hardware"]["ram_total_mb"], (int, float))


class TestUIWizard:
    async def test_wizard_status(self, client):
        resp = await client.get("/api/ui/wizard/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "completed" in data

    async def test_wizard_requirements(self, client):
        resp = await client.get("/api/ui/wizard/requirements")
        assert resp.status_code == 200
        data = resp.json()
        assert "can_proceed" in data
        assert "wizard_completed" in data
        assert "steps" in data
        assert "language" in data["steps"]

    async def test_wizard_step_submit(self, client):
        resp = await client.post(
            "/api/ui/wizard/step",
            json={"step": "language", "data": {"language": "en"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["step"] == "language"
        assert data["status"] == "ok"
        assert data["next_step"] == "wifi"

    async def test_wizard_step_unknown(self, client):
        resp = await client.post(
            "/api/ui/wizard/step",
            json={"step": "nonexistent", "data": {}},
        )
        assert resp.status_code == 400


class TestUIDevices:
    async def test_list_devices_no_auth(self, client):
        resp = await client.get("/api/ui/devices")
        assert resp.status_code == 200
        data = resp.json()
        assert "devices" in data
        assert isinstance(data["devices"], list)

    async def test_device_crud_via_ui(self, client, auth_headers):
        # Create via v1 (needs auth)
        create_resp = await client.post(
            "/api/v1/devices",
            headers=auth_headers,
            json={
                "name": "UI Test Lamp",
                "type": "actuator",
                "protocol": "mqtt",
                "capabilities": ["toggle"],
                "meta": {},
            },
        )
        assert create_resp.status_code == 201
        device_id = create_resp.json()["device_id"]

        # List via UI (no auth)
        list_resp = await client.get("/api/ui/devices")
        assert list_resp.status_code == 200
        devices = list_resp.json()["devices"]
        assert any(d["device_id"] == device_id for d in devices)

        # Update state via UI
        state_resp = await client.patch(
            f"/api/ui/devices/{device_id}/state",
            json={"state": {"on": True}},
        )
        assert state_resp.status_code == 200
        assert state_resp.json()["state"]["on"] is True

    async def test_update_nonexistent_device(self, client):
        resp = await client.patch(
            "/api/ui/devices/nonexistent-id/state",
            json={"state": {"on": True}},
        )
        assert resp.status_code == 404


class TestUIModules:
    async def test_list_modules_no_auth(self, client):
        resp = await client.get("/api/ui/modules")
        assert resp.status_code == 200
        data = resp.json()
        assert "modules" in data
        assert isinstance(data["modules"], list)
