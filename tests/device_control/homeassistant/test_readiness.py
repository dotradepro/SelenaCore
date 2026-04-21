"""Readiness aggregator tests.

The aggregator is thin — most of the interesting logic lives in the
extractors, which have their own dedicated suite. Here we verify the
tallying rules and the dedup behaviour for aggregated ``needs``.
"""
from __future__ import annotations

from system_modules.device_control.importers.homeassistant import readiness
from system_modules.device_control.importers.homeassistant.types import HADevice


def _dev(integration: str, **extra) -> HADevice:
    base = dict(
        id=f"d-{integration}",
        name=f"Device-{integration}",
        area=None,
        integration=integration,
        entry_id="e",
        entry_data={},
        entry_options={},
    )
    base.update(extra)
    return HADevice(**base)


def test_build_counts_green_yellow_red():
    devices = [
        _dev("esphome", entry_data={"host": "1.2.3.4"}),                 # green
        _dev("tuya", identifiers=[["tuya", "bf1"]]),                     # yellow
        _dev("zwave_js", identifiers=[["zwave_js", "node-7"]]),          # red
        _dev("mqtt", identifiers=[["mqtt", "uid"]]),                     # yellow
    ]
    report = readiness.build(devices)
    assert report.green == 1
    assert report.yellow == 2
    assert report.red == 1
    assert len(report.rows) == 4


def test_build_respects_context_for_tuya_extractor():
    devices = [
        _dev("tuya", identifiers=[["tuya", "bf1"]]),
    ]
    ctx = {
        "tuya_devices_by_id": {
            "bf1": {"id": "bf1", "local_key": "x", "version": "3.3",
                    "category": "dj", "product_name": "Bulb"},
        },
    }
    report = readiness.build(devices, ctx)
    assert report.green == 1
    assert report.rows[0].protocol == "tuya_local"


def test_unknown_integration_counted_as_red():
    devices = [_dev("yeelight")]
    report = readiness.build(devices)
    assert report.red == 1
    assert report.rows[0].status == "unsupported"


def test_aggregate_needs_dedupes_across_rows():
    devices = [
        _dev("mqtt", identifiers=[["mqtt", "a"]]),
        _dev("mqtt", identifiers=[["mqtt", "b"]]),
        _dev("zigbee2mqtt", name="bulb1"),
        _dev("tuya", identifiers=[["tuya", "x"]]),
    ]
    report = readiness.build(devices)
    needs = readiness.aggregate_needs(report)
    # mqtt_broker appears on 3 rows (mqtt x2 + z2m), tuya_cloud_creds once.
    assert "mqtt_broker" in needs
    assert "tuya_cloud_creds" in needs
    assert len(needs) == len(set(needs))


def test_report_as_dict_is_json_safe():
    devices = [_dev("esphome", entry_data={"host": "1.2.3.4"})]
    report = readiness.build(devices)
    d = report.as_dict()
    assert d["green"] == 1
    assert isinstance(d["rows"], list)
    assert d["rows"][0]["status"] == "ok"
    # All top-level values should be JSON-serialisable primitives.
    import json
    json.dumps(d)  # must not raise


def test_row_uses_fallback_display_name_when_device_name_empty():
    devices = [_dev("esphome", name="", entry_data={"host": "1.2.3.4"})]
    report = readiness.build(devices)
    assert report.rows[0].ha_device_name == "(esphome)"
