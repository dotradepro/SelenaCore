"""tests/test_speech_queue.py — Speech queue serialization & audio ducking tests."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.eventbus.bus import EventBus


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_voice_module():
    """Create VoiceCoreModule with mocked internals (no real audio)."""
    from system_modules.voice_core.module import VoiceCoreModule

    m = VoiceCoreModule.__new__(VoiceCoreModule)
    VoiceCoreModule.__init__(m)

    # Inject a real EventBus so publish/subscribe work
    bus = EventBus()
    m._bus = bus
    m._direct_sub_ids = []
    m._tts = MagicMock()

    return m, bus


def _make_media_module():
    """Create MediaPlayerModule with mocked VLC player."""
    from system_modules.media_player.module import MediaPlayerModule

    m = MediaPlayerModule.__new__(MediaPlayerModule)
    m._bus = None
    m._direct_sub_ids = []
    m._config = {}
    m._state_task = None
    m._pre_duck_volume = None
    m._duck_volume = 15
    m._duck_generation = 0

    # Mock player
    player = MagicMock()
    player._volume = 70
    player.get_state.return_value = "playing"
    player.set_volume = AsyncMock()
    m._player = player

    return m


# ── Queue serialization tests ───────────────────────────────────────────────

class TestSpeechQueueSerialization:
    """Verify that multiple TTS requests play one at a time, not in parallel."""

    @pytest.mark.asyncio
    async def test_sequential_playback_no_overlap(self):
        """10 concurrent voice.speak must play strictly sequentially."""
        m, bus = _make_voice_module()
        await bus.start()

        playback_log: list[tuple[str, str, float]] = []  # (text, event, time)
        active_count = 0
        max_concurrent = 0

        original_stream_speak = None

        async def mock_stream_speak(text: str, **kwargs) -> None:
            nonlocal active_count, max_concurrent
            active_count += 1
            max_concurrent = max(max_concurrent, active_count)
            playback_log.append((text, "start", time.monotonic()))
            await asyncio.sleep(0.02)  # simulate short TTS
            playback_log.append((text, "end", time.monotonic()))
            active_count -= 1

        m._stream_speak = mock_stream_speak
        m.publish = AsyncMock()

        # Start worker
        worker = asyncio.create_task(m._speech_worker())

        # Fire 10 speech requests concurrently
        done_events = []
        for i in range(10):
            ev = asyncio.Event()
            done_events.append(ev)
            await m._enqueue_speech(f"message {i}", priority=1, done_event=ev)

        # Wait for all to finish
        await asyncio.wait_for(
            asyncio.gather(*(e.wait() for e in done_events)),
            timeout=10.0,
        )

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await bus.stop()

        # Verify: never more than 1 playing at a time
        assert max_concurrent == 1, f"Overlap detected: max_concurrent={max_concurrent}"
        # Verify: all 10 played
        starts = [e for e in playback_log if e[1] == "start"]
        assert len(starts) == 10

    @pytest.mark.asyncio
    async def test_fifo_order_same_priority(self):
        """Items with the same priority must play in FIFO order."""
        m, bus = _make_voice_module()

        played: list[str] = []

        async def mock_stream_speak(text: str, **kwargs) -> None:
            played.append(text)

        m._stream_speak = mock_stream_speak
        m.publish = AsyncMock()

        worker = asyncio.create_task(m._speech_worker())

        events = []
        for i in range(5):
            ev = asyncio.Event()
            events.append(ev)
            await m._enqueue_speech(f"msg-{i}", priority=1, done_event=ev)

        await asyncio.wait_for(
            asyncio.gather(*(e.wait() for e in events)),
            timeout=5.0,
        )

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        assert played == [f"msg-{i}" for i in range(5)]

    @pytest.mark.asyncio
    async def test_high_priority_before_normal(self):
        """Priority 0 items should play before priority 1 items."""
        m, bus = _make_voice_module()

        played: list[str] = []
        gate = asyncio.Event()

        call_count = 0

        async def mock_stream_speak(text: str, **kwargs) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First item is playing; signal to enqueue the rest
                gate.set()
                await asyncio.sleep(0.05)
            played.append(text)

        m._stream_speak = mock_stream_speak
        m.publish = AsyncMock()

        worker = asyncio.create_task(m._speech_worker())

        # Enqueue a blocker first
        blocker_done = asyncio.Event()
        await m._enqueue_speech("blocker", priority=1, done_event=blocker_done)

        # Wait for blocker to start playing
        await asyncio.wait_for(gate.wait(), timeout=3.0)

        # Now enqueue: 2 normal + 1 high priority
        normal1_done = asyncio.Event()
        normal2_done = asyncio.Event()
        high_done = asyncio.Event()

        await m._enqueue_speech("normal-1", priority=1, done_event=normal1_done)
        await m._enqueue_speech("normal-2", priority=1, done_event=normal2_done)
        await m._enqueue_speech("HIGH", priority=0, done_event=high_done)

        await asyncio.wait_for(
            asyncio.gather(
                blocker_done.wait(), normal1_done.wait(),
                normal2_done.wait(), high_done.wait(),
            ),
            timeout=5.0,
        )

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        # HIGH should come right after blocker (before normal-1, normal-2)
        assert played[0] == "blocker"
        assert played[1] == "HIGH"


# ── Done event / completion tests ────────────────────────────────────────────

class TestSpeechQueueCompletion:

    @pytest.mark.asyncio
    async def test_done_event_set_on_success(self):
        """done_event must be set after successful playback."""
        m, bus = _make_voice_module()

        m._stream_speak = AsyncMock()
        m.publish = AsyncMock()

        worker = asyncio.create_task(m._speech_worker())

        done = asyncio.Event()
        await m._enqueue_speech("hello", priority=1, done_event=done)
        await asyncio.wait_for(done.wait(), timeout=3.0)

        assert done.is_set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_done_event_set_on_error(self):
        """done_event must be set even if _stream_speak raises."""
        m, bus = _make_voice_module()

        async def exploding_speak(text):
            raise RuntimeError("TTS crashed")

        m._stream_speak = exploding_speak
        m.publish = AsyncMock()

        worker = asyncio.create_task(m._speech_worker())

        done = asyncio.Event()
        await m._enqueue_speech("boom", priority=1, done_event=done)
        await asyncio.wait_for(done.wait(), timeout=3.0)

        assert done.is_set()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_queue_full_sets_done_event(self):
        """When queue is full, dropped item's done_event must still be set."""
        m, bus = _make_voice_module()

        # Fill queue to maxsize
        for i in range(200):
            m._speech_queue.put_nowait((1, time.monotonic(), f"fill-{i}", None))

        dropped_done = asyncio.Event()
        await m._enqueue_speech("overflow", priority=1, done_event=dropped_done)

        assert dropped_done.is_set(), "Dropped item's done_event must be set"


