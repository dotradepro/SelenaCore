"""End-to-end tests for SatelliteWSHub with a fake WebSocket.

Uses a minimal async stub for FastAPI's WebSocket so we can drive frame
roundtrips without spinning up the ASGI app.
"""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

if sys.version_info < (3, 10):
    pytest.skip(
        "ws_hub needs Python 3.10+ (fastapi/starlette import chain)",
        allow_module_level=True,
    )

pytest.importorskip("fastapi")

import pytest_asyncio  # noqa: E402

from system_modules.satellite_manager.auth import issue_token  # noqa: E402
from system_modules.satellite_manager.protocol import (  # noqa: E402
    Flags,
    Frame,
    MsgType,
    make_audio_chunk,
)
from system_modules.satellite_manager.ws_hub import SatelliteWSHub  # noqa: E402


SECRET = "test-hub-secret-xyz"
DEV_ID = "sat_aabbcc112233"


class FakeWebSocket:
    """Bare-minimum WebSocket stand-in — enough for ws_hub's receive loop."""

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self.sent: list[bytes] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self._disconnect_after_drain = False

    async def accept(self) -> None:
        pass

    async def receive_bytes(self) -> bytes:
        from fastapi import WebSocketDisconnect
        if self._disconnect_after_drain and self._incoming.empty():
            raise WebSocketDisconnect(code=1000)
        data = await self._incoming.get()
        return data

    async def send_bytes(self, data: bytes) -> None:
        if self.closed:
            raise RuntimeError("socket closed")
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    # Test helpers
    def inject(self, data: bytes) -> None:
        self._incoming.put_nowait(data)

    def inject_disconnect(self) -> None:
        self._disconnect_after_drain = True


class FakeRegistry:
    def __init__(self, device: dict | None) -> None:
        self._device = device
        self.online_history: list[tuple[str, bool]] = []
        self.state_updates: list[tuple[str, dict]] = []

    async def get(self, device_id: str) -> dict | None:
        return self._device

    async def set_online(self, device_id: str, online: bool) -> None:
        self.online_history.append((device_id, online))

    async def update_state(self, device_id: str, updates: dict) -> None:
        self.state_updates.append((device_id, updates))


class FakeAudioBridge:
    def __init__(self) -> None:
        self.wakes: list[tuple[str, str, str | None]] = []
        self.chunks: list[tuple[str, bytes]] = []
        self.ends: list[str] = []

    async def on_wake(self, session_id: str, device_id: str, location: str | None) -> None:
        self.wakes.append((session_id, device_id, location))

    async def on_audio_chunk(self, session_id: str, pcm_data: bytes) -> None:
        self.chunks.append((session_id, pcm_data))

    async def on_audio_end(self, session_id: str) -> None:
        self.ends.append(session_id)


@pytest_asyncio.fixture
async def hub_and_ws():
    device = {
        "device_id": DEV_ID,
        "name": "Kitchen sat",
        "location": "kitchen",
        "state": {"online": False, "volume": 60, "muted": False},
    }
    registry = FakeRegistry(device)
    bridge = FakeAudioBridge()
    hub = SatelliteWSHub(registry=registry, audio_bridge=bridge, hub_secret=SECRET)
    ws = FakeWebSocket()
    yield hub, ws, registry, bridge


async def _run_handler(hub: SatelliteWSHub, ws: FakeWebSocket, token: str) -> None:
    await hub.handle_connection(ws, token=token, device_id=DEV_ID)


async def test_unauthorized_token_closes_without_accept(hub_and_ws):
    hub, ws, registry, _ = hub_and_ws
    await hub.handle_connection(ws, token="bogus", device_id=DEV_ID)
    assert ws.closed is True
    assert ws.close_code == 4001
    assert registry.online_history == []


async def test_unknown_device_rejected(hub_and_ws):
    hub, ws, _, _ = hub_and_ws
    # Swap in a registry that knows no devices
    hub._registry = FakeRegistry(None)
    token = issue_token(DEV_ID, SECRET)
    await hub.handle_connection(ws, token=token, device_id=DEV_ID)
    assert ws.closed is True
    assert ws.close_code == 4004


