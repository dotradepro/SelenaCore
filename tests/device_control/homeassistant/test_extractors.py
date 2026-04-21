"""Unit tests for HA importer extractors.

Covers all six extractors (tuya, esphome, hue, mqtt, zigbee2mqtt,
zwave_js) plus the dispatch registry. No HA server, no aiohttp — just
synthetic HADevice records and dict contexts so the extractor contracts
stay deterministic.
"""
from __future__ import annotations

import pytest

from system_modules.device_control.importers.homeassistant import (
    extractors,
)
from system_modules.device_control.importers.homeassistant.types import (
    HADevice,
    HAEntity,
)


def _ha_device(
    *,
    integration: str,
    entry_data: dict | None = None,
    identifiers: list | None = None,
    entities: list[HAEntity] | None = None,
    name: str = "Device",
) -> HADevice:
    return HADevice(
        id="dev-1",
        name=name,
        area=None,
        integration=integration,
        entry_id="entry-1",
        entry_data=entry_data or {},
        entry_options={},
        identifiers=identifiers or [],
        entities=entities or [],
    )


# ── Registry + dispatch ───────────────────────────────────────────────────


def test_registry_covers_all_six_integrations():
    assert set(extractors.known_integrations()) == {
        "tuya", "esphome", "hue", "mqtt", "zigbee2mqtt", "zwave_js",
    }


def test_unknown_integration_returns_unsupported():
    d = _ha_device(integration="yeelight")
    result = extractors.extract(d)
    assert result.status == "unsupported"
    assert "yeelight" in result.reason


# ── Z-Wave: always unsupported ────────────────────────────────────────────


def test_zwave_js_is_always_unsupported():
    d = _ha_device(
        integration="zwave_js",
        identifiers=[["zwave_js", "node-42"]],
    )
    result = extractors.extract(d)
    assert result.status == "unsupported"
    assert "controller" in result.reason.lower()


# ── ESPHome ───────────────────────────────────────────────────────────────


def test_esphome_ok_with_minimal_entry_data():
    d = _ha_device(
        integration="esphome",
        entry_data={"host": "192.168.1.10", "port": 6053},
        entities=[HAEntity(entity_id="switch.relay_1", unique_id="r1", platform="esphome")],
    )
    r = extractors.extract(d)
    assert r.status == "ok"
    assert r.protocol == "esphome"
    assert r.credentials["host"] == "192.168.1.10"
    assert r.credentials["port"] == 6053
    assert r.entity_type == "switch"


def test_esphome_passes_through_password_and_noise_psk():
    d = _ha_device(
        integration="esphome",
        entry_data={
            "host": "10.0.0.5",
            "port": 6053,
            "password": "secret",
            "noise_psk": "base64-encryption-key",
        },
    )
    r = extractors.extract(d)
    assert r.credentials["password"] == "secret"
    assert r.credentials["encryption_key"] == "base64-encryption-key"


def test_esphome_without_host_is_unsupported():
    d = _ha_device(integration="esphome", entry_data={"port": 6053})
    r = extractors.extract(d)
    assert r.status == "unsupported"


def test_esphome_prefers_light_entity_type():
    d = _ha_device(
        integration="esphome",
        entry_data={"host": "1.2.3.4"},
        entities=[
            HAEntity(entity_id="light.main", unique_id="l1", platform="esphome"),
            HAEntity(entity_id="sensor.temp", unique_id="t1", platform="esphome"),
        ],
    )
    r = extractors.extract(d)
    assert r.entity_type == "light"


# ── Hue ───────────────────────────────────────────────────────────────────


def test_hue_ok_with_v2_style_identifiers():
    d = _ha_device(
        integration="hue",
        entry_data={"host": "192.168.1.254", "api_key": "abc-token", "bridge_id": "001788FF"},
        identifiers=[["hue", "001788FF/light-123"]],
    )
    r = extractors.extract(d)
    assert r.status == "ok"
    assert r.protocol == "philips_hue"
    assert r.entity_type == "light"
    assert r.credentials["api_host"] == "http://192.168.1.254"
    assert r.credentials["token"] == "abc-token"
    assert r.credentials["light_id"] == "light-123"
    assert r.credentials["bridge_id"] == "001788FF"


def test_hue_ok_with_v1_style_identifiers():
    d = _ha_device(
        integration="hue",
        entry_data={"host": "http://hue.local", "username": "tok"},
        identifiers=[["hue", "7"]],
    )
    r = extractors.extract(d)
    assert r.status == "ok"
    assert r.credentials["light_id"] == "7"
    assert r.credentials["api_host"] == "http://hue.local"


def test_hue_without_host_is_unsupported():
    d = _ha_device(
        integration="hue",
        entry_data={"api_key": "tok"},
        identifiers=[["hue", "1"]],
    )
    r = extractors.extract(d)
    assert r.status == "unsupported"


def test_hue_bridge_only_record_is_unsupported():
    d = _ha_device(
        integration="hue",
        entry_data={"host": "hue.local", "api_key": "tok"},
        identifiers=[],  # no hue identifier ⇒ bridge meta, not a light
    )
    r = extractors.extract(d)
    assert r.status == "unsupported"


# ── MQTT ──────────────────────────────────────────────────────────────────


def test_mqtt_needs_broker_when_context_empty():
    d = _ha_device(integration="mqtt", identifiers=[["mqtt", "unique-1"]])
    r = extractors.extract(d)
    assert r.status == "needs_user_input"
    assert "mqtt_broker" in r.needs


