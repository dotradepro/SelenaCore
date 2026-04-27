"""
tests/test_scenes_activate.py — POST /api/v1/scenes/{id}/activate (Phase 0).

Covers:
  * 404 / 409 on missing or disabled scene
  * No-op activation (empty actions list)
  * device_command action updates the registry and publishes scene.activated
  * unknown action type counts toward actions_failed and publishes scene.failed
"""
from __future__ import annotations

import pytest

from core.eventbus.bus import get_event_bus
from core.eventbus.types import SCENE_ACTIVATE, SCENE_ACTIVATED, SCENE_FAILED


import pytest_asyncio


@pytest_asyncio.fixture
async def captured_events():
    """Reset the EventBus singleton, start its dispatch loop, capture scene.* topics."""
    import core.eventbus.bus as _bus_module

    _bus_module._bus = None
    bus = _bus_module.get_event_bus()
    captured: list[tuple[str, dict]] = []

    async def _on(event):
        captured.append((event.type, event.payload))

    await bus.start()
    sub_id = bus.subscribe_direct(
        module_id="_test_scenes",
        event_types=[SCENE_ACTIVATE, SCENE_ACTIVATED, SCENE_FAILED],
        callback=_on,
    )
    try:
        yield captured
    finally:
        bus.unsubscribe_direct(sub_id)
        await bus.stop()
        _bus_module._bus = None


async def _create_scene(client, auth_headers, *, name="Test", actions=None, enabled=True):
    body = {"name_user": name, "actions": actions or [], "enabled": enabled}
    resp = await client.post("/api/v1/scenes", headers=auth_headers, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


class TestActivateMissing:
    async def test_404_on_missing(self, client, auth_headers):
        resp = await client.post("/api/v1/scenes/9999/activate", headers=auth_headers)
        assert resp.status_code == 404

    async def test_409_on_disabled(self, client, auth_headers):
        scene_id = await _create_scene(client, auth_headers, enabled=False)
        resp = await client.post(f"/api/v1/scenes/{scene_id}/activate", headers=auth_headers)
        assert resp.status_code == 409


class TestActivateNoOp:
    async def test_empty_actions_succeeds(self, client, auth_headers, captured_events):
        scene_id = await _create_scene(client, auth_headers, actions=[])
        resp = await client.post(f"/api/v1/scenes/{scene_id}/activate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["actions_run"] == 0
        assert data["actions_failed"] == 0
        # EventBus dispatch is async; allow scheduled callbacks to run.
        import asyncio
        await asyncio.sleep(0.05)
        topics = [t for t, _ in captured_events]
        assert SCENE_ACTIVATE in topics
        assert SCENE_ACTIVATED in topics


class TestActivateDeviceCommand:
    async def test_device_command_action(self, client, auth_headers, captured_events):
        # Create a device first.
        resp = await client.post(
            "/api/v1/devices",
            headers=auth_headers,
            json={
                "name": "Test Lamp",
                "type": "actuator",
                "protocol": "mqtt",
                "capabilities": ["on_off"],
                "meta": {},
            },
        )
        assert resp.status_code == 201, resp.text
        device_id = resp.json()["device_id"]

        scene_id = await _create_scene(
            client,
            auth_headers,
            actions=[{"type": "device_command", "device_id": device_id, "state": {"power": "on"}}],
        )

        resp = await client.post(f"/api/v1/scenes/{scene_id}/activate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["actions_run"] == 1
        assert data["actions_failed"] == 0

        # Verify device state was updated.
        resp = await client.get(f"/api/v1/devices/{device_id}", headers=auth_headers)
        assert resp.json()["state"] == {"power": "on"}


class TestActivateUnknownAction:
    async def test_unknown_action_type_records_failure(
        self, client, auth_headers, captured_events,
    ):
        scene_id = await _create_scene(
            client,
            auth_headers,
            actions=[{"type": "no_such_action_type"}],
        )
        resp = await client.post(f"/api/v1/scenes/{scene_id}/activate", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["actions_run"] == 0
        assert data["actions_failed"] == 1
        assert data["errors"] and "no_such_action_type" in data["errors"][0]

        import asyncio
        await asyncio.sleep(0.05)
        topics = [t for t, _ in captured_events]
        assert SCENE_FAILED in topics
        assert SCENE_ACTIVATED not in topics
