"""Unit tests for the Matter driver — pure mappers, no WebSocket."""
from __future__ import annotations

import asyncio
import sys
import types

import pytest


# ── Stub matter_server / aiohttp before import ──────────────────────────────
#
# python-matter-server is an optional runtime dependency installed via the
# Providers tab. These tests target the pure-Python translation layer (cluster
# attribute → logical state and back), so we install lightweight stubs that
# satisfy the lazy import inside _MatterClientHolder.

def _install_stubs() -> None:
    if "aiohttp" not in sys.modules:
        aiohttp_mod = types.ModuleType("aiohttp")
        aiohttp_mod.ClientSession = lambda *a, **kw: None  # type: ignore[attr-defined]
        sys.modules["aiohttp"] = aiohttp_mod

    if "matter_server" not in sys.modules:
        ms_pkg = types.ModuleType("matter_server")
        client_pkg = types.ModuleType("matter_server.client")
        client_mod = types.ModuleType("matter_server.client.client")

        class _StubClient:
            def __init__(self, *a, **kw): ...
            async def connect(self): ...
            async def start_listening(self, *a, **kw): ...
            def subscribe_events(self, *a, **kw): ...
            async def get_node(self, node_id):
                return types.SimpleNamespace(node_id=node_id, attributes={})
            async def commission_with_code(self, code):
                return types.SimpleNamespace(node_id=42)
            async def remove_node(self, node_id): ...
            async def send_device_command(self, **kw):
                return None

        client_mod.MatterClient = _StubClient  # type: ignore[attr-defined]
        client_pkg.client = client_mod  # type: ignore[attr-defined]
        ms_pkg.client = client_pkg  # type: ignore[attr-defined]
        sys.modules["matter_server"] = ms_pkg
        sys.modules["matter_server.client"] = client_pkg
        sys.modules["matter_server.client.client"] = client_mod


_install_stubs()


# ── Imports ────────────────────────────────────────────────────────────────

from system_modules.device_control.drivers.base import DriverError  # noqa: E402
from system_modules.device_control.drivers.matter import (  # noqa: E402
    CLUSTER_MAP,
    MatterDriver,
    _MatterClientHolder,
    _decode_lock,
    _decode_temp,
    _encode_temp,
    _logical_to_matter,
)


# ── CLUSTER_MAP coverage ───────────────────────────────────────────────────


def test_cluster_map_has_all_required_logical_keys():
    logical_keys = {entry[0] for entry in CLUSTER_MAP.values()}
    # The keys called out in the spec.
    for key in (
        "on", "brightness", "colour_temp",
        "temperature", "target_temp", "hvac_mode",
        "locked", "contact",
    ):
        assert key in logical_keys, f"missing logical key {key!r}"


def test_decode_temperature_divides_by_100():
    assert _decode_temp(2150) == 21.5
    assert _decode_temp(0) == 0.0
    assert _decode_temp(-500) == -5.0


def test_encode_temperature_multiplies_and_int():
    assert _encode_temp(22.5) == 2250
    assert _encode_temp(21) == 2100
    assert isinstance(_encode_temp(22.5), int)


def test_decode_lock_state():
    # Matter Door Lock cluster: 1 = Locked, 2 = Unlocked.
    assert _decode_lock(1) is True
    assert _decode_lock(2) is False
    # Anything else (e.g. NotFullyLocked = 0) is treated as not-fully-locked.
    assert _decode_lock(0) is False


def test_translate_on_off_attribute():
    mapping = CLUSTER_MAP[(0x0006, "on_off")]
    logical_key, decode, encode = mapping
    assert logical_key == "on"
    assert decode(False) is False
    assert decode(True) is True
    assert encode is not None


def test_logical_to_matter_reverse_lookup_includes_target_temp():
    pairs = _logical_to_matter("target_temp")
    assert (0x0201, "occupied_heating_setpoint") in pairs