def test_mqtt_needs_override_when_broker_is_ha_addon():
    d = _ha_device(integration="mqtt", identifiers=[["mqtt", "unique-1"]])
    ctx = {"mqtt_broker": {"broker_host": "core-mosquitto", "broker_port": 1883}}
    r = extractors.extract(d, ctx)
    assert r.status == "needs_user_input"
    assert "mqtt_broker_override" in r.needs


def test_mqtt_ok_with_external_broker():
    d = _ha_device(integration="mqtt", identifiers=[["mqtt", "unique-1"]])
    ctx = {
        "mqtt_broker": {
            "broker_host": "mosquitto.lan",
            "broker_port": 1883,
            "username": "user",
            "password": "pw",
        },
    }
    r = extractors.extract(d, ctx)
    assert r.status == "ok"
    assert r.protocol == "mqtt"
    assert r.credentials["broker_host"] == "mosquitto.lan"
    assert r.credentials["unique_id"] == "unique-1"
    assert r.credentials["username"] == "user"


def test_mqtt_override_beats_ha_addon_broker():
    d = _ha_device(integration="mqtt", identifiers=[["mqtt", "u1"]])
    ctx = {
        "mqtt_broker": {"broker_host": "core-mosquitto", "broker_port": 1883},
        "mqtt_override": {"broker_host": "external.lan", "broker_port": 1883},
    }
    r = extractors.extract(d, ctx)
    assert r.status == "ok"
    assert r.credentials["broker_host"] == "external.lan"


def test_mqtt_without_unique_id_is_unsupported():
    d = _ha_device(integration="mqtt", identifiers=[])
    ctx = {"mqtt_broker": {"broker_host": "mosquitto.lan"}}
    r = extractors.extract(d, ctx)
    assert r.status == "unsupported"


# ── Zigbee2MQTT ───────────────────────────────────────────────────────────


def test_z2m_needs_broker_when_context_empty():
    d = _ha_device(integration="zigbee2mqtt", name="bulb_kitchen")
    r = extractors.extract(d)
    assert r.status == "needs_user_input"


def test_z2m_ok_with_external_broker():
    d = _ha_device(integration="zigbee2mqtt", name="bulb_kitchen")
    ctx = {
        "mqtt_broker": {
            "broker_host": "mosquitto.lan",
            "broker_port": 1883,
            "base_topic": "zigbee2mqtt",
        },
    }
    r = extractors.extract(d, ctx)
    assert r.status == "ok"
    assert r.protocol == "zigbee2mqtt"
    assert r.credentials["friendly_name"] == "bulb_kitchen"
    assert r.credentials["base_topic"] == "zigbee2mqtt"


def test_z2m_without_friendly_name_is_unsupported():
    d = _ha_device(integration="zigbee2mqtt", name="")
    ctx = {"mqtt_broker": {"broker_host": "mosquitto.lan"}}
    r = extractors.extract(d, ctx)
    assert r.status == "unsupported"


def test_z2m_flags_ha_addon_broker():
    d = _ha_device(integration="zigbee2mqtt", name="bulb_1")
    ctx = {"mqtt_broker": {"broker_host": "localhost", "broker_port": 1883}}
    r = extractors.extract(d, ctx)
    assert r.status == "needs_user_input"


# ── Tuya ──────────────────────────────────────────────────────────────────


def test_tuya_needs_cloud_creds_when_context_empty():
    d = _ha_device(integration="tuya", identifiers=[["tuya", "bf1234567890"]])
    r = extractors.extract(d)
    assert r.status == "needs_user_input"
    assert "tuya_cloud_creds" in r.needs


def test_tuya_ok_when_device_present_in_cloud_session():
    d = _ha_device(
        integration="tuya",
        identifiers=[["tuya", "bf1234567890"]],
    )
    ctx = {
        "tuya_devices_by_id": {
            "bf1234567890": {
                "id": "bf1234567890",
                "local_key": "deadbeef",
                "version": "3.3",
                "ip": "192.168.1.77",
                "category": "dj",
                "name": "Kitchen light",
                "product_name": "RGB bulb",
                "status": {"switch_led": True},
            },
        },
    }
    r = extractors.extract(d, ctx)
    assert r.status == "ok"
    assert r.protocol == "tuya_local"
    assert r.entity_type == "light"
    assert r.credentials["device_id"] == "bf1234567890"
    assert r.credentials["local_key"] == "deadbeef"
    assert r.credentials["ip"] == "192.168.1.77"


def test_tuya_unsupported_when_device_missing_from_cloud_session():
    d = _ha_device(integration="tuya", identifiers=[["tuya", "bf_missing"]])
    ctx = {"tuya_devices_by_id": {"bf_other": {"local_key": "x"}}}
    r = extractors.extract(d, ctx)
    assert r.status == "unsupported"
    assert "not in the connected" in r.reason


def test_tuya_unsupported_when_cloud_record_has_no_local_key():
    d = _ha_device(integration="tuya", identifiers=[["tuya", "bfid"]])
    ctx = {"tuya_devices_by_id": {"bfid": {"id": "bfid", "local_key": ""}}}
    r = extractors.extract(d, ctx)
    assert r.status == "unsupported"
    assert "no local_key" in r.reason


def test_tuya_unsupported_when_no_identifier():
    d = _ha_device(integration="tuya", identifiers=[])
    ctx = {"tuya_devices_by_id": {"x": {"local_key": "y"}}}
    r = extractors.extract(d, ctx)
    assert r.status == "unsupported"


# ── Determinism ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("integration", [
    "tuya", "esphome", "hue", "mqtt", "zigbee2mqtt", "zwave_js",
])
def test_extractor_callable_is_registered(integration):
    assert extractors.get(integration) is not None