# ── TTS events (tts_start / tts_done) ───────────────────────────────────────

class TestSpeechQueueEvents:

    @pytest.mark.asyncio
    async def test_tts_start_and_done_published(self):
        """Worker must publish voice.tts_start before and voice.tts_done after playback."""
        m, bus = _make_voice_module()

        published: list[str] = []
        original_publish = m.publish

        async def track_publish(event_type, payload):
            published.append(event_type)

        m._stream_speak = AsyncMock()
        m.publish = track_publish

        worker = asyncio.create_task(m._speech_worker())

        done = asyncio.Event()
        await m._enqueue_speech("test", priority=1, done_event=done)
        await asyncio.wait_for(done.wait(), timeout=3.0)

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        assert "voice.tts_start" in published
        assert "voice.tts_done" in published
        # tts_start must come before tts_done
        assert published.index("voice.tts_start") < published.index("voice.tts_done")

    @pytest.mark.asyncio
    async def test_tts_done_published_on_error(self):
        """voice.tts_done must be published even when TTS crashes."""
        m, bus = _make_voice_module()

        published: list[str] = []

        async def track_publish(event_type, payload):
            published.append(event_type)

        async def crash_speak(text):
            raise RuntimeError("boom")

        m._stream_speak = crash_speak
        m.publish = track_publish

        worker = asyncio.create_task(m._speech_worker())

        done = asyncio.Event()
        await m._enqueue_speech("crash", priority=1, done_event=done)
        await asyncio.wait_for(done.wait(), timeout=3.0)

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        assert "voice.tts_done" in published

    @pytest.mark.asyncio
    async def test_chime_handled_via_sentinel(self):
        """__CHIME__ sentinel should call _play_chime_internal, not _stream_speak."""
        m, bus = _make_voice_module()

        m._stream_speak = AsyncMock()
        m._play_chime_internal = AsyncMock()
        m.publish = AsyncMock()

        worker = asyncio.create_task(m._speech_worker())

        done = asyncio.Event()
        await m._enqueue_speech("__CHIME__", priority=0, done_event=done)
        await asyncio.wait_for(done.wait(), timeout=3.0)

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

        m._play_chime_internal.assert_called_once()
        m._stream_speak.assert_not_called()