# ── Driver behaviour ───────────────────────────────────────────────────────


def test_connect_requires_node_id():
    drv = MatterDriver("dev-1", meta={"matter": {}})
    with pytest.raises(DriverError, match="node_id missing"):
        asyncio.run(drv.connect())


def test_translate_node_event_decodes_temperature():
    """Push events arrive as opaque dicts; verify the path → logical xform."""
    holder = _MatterClientHolder()
    fake_event = types.SimpleNamespace(
        node_id=4,
        data={"path": (1, 0x0201, "local_temperature"), "value": 2150},
    )
    delta = holder._translate_node_event(fake_event)
    assert delta == {"temperature": 21.5}


def test_translate_node_event_unknown_cluster_returns_none():
    holder = _MatterClientHolder()
    fake_event = types.SimpleNamespace(
        node_id=4,
        data={"path": (1, 0x9999, "unknown_attr"), "value": 1},
    )
    assert holder._translate_node_event(fake_event) is None


def test_translate_node_event_lock_state():
    holder = _MatterClientHolder()
    fake_event = types.SimpleNamespace(
        node_id=4,
        data={"path": (1, 0x0101, "lock_state"), "value": 1},
    )
    assert holder._translate_node_event(fake_event) == {"locked": True}

    fake_event.data["value"] = 2
    assert holder._translate_node_event(fake_event) == {"locked": False}


def test_register_unregister_device_isolates_queues():
    holder = _MatterClientHolder()
    q1 = holder.register_device("dev-A", node_id=1)
    q2 = holder.register_device("dev-B", node_id=2)
    assert q1 is not q2
    assert holder._node_for_device == {"dev-A": 1, "dev-B": 2}
    holder.unregister_device("dev-A")
    assert "dev-A" not in holder._node_for_device
    assert "dev-B" in holder._node_for_device


# ── Reconnect / sentinel propagation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_listener_death_pushes_sentinel_and_invalidates_singleton():
    """Simulate matter-server crash: every queue gets a sentinel and the
    singleton's _client is cleared so the next call rebuilds."""
    from system_modules.device_control.drivers.matter import (
        _Sentinel, _MatterClientHolder,
    )

    holder = _MatterClientHolder()
    holder._client = object()  # pretend we were connected

    q1 = holder.register_device("dev-A", node_id=1)
    q2 = holder.register_device("dev-B", node_id=2)

    # Build a fake client whose start_listening immediately raises, then
    # run _run_listener directly to exercise the cleanup branch.
    class _FakeClient:
        async def start_listening(self, init_ready):
            raise RuntimeError("matter-server died")

    class _FakeSession:
        async def close(self): pass

    await holder._run_listener(_FakeClient(), _FakeSession(), asyncio.Event())

    # Both queues received exactly one sentinel.
    assert q1.qsize() == 1
    assert q2.qsize() == 1
    item_a = q1.get_nowait()
    item_b = q2.get_nowait()
    assert isinstance(item_a, _Sentinel)
    assert isinstance(item_b, _Sentinel)
    assert isinstance(item_a.exc, RuntimeError) or "died" in str(item_a.exc)

    # Singleton was invalidated → next ensure_connected() must rebuild.
    assert holder._client is None
    assert holder._session is None
    assert holder._listen_task is None


@pytest.mark.asyncio
async def test_stream_events_raises_driver_error_on_sentinel():
    """The driver's stream_events generator must convert sentinels into
    DriverError so the watcher loop in DeviceControlModule kicks in."""
    drv = MatterDriver("dev-A", meta={"matter": {"node_id": 7, "endpoint": 1}})
    drv._queue = asyncio.Queue()

    from system_modules.device_control.drivers.matter import _Sentinel
    drv._queue.put_nowait(_Sentinel(RuntimeError("ws closed")))

    gen = drv.stream_events()
    with pytest.raises(DriverError, match="upstream lost"):
        await gen.__anext__()
