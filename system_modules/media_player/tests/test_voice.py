"""tests/test_voice.py — Unit tests for MediaVoiceHandler."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def module():
    """Minimal mock of MediaPlayerModule for voice handler tests."""
    m = MagicMock()
    m._player = MagicMock()
    m._player._volume = 70
    m._player._shuffle = False
    m._player._current_source_type = "unknown"
    m._player.get_state = MagicMock(return_value="playing")
    m._player.get_current_track = MagicMock(return_value=None)
    m._player.set_volume = AsyncMock()
    m._player.next = AsyncMock()
    m._player.previous = AsyncMock()
    m._player.stop = AsyncMock()
    m._player.pause = AsyncMock()
    m._player.play_url = AsyncMock()
    m._library = MagicMock()
    m._library.search = MagicMock(return_value=[])
    m._usb = MagicMock()
    m._usb.scan = AsyncMock(return_value=[])
    return m


@pytest.fixture
def handler(module):
    from system_modules.media_player.voice_handler import MediaVoiceHandler

    return MediaVoiceHandler(module)


@pytest.mark.asyncio
async def test_volume_up(handler, module):
    ctx = await handler.handle("media.volume_up", {})
    module._player.set_volume.assert_called_once_with(85)
    assert ctx is not None
    assert ctx["level"] == 85


@pytest.mark.asyncio
async def test_volume_down(handler, module):
    ctx = await handler.handle("media.volume_down", {})
    module._player.set_volume.assert_called_once_with(55)
    assert ctx is not None
    assert ctx["level"] == 55


@pytest.mark.asyncio
async def test_volume_up_clamps_at_100(handler, module):
    module._player._volume = 95
    ctx = await handler.handle("media.volume_up", {})
    module._player.set_volume.assert_called_once_with(100)
    assert ctx["level"] == 100


@pytest.mark.asyncio
async def test_volume_down_clamps_at_0(handler, module):
    module._player._volume = 5
    ctx = await handler.handle("media.volume_down", {})
    module._player.set_volume.assert_called_once_with(0)
    assert ctx["level"] == 0


@pytest.mark.asyncio
async def test_volume_set(handler, module):
    ctx = await handler.handle("media.volume_set", {"level": "42"})
    module._player.set_volume.assert_called_once_with(42)
    assert ctx is not None
    assert ctx["level"] == 42


@pytest.mark.asyncio
async def test_volume_set_invalid(handler, module):
    ctx = await handler.handle("media.volume_set", {"level": "bad"})
    module._player.set_volume.assert_not_called()
    assert ctx is None


@pytest.mark.asyncio
async def test_next(handler, module):
    ctx = await handler.handle("media.next", {})
    module._player.next.assert_called_once()
    assert ctx == {"action": "next"}


@pytest.mark.asyncio
async def test_previous(handler, module):
    ctx = await handler.handle("media.previous", {})
    module._player.previous.assert_called_once()
    assert ctx == {"action": "previous"}


@pytest.mark.asyncio
async def test_stop(handler, module):
    ctx = await handler.handle("media.stop", {})
    module._player.stop.assert_called_once()
    assert ctx == {"action": "stop"}


@pytest.mark.asyncio
async def test_pause_returns_paused(handler, module):
    module._player.get_state.return_value = "playing"
    ctx = await handler.handle("media.pause", {})
    module._player.pause.assert_called_once()
    assert ctx == {"action": "paused"}


@pytest.mark.asyncio
async def test_pause_returns_resumed(handler, module):
    module._player.get_state.return_value = "paused"
    ctx = await handler.handle("media.pause", {})
    module._player.pause.assert_called_once()
    assert ctx == {"action": "resumed"}


@pytest.mark.asyncio
async def test_resume_when_paused(handler, module):
    module._player.get_state.return_value = "paused"
    ctx = await handler.handle("media.resume", {})
    module._player.pause.assert_called_once()  # toggles via pause()
    assert ctx == {"action": "resumed"}


@pytest.mark.asyncio
async def test_resume_when_already_playing(handler, module):
    module._player.get_state.return_value = "playing"
    ctx = await handler.handle("media.resume", {})
    module._player.pause.assert_not_called()
    assert ctx is None


@pytest.mark.asyncio
async def test_whats_playing_with_track(handler, module):
    track = MagicMock()
    track.title = "Clair de Lune"
    track.artist = "Debussy"
    track.album = "Suite bergamasque"
    module._player.get_current_track.return_value = track
    module._player.get_state.return_value = "playing"
    ctx = await handler.handle("media.whats_playing", {})
    assert ctx is not None
    assert ctx["title"] == "Clair de Lune"
    assert ctx["artist"] == "Debussy"
    assert ctx["album"] == "Suite bergamasque"


@pytest.mark.asyncio
async def test_whats_playing_nothing(handler, module):
    module._player.get_state.return_value = "stopped"
    ctx = await handler.handle("media.whats_playing", {})
    assert ctx == {"action": "nothing_playing"}


@pytest.mark.asyncio
async def test_shuffle_toggle(handler, module):
    module._player._shuffle = False
    ctx = await handler.handle("media.shuffle_toggle", {})
    module._player.set_shuffle.assert_called_once_with(True)
    assert ctx == {"action": "shuffle_on"}
