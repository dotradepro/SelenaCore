"""Gateway tests — fake BleakBackend so no real BT adapter is touched.

Covers:
    - pure frame encoders (deterministic, no state)
    - scan→connect→subscribe happy path
    - encryption of outgoing commands
    - reconnect after dropped connection
    - subscriber receives decoded events from notifications
"""
from __future__ import annotations

import asyncio

import pytest

from core.ble.arbiter import BLEArbiter
from system_modules.device_control.plejd import crypto, gateway
from system_modules.device_control.plejd.gateway import (
    PLEJD_DATA_UUID,
    PlejdEvent,
    PlejdGateway,
    _ScanResult,
    decode_event,
    encode_off,
    encode_on,
)


SITE_KEY = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
CONNECTED = bytes.fromhex("112233445566")


# ── Pure encoders ─────────────────────────────────────────────────────────


def test_encode_on_without_dim_level():
    frame = encode_on(5)
    assert frame == bytes([5, 0x01, 0x10, 0x00])


def test_encode_on_with_dim_level():
    frame = encode_on(7, 0xABCD)
    assert frame == bytes([7, 0x01, 0x10, 0x00, 0xAB, 0xCD])


def test_encode_off():
    frame = encode_off(3)
    assert frame == bytes([3, 0x01, 0x10, 0x00, 0x00, 0x00])


def test_encode_on_rejects_bad_addr():
    with pytest.raises(ValueError):
        encode_on(-1)
    with pytest.raises(ValueError):
        encode_on(256)


def test_encode_on_rejects_out_of_range_dim():
    with pytest.raises(ValueError):
        encode_on(1, 70000)


def test_decode_event_roundtrip_for_dim_on():
    # Build the cleartext frame the mesh would emit for dim-on at output 7.
    # Opcode 0x0098 in bytes 3-4, dim_level 0x8000 in bytes 5-6.
    cleartext = bytes([7, 0x00, 0x00, 0x00, 0x98, 0x80, 0x00])
    ciphertext = crypto.encrypt_decrypt(SITE_KEY, CONNECTED, cleartext)
    ev = decode_event(ciphertext, SITE_KEY, CONNECTED)
    assert ev is not None
    assert ev.output_address == 7
    assert ev.on is True
    assert ev.dim_level == 0x8000


def test_decode_event_roundtrip_for_off():
    cleartext = bytes([9, 0x00, 0x00, 0x00, 0x97, 0x00, 0x00])
    ciphertext = crypto.encrypt_decrypt(SITE_KEY, CONNECTED, cleartext)
    ev = decode_event(ciphertext, SITE_KEY, CONNECTED)
    assert ev is not None
    assert ev.output_address == 9
    assert ev.on is False


def test_decode_event_returns_none_for_unknown_opcode():
    cleartext = bytes([1, 0, 0, 0x11, 0x22, 0, 0])
    ct = crypto.encrypt_decrypt(SITE_KEY, CONNECTED, cleartext)
    assert decode_event(ct, SITE_KEY, CONNECTED) is None


def test_decode_event_ignores_empty():
    assert decode_event(b"", SITE_KEY, CONNECTED) is None


# ── Fake bleak backend + client ───────────────────────────────────────────


class _FakeClient:
    def __init__(self, addr: str) -> None:
        self.addr = addr
        self._alive = True
        self.writes: list[tuple[str, bytes]] = []
        self._notify_cb = None

    def is_connected(self):
        return self._alive

    async def write_gatt_char(self, uuid, data, response=True):
        self.writes.append((uuid, bytes(data)))

    async def start_notify(self, uuid, callback):
        assert uuid == PLEJD_DATA_UUID
        self._notify_cb = callback

    async def stop_notify(self, uuid):
        self._notify_cb = None

    async def disconnect(self):
        self._alive = False

    # Test hook: simulate a mesh notification.
    async def deliver(self, data: bytes) -> None:
        if self._notify_cb is not None:
            res = self._notify_cb("sender", bytes(data))
            if asyncio.iscoroutine(res):
                await res

    # Test hook: simulate the mesh dropping us.
    def drop(self) -> None:
        self._alive = False


class _FakeBackend(gateway.BleakBackend):
    def __init__(self, candidates: list[_ScanResult]) -> None:
        self._candidates = candidates
        self._next_client_fail = False
        self.scans = 0
        self.connects: list[str] = []
        self.current_client: _FakeClient | None = None

    async def scan(self, service_uuid, timeout):
        self.scans += 1
        return list(self._candidates)

    async def connect(self, ble_address):
        self.connects.append(ble_address)
        if self._next_client_fail:
            self._next_client_fail = False
            raise RuntimeError("simulated connect failure")
        self.current_client = _FakeClient(ble_address)
        return self.current_client


