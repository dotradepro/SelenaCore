"""Driver tests — use a fake gateway so no real BLE is involved."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from system_modules.device_control.drivers.base import DriverError
from system_modules.device_control.drivers.plejd import PlejdDriver
from system_modules.device_control.plejd import gateway as gw_mod
from system_modules.device_control.plejd.gateway import PlejdEvent


class _FakeGateway:
    def __init__(self) -> None:
        self.subscribers = []
        self.sent_on: list[tuple[int, int | None]] = []
        self.sent_off: list[int] = []

    def subscribe(self, cb):
        self.subscribers.append(cb)

    async def send_on(self, output_address: int, dim_level: int | None = None):
        self.sent_on.append((output_address, dim_level))

    async def send_off(self, output_address: int):
        self.sent_off.append(output_address)


@pytest.fixture
def fake_gateway():
    fake = _FakeGateway()
    gw_mod.set_gateway(fake)
    yield fake
    gw_mod.set_gateway(None)


def _driver(output_address: int = 7, dimmable: bool = True) -> PlejdDriver:
    return PlejdDriver("dev-1", {"plejd": {
        "output_address": output_address,
        "dimmable": dimmable,
        "ble_address": "AA:BB:CC:DD:EE:01",
        "site_id": "sid-1",
    }})


# ── Meta validation ──────────────────────────────────────────────────────


def test_driver_requires_output_address_in_meta():
    with pytest.raises(DriverError, match="output_address"):
        PlejdDriver("dev-1", {"plejd": {}})


# ── Connect without gateway running ──────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_fails_when_gateway_not_running():
    gw_mod.set_gateway(None)
    d = _driver()
    with pytest.raises(DriverError, match="gateway not running"):
        await d.connect()


@pytest.mark.asyncio
async def test_connect_subscribes_and_returns_last_state(fake_gateway):
    d = _driver()
    state = await d.connect()
    assert state == {"on": False, "brightness": 0}
    assert len(fake_gateway.subscribers) == 1


@pytest.mark.asyncio
async def test_connect_for_nondimmable_has_no_brightness(fake_gateway):
    d = _driver(dimmable=False)
    state = await d.connect()
    assert state == {"on": False}


# ── set_state → gateway commands ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_state_on_without_brightness_sends_plain_on(fake_gateway):
    d = _driver(7)
    await d.connect()
    await d.set_state({"on": True})
    assert fake_gateway.sent_on == [(7, None)]
    assert not fake_gateway.sent_off


@pytest.mark.asyncio
async def test_set_state_with_brightness_scales_to_16_bit(fake_gateway):
    d = _driver(9)
    await d.connect()
    await d.set_state({"on": True, "brightness": 128})
    assert fake_gateway.sent_on == [(9, 128 << 8)]


@pytest.mark.asyncio
async def test_set_state_off_sends_off(fake_gateway):
    d = _driver(5)
    await d.connect()
    await d.set_state({"on": False})
    assert fake_gateway.sent_off == [5]
    assert not fake_gateway.sent_on


@pytest.mark.asyncio
async def test_brightness_on_nondimmable_is_ignored(fake_gateway):
    d = _driver(3, dimmable=False)
    await d.connect()
    await d.set_state({"on": True, "brightness": 200})
    assert fake_gateway.sent_on == [(3, None)]


# ── stream_events forwards notifications for matching output ─────────────


@pytest.mark.asyncio
async def test_stream_events_emits_state_for_matching_output(fake_gateway):
    d = _driver(11)
    await d.connect()
    cb = fake_gateway.subscribers[0]
    cb(PlejdEvent(output_address=11, on=True, dim_level=0x8000, raw=b""))
    cb(PlejdEvent(output_address=99, on=False, dim_level=None, raw=b""))  # other output
    cb(PlejdEvent(output_address=11, on=False, dim_level=None, raw=b""))

    stream = d.stream_events()
    first = await asyncio.wait_for(stream.__anext__(), timeout=0.5)
    second = await asyncio.wait_for(stream.__anext__(), timeout=0.5)
    assert first == {"on": True, "brightness": 0x80}
    assert second == {"on": False}


@pytest.mark.asyncio
async def test_get_state_returns_last_known(fake_gateway):
    d = _driver(4)
    await d.connect()
    await d.set_state({"on": True, "brightness": 64})
    assert await d.get_state() == {"on": True, "brightness": 64}