# ── Audio ducking tests ──────────────────────────────────────────────────────

class TestAudioDucking:

    @pytest.mark.asyncio
    async def test_duck_on_tts_start(self):
        """Media volume should drop to duck_volume on voice.tts_start."""
        m = _make_media_module()

        event = MagicMock()
        event.type = "voice.tts_start"
        event.payload = {"text": "hello"}

        await m._on_tts_start(event)

        assert m._pre_duck_volume == 70
        m._player.set_volume.assert_called_once_with(15)

    @pytest.mark.asyncio
    async def test_restore_on_tts_done(self):
        """Media volume should restore to original on voice.tts_done."""
        m = _make_media_module()
        m._pre_duck_volume = 70

        event = MagicMock()
        event.type = "voice.tts_done"
        event.payload = {"text": "hello"}

        await m._on_tts_done(event)

        m._player.set_volume.assert_called_with(70)
        assert m._pre_duck_volume is None

    @pytest.mark.asyncio
    async def test_no_duck_when_not_playing(self):
        """Should not duck if media player is not playing."""
        m = _make_media_module()
        m._player.get_state.return_value = "stopped"

        event = MagicMock()
        await m._on_tts_start(event)

        assert m._pre_duck_volume is None
        m._player.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_restore_when_not_ducked(self):
        """Should not restore if not currently ducked."""
        m = _make_media_module()
        m._pre_duck_volume = None

        event = MagicMock()
        await m._on_tts_done(event)

        m._player.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_stay_ducked_on_consecutive_tts(self):
        """Back-to-back TTS: should not restore between utterances."""
        m = _make_media_module()

        event = MagicMock()

        # First TTS starts
        await m._on_tts_start(event)
        assert m._pre_duck_volume == 70

        # First TTS done — starts grace period (300ms)
        done_task = asyncio.create_task(m._on_tts_done(event))
        await asyncio.sleep(0.05)  # within grace period

        # Second TTS starts before grace period ends
        await m._on_tts_start(event)

        # Original volume should still be saved (not overwritten with ducked volume)
        assert m._pre_duck_volume == 70

        # Let first _on_tts_done finish — should NOT restore since re-ducked
        await done_task

        # Volume should still be ducked (pre_duck_volume preserved for eventual restore)
        assert m._pre_duck_volume == 70


# ── Full integration: queue + ducking via EventBus ───────────────────────────

class TestQueueWithDuckingIntegration:

    @pytest.mark.asyncio
    async def test_full_pipeline_multiple_speaks(self):
        """Simulate 5 modules speaking simultaneously: queue + ducking end-to-end."""
        m, bus = _make_voice_module()
        media = _make_media_module()
        media._bus = bus
        media._direct_sub_ids = []

        await bus.start()

        # Subscribe media module to ducking events
        media.subscribe(["voice.tts_start"], media._on_tts_start)
        media.subscribe(["voice.tts_done"], media._on_tts_done)

        playback_order: list[str] = []
        active = 0
        max_active = 0

        async def mock_stream_speak(text, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            playback_order.append(text)
            await asyncio.sleep(0.03)
            active -= 1

        m._stream_speak = mock_stream_speak

        # Start worker
        worker = asyncio.create_task(m._speech_worker())

        # 5 modules fire speech events at the same time
        done_events = []
        for i in range(5):
            ev = asyncio.Event()
            done_events.append(ev)
            await m._enqueue_speech(f"module-{i}", priority=1, done_event=ev)

        await asyncio.wait_for(
            asyncio.gather(*(e.wait() for e in done_events)),
            timeout=10.0,
        )

        # Give EventBus time to deliver tts_done to media module
        await asyncio.sleep(0.5)

        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        await bus.stop()

        # Assert: sequential (no overlap)
        assert max_active == 1
        # Assert: all 5 played
        assert len(playback_order) == 5
        # Assert: ducking restored (media back to normal)
        assert media._pre_duck_volume is None