# ── Gateway happy path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_connects_to_strongest_rssi():
    backend = _FakeBackend([
        _ScanResult("AA:BB:CC:DD:EE:01", rssi=-80),
        _ScanResult("AA:BB:CC:DD:EE:02", rssi=-42),
        _ScanResult("AA:BB:CC:DD:EE:03", rssi=-60),
    ])
    arb = BLEArbiter()
    gw = PlejdGateway(site_key=SITE_KEY, backend=backend, arbiter=arb)
    await gw.start()
    # Wait until connect completes.
    for _ in range(50):
        if gw.is_connected():
            break
        await asyncio.sleep(0.02)
    assert gw.is_connected()
    assert backend.connects[-1] == "AA:BB:CC:DD:EE:02"
    await gw.stop()


@pytest.mark.asyncio
async def test_send_on_encrypts_frame_against_connected_addr():
    backend = _FakeBackend([_ScanResult("AA:BB:CC:DD:EE:01", rssi=-40)])
    arb = BLEArbiter()
    gw = PlejdGateway(site_key=SITE_KEY, backend=backend, arbiter=arb)
    await gw.start()
    for _ in range(50):
        if gw.is_connected():
            break
        await asyncio.sleep(0.02)
    await gw.send_on(11, 0x4000)
    await gw.stop()

    writes = backend.current_client.writes
    assert len(writes) == 1
    uuid, ciphertext = writes[0]
    assert uuid == PLEJD_DATA_UUID
    cleartext = crypto.encrypt_decrypt(
        SITE_KEY,
        crypto.parse_addr("AA:BB:CC:DD:EE:01"),
        ciphertext,
    )
    assert cleartext == encode_on(11, 0x4000)


@pytest.mark.asyncio
async def test_subscriber_receives_notification():
    backend = _FakeBackend([_ScanResult("AA:BB:CC:DD:EE:01", rssi=-40)])
    arb = BLEArbiter()
    gw = PlejdGateway(site_key=SITE_KEY, backend=backend, arbiter=arb)
    received: list[PlejdEvent] = []
    gw.subscribe(lambda ev: received.append(ev))
    await gw.start()
    for _ in range(50):
        if gw.is_connected():
            break
        await asyncio.sleep(0.02)
    # Deliver a cleartext frame encoded with the connected-address key.
    cleartext = bytes([7, 0x00, 0x00, 0x00, 0x98, 0x80, 0x00])
    ct = crypto.encrypt_decrypt(
        SITE_KEY, crypto.parse_addr("AA:BB:CC:DD:EE:01"), cleartext,
    )
    await backend.current_client.deliver(ct)
    await gw.stop()

    assert len(received) == 1
    assert received[0].output_address == 7
    assert received[0].on is True
    assert received[0].dim_level == 0x8000


@pytest.mark.asyncio
async def test_send_when_not_connected_raises():
    gw = PlejdGateway(site_key=SITE_KEY, backend=_FakeBackend([]), arbiter=BLEArbiter())
    with pytest.raises(RuntimeError, match="not connected"):
        await gw.send_on(1)


# ── Reconnect on drop ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gateway_reconnects_after_drop():
    backend = _FakeBackend([_ScanResult("AA:BB:CC:DD:EE:01", rssi=-40)])
    arb = BLEArbiter()
    gw = PlejdGateway(site_key=SITE_KEY, backend=backend, arbiter=arb)
    await gw.start()
    # First connection.
    for _ in range(50):
        if gw.is_connected():
            break
        await asyncio.sleep(0.02)
    assert backend.current_client is not None
    first_client = backend.current_client
    # Simulate a drop: current bleak client says is_connected() == False.
    first_client.drop()
    # Gateway should tear down and reconnect.
    for _ in range(300):   # up to ~6 s (reconnect min is 2 s)
        if (
            gw.is_connected()
            and backend.current_client is not None
            and backend.current_client is not first_client
        ):
            break
        await asyncio.sleep(0.02)
    assert backend.current_client is not first_client, (
        "gateway failed to reconnect after drop"
    )
    await gw.stop()


# ── Site-key length validation ────────────────────────────────────────────


def test_gateway_rejects_bad_site_key():
    with pytest.raises(ValueError):
        PlejdGateway(site_key=b"\x00" * 15)