async def test_wake_chunk_end_roundtrip(hub_and_ws):
    hub, ws, registry, bridge = hub_and_ws
    token = issue_token(DEV_ID, SECRET)

    # Queue: WAKE_DETECTED → AUDIO_CHUNK → AUDIO_END → disconnect
    ws.inject(Frame(MsgType.WAKE_DETECTED, Flags.NONE, b"").pack())
    ws.inject(make_audio_chunk(b"\x11\x22" * 100))
    ws.inject(Frame(MsgType.AUDIO_END, Flags.NONE, b"").pack())
    ws.inject_disconnect()

    await _run_handler(hub, ws, token)

    # Session marked online on connect, offline on disconnect
    assert (DEV_ID, True) in registry.online_history
    assert (DEV_ID, False) in registry.online_history

    # Audio bridge saw all three events
    assert len(bridge.wakes) == 1
    wake_sid, wake_device, wake_location = bridge.wakes[0]
    assert wake_device == DEV_ID
    assert wake_location == "kitchen"
    assert bridge.chunks == [(wake_sid, b"\x11\x22" * 100)]
    assert bridge.ends == [wake_sid]

    # Hub sent CONFIG (on connect) + STATE listening + STATE processing
    state_frames = []
    config_frames = []
    for raw in ws.sent:
        frame, _ = Frame.unpack(raw)
        if frame.msg_type is MsgType.CONFIG:
            config_frames.append(json.loads(frame.payload))
        elif frame.msg_type is MsgType.STATE:
            state_frames.append(json.loads(frame.payload))
    assert len(config_frames) == 1
    assert config_frames[0]["location"] == "kitchen"
    states = [s["state"] for s in state_frames]
    assert "listening" in states
    assert "processing" in states


async def test_heartbeat_updates_registry_and_responds_with_pong(hub_and_ws):
    hub, ws, registry, _ = hub_and_ws
    token = issue_token(DEV_ID, SECRET)

    hb = Frame(MsgType.HEARTBEAT, Flags.JSON,
               json.dumps({"rssi": -42, "firmware": "1.2.3"}).encode()).pack()
    ws.inject(hb)
    ws.inject_disconnect()

    await _run_handler(hub, ws, token)

    assert any(
        upd == {"rssi": -42, "online": True}
        for _, upd in registry.state_updates
    )
    # Last frame sent should be a PONG
    pong_seen = False
    for raw in ws.sent:
        frame, _ = Frame.unpack(raw)
        if frame.msg_type is MsgType.PONG:
            pong_seen = True
    assert pong_seen


async def test_send_tts_chunk_sends_state_then_chunk_on_first_call(hub_and_ws):
    """On the first TTS chunk, hub must prepend a STATE frame carrying
    sample_rate so the ESP32 can configure its DAC before receiving PCM."""
    hub, ws, _, bridge = hub_and_ws
    token = issue_token(DEV_ID, SECRET)

    # Open the WS, start a wake so session_by_sid[...] is populated,
    # then disconnect.
    ws.inject(Frame(MsgType.WAKE_DETECTED, Flags.NONE, b"").pack())
    ws.inject_disconnect()
    task = asyncio.create_task(_run_handler(hub, ws, token))
    # give the loop a chance to pick up the frame
    for _ in range(20):
        if bridge.wakes:
            break
        await asyncio.sleep(0)
    assert bridge.wakes, "wake should have been processed"
    wake_sid = bridge.wakes[0][0]
    await task  # finish the disconnect path

    # Replace ws with a live one again — simulate the WS still open to send TTS
    ws2 = FakeWebSocket()
    hub._session_by_sid[wake_sid].ws = ws2  # type: ignore[attr-defined]
    hub._session_by_sid[wake_sid].state = "processing"  # type: ignore[attr-defined]

    await hub.send_tts_chunk(wake_sid, b"\xaa" * 50, sample_rate=22050)

    # Expect: STATE speaking with sample_rate=22050, then TTS_CHUNK
    assert len(ws2.sent) == 2
    state_frame, _ = Frame.unpack(ws2.sent[0])
    assert state_frame.msg_type is MsgType.STATE
    assert json.loads(state_frame.payload) == {"state": "speaking", "sample_rate": 22050}

    tts_frame, _ = Frame.unpack(ws2.sent[1])
    assert tts_frame.msg_type is MsgType.TTS_CHUNK
    assert tts_frame.payload == b"\xaa" * 50

    # Second call should NOT re-advertise the STATE — only a TTS_CHUNK
    await hub.send_tts_chunk(wake_sid, b"\xbb" * 50, sample_rate=22050)
    assert len(ws2.sent) == 3
    tts_frame2, _ = Frame.unpack(ws2.sent[2])
    assert tts_frame2.msg_type is MsgType.TTS_CHUNK


