"""Unit tests for satellite_manager.protocol."""
from __future__ import annotations

import json

import pytest

from system_modules.satellite_manager.protocol import (
    HEADER_SIZE,
    MAX_PAYLOAD,
    Flags,
    Frame,
    MsgType,
    make_audio_chunk,
    make_config,
    make_led,
    make_ping,
    make_pong,
    make_state,
    make_tts_chunk,
    make_tts_end,
    make_volume,
)


def test_pack_unpack_roundtrip_binary():
    payload = b"\x00\x01\x02\x03\xff" * 100
    packed = Frame(MsgType.AUDIO_CHUNK, Flags.BINARY, payload).pack()
    frame, consumed = Frame.unpack(packed)
    assert consumed == len(packed)
    assert frame.msg_type is MsgType.AUDIO_CHUNK
    assert frame.flags == Flags.BINARY
    assert frame.payload == payload


def test_pack_unpack_roundtrip_json():
    cfg = {"state": "listening"}
    packed = make_state("listening")
    frame, consumed = Frame.unpack(packed)
    assert consumed == len(packed)
    assert frame.msg_type is MsgType.STATE
    assert frame.flags == Flags.JSON
    assert json.loads(frame.payload) == cfg


def test_empty_payload():
    packed = make_tts_end()
    assert len(packed) == HEADER_SIZE
    frame, consumed = Frame.unpack(packed)
    assert consumed == HEADER_SIZE
    assert frame.msg_type is MsgType.TTS_END
    assert frame.payload == b""


def test_header_is_little_endian():
    packed = Frame(MsgType.AUDIO_CHUNK, Flags.BINARY, b"\xaa" * 257).pack()
    # length=257 → 0x0101 → little endian: 0x01 0x01
    assert packed[0] == int(MsgType.AUDIO_CHUNK)
    assert packed[1] == int(Flags.BINARY)
    assert packed[2] == 0x01
    assert packed[3] == 0x01


def test_unpack_rejects_short_header():
    with pytest.raises(ValueError, match="incomplete header"):
        Frame.unpack(b"\x01\x02")


def test_unpack_rejects_short_payload():
    # header claims 10 bytes but only 2 are present
    header = b"\x01\x02\x0a\x00"
    with pytest.raises(ValueError, match="incomplete payload"):
        Frame.unpack(header + b"\x00\x00")


def test_pack_rejects_oversized_payload():
    oversized = b"\x00" * (MAX_PAYLOAD + 1)
    with pytest.raises(ValueError, match="payload too large"):
        Frame(MsgType.AUDIO_CHUNK, Flags.BINARY, oversized).pack()


def test_max_payload_size_is_accepted():
    payload = b"\x00" * MAX_PAYLOAD
    packed = Frame(MsgType.AUDIO_CHUNK, Flags.BINARY, payload).pack()
    frame, consumed = Frame.unpack(packed)
    assert consumed == HEADER_SIZE + MAX_PAYLOAD
    assert len(frame.payload) == MAX_PAYLOAD


def test_streaming_multiple_frames():
    """Hub will receive concatenated frames and must split them."""
    f1 = make_audio_chunk(b"\x11" * 10)
    f2 = make_audio_chunk(b"\x22" * 20)
    f3 = make_tts_end()
    buf = bytearray(f1 + f2 + f3)

    frames = []
    while len(buf) >= HEADER_SIZE:
        frame, consumed = Frame.unpack(bytes(buf))
        frames.append(frame)
        buf = buf[consumed:]

    assert len(frames) == 3
    assert frames[0].msg_type is MsgType.AUDIO_CHUNK
    assert frames[0].payload == b"\x11" * 10
    assert frames[1].payload == b"\x22" * 20
    assert frames[2].msg_type is MsgType.TTS_END
    assert len(buf) == 0


def test_streaming_partial_frame_not_consumed():
    """If the buffer has a complete frame plus a partial next one, only the complete one is consumed."""
    complete = make_audio_chunk(b"\xaa" * 5)
    partial = make_audio_chunk(b"\xbb" * 5)[:6]  # truncated mid-payload
    buf = bytes(complete + partial)

    frame, consumed = Frame.unpack(buf)
    assert consumed == len(complete)
    assert frame.payload == b"\xaa" * 5

    with pytest.raises(ValueError, match="incomplete payload"):
        Frame.unpack(buf[consumed:])


def test_make_audio_chunk():
    pcm = b"\x01\x02\x03\x04"
    packed = make_audio_chunk(pcm)
    frame, _ = Frame.unpack(packed)
    assert frame.msg_type is MsgType.AUDIO_CHUNK
    assert frame.flags == Flags.BINARY
    assert frame.payload == pcm


def test_make_tts_chunk():
    pcm = b"\xaa" * 200
    frame, _ = Frame.unpack(make_tts_chunk(pcm))
    assert frame.msg_type is MsgType.TTS_CHUNK
    assert frame.payload == pcm


def test_make_config():
    cfg = {"location": "kitchen", "volume": 80, "wake_word_enabled": True}
    frame, _ = Frame.unpack(make_config(cfg))
    assert frame.msg_type is MsgType.CONFIG
    assert frame.flags == Flags.JSON
    assert json.loads(frame.payload) == cfg


def test_make_volume():
    frame, _ = Frame.unpack(make_volume(42))
    assert frame.msg_type is MsgType.VOLUME
    assert json.loads(frame.payload) == {"volume": 42}


def test_make_led_with_color():
    frame, _ = Frame.unpack(make_led("pulse", "#ff0000"))
    assert frame.msg_type is MsgType.LED
    assert json.loads(frame.payload) == {"pattern": "pulse", "color": "#ff0000"}


def test_make_led_without_color():
    frame, _ = Frame.unpack(make_led("off"))
    assert frame.msg_type is MsgType.LED
    assert json.loads(frame.payload) == {"pattern": "off"}


def test_make_pong_and_ping():
    pong_frame, _ = Frame.unpack(make_pong())
    assert pong_frame.msg_type is MsgType.PONG
    assert pong_frame.payload == b""

    ping_frame, _ = Frame.unpack(make_ping())
    assert ping_frame.msg_type is MsgType.PING


def test_unknown_msg_type_raises():
    raw = bytes([0xEE, 0, 0, 0])
    with pytest.raises(ValueError):
        Frame.unpack(raw)


def test_make_state_carries_extra_metadata():
    """STATE frames need to advertise TTS sample_rate; firmware reads it
    before the first TTS_CHUNK to set up its DAC."""
    frame, _ = Frame.unpack(make_state("speaking", sample_rate=22050, codec="pcm_s16le"))
    assert frame.msg_type is MsgType.STATE
    payload = json.loads(frame.payload)
    assert payload == {"state": "speaking", "sample_rate": 22050, "codec": "pcm_s16le"}


def test_make_state_without_metadata_is_backward_compatible():
    frame, _ = Frame.unpack(make_state("idle"))
    payload = json.loads(frame.payload)
    assert payload == {"state": "idle"}
