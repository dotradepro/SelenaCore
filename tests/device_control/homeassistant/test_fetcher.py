"""Tests for the HA fetcher.

The fetcher is pure glue code — it wires a small number of HA WS commands
to our normalised HADevice shape. We stub HAClient with a canned-response
double so the tests assert the join semantics (area lookups, per-device
entities, integration resolution, MQTT broker extraction) without a real
WebSocket.
"""
from __future__ import annotations

import pytest

from system_modules.device_control.importers.homeassistant import fetcher


class _StubClient:
    """Stand-in for HAClient that returns canned responses for each
    ``send_command`` call. The responses dict is keyed by the HA wire
    message type."""
    def __init__(self, responses: dict[str, list | dict | None]) -> None:
        self._responses = responses
        self.calls: list[str] = []
        self.ha_version = "2025.2.0"

    async def send_command(self, msg_type: str, **_kw):
        self.calls.append(msg_type)
        if msg_type not in self._responses:
            raise RuntimeError(f"unmocked command {msg_type}")
        return self._responses[msg_type]


@pytest.mark.asyncio
async def test_fetch_all_joins_devices_entities_areas_and_entries():
    client = _StubClient({
        "config/device_registry/list": [
            {
                "id": "dev-1",
                "name": "Kitchen Bulb",
                "name_by_user": None,
                "area_id": "area-kitchen",
                "config_entries": ["entry-hue"],
                "identifiers": [["hue", "bridge1/light-7"]],
                "manufacturer": "Signify",
                "model": "LCT024",
            },
            {
                "id": "dev-2",
                "name": "ESP Relay",
                "area_id": "area-bedroom",
                "config_entries": ["entry-esphome"],
                "identifiers": [["esphome", "mac:aabbcc"]],
            },
            # Device with no integration — should be filtered out.
            {"id": "dev-orphan", "name": "Group", "config_entries": []},
        ],
        "config/entity_registry/list": [
            {"entity_id": "light.kitchen_bulb", "unique_id": "u1",
             "device_id": "dev-1", "platform": "hue"},
            {"entity_id": "switch.relay_1", "unique_id": "u2",
             "device_id": "dev-2", "platform": "esphome"},
        ],
        "config/area_registry/list": [
            {"area_id": "area-kitchen", "name": "Kitchen"},
            {"area_id": "area-bedroom", "name": "Bedroom"},
        ],
        "config_entries/get": [
            {"entry_id": "entry-hue", "domain": "hue",
             "data": {"host": "192.168.1.254", "api_key": "tok"}},
            {"entry_id": "entry-esphome", "domain": "esphome",
             "data": {"host": "esp.local", "port": 6053}},
        ],
    })
    out = await fetcher.fetch_all(client)

    assert out["ha_version"] == "2025.2.0"
    devices = out["devices"]
    assert len(devices) == 2   # orphan filtered

    by_id = {d.id: d for d in devices}
    kitchen = by_id["dev-1"]
    assert kitchen.integration == "hue"
    assert kitchen.area == "Kitchen"
    assert kitchen.entry_data["host"] == "192.168.1.254"
    assert len(kitchen.entities) == 1
    assert kitchen.entities[0].entity_id == "light.kitchen_bulb"
    assert kitchen.identifiers == [["hue", "bridge1/light-7"]]

    esp = by_id["dev-2"]
    assert esp.integration == "esphome"
    assert esp.area == "Bedroom"
    assert esp.entry_data["port"] == 6053


@pytest.mark.asyncio
async def test_fetch_all_falls_back_when_config_entries_get_missing():
    """Old HA deployments only expose ``config_entries/list``. The fetcher
    must try the modern shape first but degrade gracefully."""
    class _FallbackStub(_StubClient):
        async def send_command(self, msg_type: str, **_kw):
            if msg_type == "config_entries/get":
                raise RuntimeError("unknown command")
            return await super().send_command(msg_type)

    client = _FallbackStub({
        "config/device_registry/list": [
            {"id": "d1", "name": "X", "config_entries": ["e1"],
             "identifiers": [["esphome", "x"]]},
        ],
        "config/entity_registry/list": [],
        "config/area_registry/list": [],
        "config_entries/list": [
            {"entry_id": "e1", "domain": "esphome",
             "data": {"host": "x.local"}},
        ],
    })
    out = await fetcher.fetch_all(client)
    assert len(out["devices"]) == 1
    assert out["devices"][0].integration == "esphome"


@pytest.mark.asyncio
async def test_fetch_all_surfaces_mqtt_broker_from_config_entries():
    client = _StubClient({
        "config/device_registry/list": [
            {"id": "d1", "name": "Thermostat", "config_entries": ["mqtt-entry"],
             "identifiers": [["mqtt", "therm-1"]]},
        ],
        "config/entity_registry/list": [],
        "config/area_registry/list": [],
        "config_entries/get": [
            {"entry_id": "mqtt-entry", "domain": "mqtt",
             "data": {"broker": "mqtt.lan", "port": 1883,
                      "username": "u", "password": "p"}},
        ],
    })
    out = await fetcher.fetch_all(client)
    broker = out["mqtt_broker"]
    assert broker is not None
    assert broker["broker_host"] == "mqtt.lan"
    assert broker["username"] == "u"


@pytest.mark.asyncio
async def test_fetch_all_returns_no_broker_when_mqtt_absent():
    client = _StubClient({
        "config/device_registry/list": [],
        "config/entity_registry/list": [],
        "config/area_registry/list": [],
        "config_entries/get": [
            {"entry_id": "e", "domain": "hue", "data": {"host": "x"}},
        ],
    })
    out = await fetcher.fetch_all(client)
    assert out["mqtt_broker"] is None


def test_integrations_summary_counts_per_domain():
    from system_modules.device_control.importers.homeassistant.types import HADevice
    devices = [
        HADevice(id="1", name="", area=None, integration="tuya",
                 entry_id="e", entry_data={}, entry_options={}),
        HADevice(id="2", name="", area=None, integration="tuya",
                 entry_id="e", entry_data={}, entry_options={}),
        HADevice(id="3", name="", area=None, integration="hue",
                 entry_id="e", entry_data={}, entry_options={}),
    ]
    assert fetcher.integrations_summary(devices) == {"tuya": 2, "hue": 1}


def test_supported_integrations_matches_extractor_registry():
    from system_modules.device_control.importers.homeassistant import extractors
    assert fetcher.supported_integrations() == set(extractors.known_integrations())