async def test_mic_test_captures_wake_session_instead_of_bridging(hub_and_ws):
    """When a mic test is armed, the next WAKE + AUDIO_CHUNK + AUDIO_END
    burst is captured locally and reported — never forwarded to the bridge."""
    hub, ws, _, bridge = hub_and_ws
    token = issue_token(DEV_ID, SECRET)

    # Run the WS handler in the background while we drive frames into it.
    handler_task = asyncio.create_task(_run_handler(hub, ws, token))
    # Wait for the session to be registered
    for _ in range(50):
        if DEV_ID in hub._sessions:
            break
        await asyncio.sleep(0)
    assert DEV_ID in hub._sessions

    # Arm the mic test, then inject a wake burst
    arm_task = asyncio.create_task(hub.arm_mic_test(DEV_ID, timeout_s=3.0))
    # Let arm set _mic_test_future before injecting wake
    await asyncio.sleep(0)

    pcm = b"\x10\x20" * 8000  # ~1s @ 16 kHz
    ws.inject(Frame(MsgType.WAKE_DETECTED, Flags.NONE, b"").pack())
    ws.inject(Frame(MsgType.AUDIO_CHUNK, Flags.BINARY, pcm).pack())
    ws.inject(Frame(MsgType.AUDIO_END, Flags.NONE, b"").pack())

    result = await arm_task

    assert result["status"] == "audio_end"
    assert result["samples"] == len(pcm) // 2
    assert result["duration_ms"] > 0
    # Never leaked to the voice pipeline
    assert bridge.wakes == []
    assert bridge.chunks == []
    assert bridge.ends == []

    # Cleanly drain the handler
    ws.inject_disconnect()
    await handler_task


async def test_mic_test_times_out_when_no_wake(hub_and_ws):
    hub, ws, _, bridge = hub_and_ws
    token = issue_token(DEV_ID, SECRET)

    handler_task = asyncio.create_task(_run_handler(hub, ws, token))
    for _ in range(50):
        if DEV_ID in hub._sessions:
            break
        await asyncio.sleep(0)

    result = await hub.arm_mic_test(DEV_ID, timeout_s=0.1)
    assert result["status"] == "timeout"
    assert result["samples"] == 0
    assert bridge.wakes == []

    ws.inject_disconnect()
    await handler_task


async def test_mic_test_offline_device():
    registry = FakeRegistry(None)
    bridge = FakeAudioBridge()
    hub = SatelliteWSHub(registry=registry, audio_bridge=bridge, hub_secret=SECRET)
    result = await hub.arm_mic_test("sat_ghostly", timeout_s=1.0)
    assert result["status"] == "offline"


async def test_send_tts_end_keep_session_open_keeps_mic_listening(hub_and_ws):
    """After a clarification question the satellite must stay in LISTENING,
    and the session must NOT be dropped from _session_by_sid — otherwise the
    user's reply wouldn't route to the same session_id."""
    hub, ws, _, bridge = hub_and_ws
    token = issue_token(DEV_ID, SECRET)

    ws.inject(Frame(MsgType.WAKE_DETECTED, Flags.NONE, b"").pack())
    ws.inject_disconnect()
    task = asyncio.create_task(_run_handler(hub, ws, token))
    for _ in range(20):
        if bridge.wakes:
            break
        await asyncio.sleep(0)
    assert bridge.wakes
    wake_sid = bridge.wakes[0][0]
    await task

    # Re-attach a live WS so send_* can push more frames
    ws2 = FakeWebSocket()
    hub._session_by_sid[wake_sid].ws = ws2  # type: ignore[attr-defined]
    hub._session_by_sid[wake_sid].active_session_id = wake_sid  # type: ignore[attr-defined]
    hub._session_by_sid[wake_sid].state = "speaking"  # type: ignore[attr-defined]

    await hub.send_tts_end(wake_sid, keep_session_open=True)

    # Session NOT popped
    assert wake_sid in hub._session_by_sid
    assert hub._session_by_sid[wake_sid].state == "listening"
    # Frames: TTS_END + STATE listening (no idle)
    assert len(ws2.sent) == 2
    end_frame, _ = Frame.unpack(ws2.sent[0])
    state_frame, _ = Frame.unpack(ws2.sent[1])
    assert end_frame.msg_type is MsgType.TTS_END
    assert state_frame.msg_type is MsgType.STATE
    assert json.loads(state_frame.payload) == {"state": "listening"}


async def test_send_tts_end_default_closes_session(hub_and_ws):
    """Regression guard: default behavior still drops the session and
    transitions the satellite back to idle."""
    hub, ws, _, bridge = hub_and_ws
    token = issue_token(DEV_ID, SECRET)

    ws.inject(Frame(MsgType.WAKE_DETECTED, Flags.NONE, b"").pack())
    ws.inject_disconnect()
    task = asyncio.create_task(_run_handler(hub, ws, token))
    for _ in range(20):
        if bridge.wakes:
            break
        await asyncio.sleep(0)
    wake_sid = bridge.wakes[0][0]
    await task

    ws2 = FakeWebSocket()
    hub._session_by_sid[wake_sid].ws = ws2  # type: ignore[attr-defined]
    await hub.send_tts_end(wake_sid)  # default keep_session_open=False

    assert wake_sid not in hub._session_by_sid
    # Frames: TTS_END + STATE idle
    assert any(
        Frame.unpack(raw)[0].msg_type is MsgType.STATE
        and json.loads(Frame.unpack(raw)[0].payload) == {"state": "idle"}
        for raw in ws2.sent
    )
