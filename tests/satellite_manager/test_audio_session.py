"""Unit tests for voice_core.audio_session — the per-satellite STT wrapper.

Covers the `SatelliteAudioSession.feed` / `.finalize` path with a mock Vosk
recognizer so we never need the real vosk package on the test host.
"""
from __future__ import annotations

import json

import pytest

from system_modules.voice_core.audio_session import (
    SatelliteAudioSession,
    create_session_recognizer,
)


class FakeRecognizer:
    """Stand-in for vosk.KaldiRecognizer."""

    def __init__(self) -> None:
        self.waveforms: list[bytes] = []
        self.final_result_payload = {"text": ""}
        self.partial_payload = {"partial": ""}
        # If True, AcceptWaveform returns True — Vosk reports a final segment
        self.accept_returns = False

    def AcceptWaveform(self, pcm: bytes) -> bool:
        self.waveforms.append(pcm)
        return self.accept_returns

    def Result(self) -> str:
        return json.dumps(self.final_result_payload)

    def PartialResult(self) -> str:
        return json.dumps(self.partial_payload)

    def FinalResult(self) -> str:
        return json.dumps(self.final_result_payload)


def _make_session(rec: FakeRecognizer) -> SatelliteAudioSession:
    return SatelliteAudioSession(
        session_id="sid-1",
        device_id="sat_aabbcc112233",
        location="kitchen",
        recognizer=rec,
    )


def test_feed_returns_partial_when_vosk_has_no_endpoint():
    rec = FakeRecognizer()
    rec.accept_returns = False
    rec.partial_payload = {"partial": "включи"}
    session = _make_session(rec)

    partial, final = session.feed(b"\x01\x02" * 100)
    assert partial == "включи"
    assert final is None
    assert rec.waveforms == [b"\x01\x02" * 100]


def test_feed_returns_final_when_vosk_endpoints_midstream():
    rec = FakeRecognizer()
    rec.accept_returns = True
    rec.final_result_payload = {"text": "включи світло"}
    session = _make_session(rec)

    partial, final = session.feed(b"\x00" * 10)
    assert partial is None
    assert final == "включи світло"


def test_feed_empty_text_returns_none():
    rec = FakeRecognizer()
    rec.accept_returns = True
    rec.final_result_payload = {"text": "   "}
    session = _make_session(rec)

    _, final = session.feed(b"\x00")
    assert final is None


def test_finalize_returns_vosk_final_result():
    rec = FakeRecognizer()
    rec.final_result_payload = {"text": "увімкни кухню"}
    session = _make_session(rec)

    assert session.finalize() == "увімкни кухню"
    assert session.finalized is True


def test_finalize_idempotent():
    rec = FakeRecognizer()
    rec.final_result_payload = {"text": "hello"}
    session = _make_session(rec)

    assert session.finalize() == "hello"
    # Second call — session is flagged finalized, returns empty without
    # touching the recognizer again
    rec.final_result_payload = {"text": "should not see this"}
    assert session.finalize() == ""


def test_feed_updates_last_chunk_at():
    rec = FakeRecognizer()
    session = _make_session(rec)
    before = session.last_chunk_at
    import time
    time.sleep(0.01)
    session.feed(b"\x00" * 10)
    assert session.last_chunk_at > before


def test_feed_swallows_recognizer_exceptions():
    """A broken recognizer shouldn't crash the whole event handler."""
    class BrokenRec:
        def AcceptWaveform(self, _pcm: bytes) -> bool:
            raise RuntimeError("vosk internal")
        def Result(self) -> str: return "{}"
        def PartialResult(self) -> str: return "{}"
        def FinalResult(self) -> str: return "{}"

    session = SatelliteAudioSession(
        session_id="x", device_id="y", location=None, recognizer=BrokenRec(),
    )
    partial, final = session.feed(b"\x00")
    assert partial is None and final is None


def test_create_session_recognizer_returns_none_without_model():
    """If the provider hasn't loaded its Vosk model yet, we fail soft."""
    class StubProvider:
        _model = None

    assert create_session_recognizer(StubProvider()) is None


def test_create_session_recognizer_returns_none_when_vosk_missing():
    """When vosk isn't installed, don't crash — just return None and let
    the caller drop the satellite wake with a clear log line."""
    class StubProvider:
        _model = object()  # truthy but not a real Vosk model

    # If vosk IS installed, this should succeed (or fail inside Vosk); either
    # way we only test the "no vosk" branch when the module is absent.
    import importlib
    try:
        importlib.import_module("vosk")
        pytest.skip("vosk is installed on this host; skipping no-vosk branch")
    except ImportError:
        assert create_session_recognizer(StubProvider()) is None


def test_reset_for_clarification_clears_finalized_flag():
    rec = FakeRecognizer()
    rec.accept_returns = True
    rec.final_result_payload = {"text": "first utterance"}
    session = _make_session(rec)

    # Complete the first utterance
    assert session.finalize() == "first utterance"
    assert session.finalized is True

    # Reset for a clarification reply
    session.reset_for_clarification()
    assert session.finalized is False

    # Second utterance now works again
    rec.final_result_payload = {"text": "kitchen"}
    assert session.finalize() == "kitchen"


def test_reset_calls_vosk_reset_when_available():
    """Ensure we exercise the Vosk Reset() path when the recognizer supports it."""
    class ResettableRec(FakeRecognizer):
        reset_count = 0
        def Reset(self) -> None:
            type(self).reset_count += 1

    rec = ResettableRec()
    session = SatelliteAudioSession(
        session_id="x", device_id="y", location=None, recognizer=rec,
    )
    session.finalize()
    session.reset_for_clarification()
    assert ResettableRec.reset_count == 1


def test_reset_is_no_op_if_recognizer_has_no_reset():
    """Some recognizer stubs may not implement Reset — must not raise."""
    class NoResetRec(FakeRecognizer):
        pass  # inherits FakeRecognizer, no Reset

    rec = NoResetRec()
    session = SatelliteAudioSession(
        session_id="x", device_id="y", location=None, recognizer=rec,
    )
    session.finalize()
    # Should not raise
    session.reset_for_clarification()
    assert session.finalized is False
