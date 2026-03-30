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
    m._player.get_state = MagicMock(return_value="playing")
    m._player.get_current_track = MagicMock(return_value=None)
    m._player.set_volume = AsyncMock()
    m._player.next = AsyncMock()
    m._player.previous = AsyncMock()
    m._player.stop = AsyncMock()
    m._player.pause = AsyncMock()
    m._player.play_url = AsyncMock()
    m._radio = MagicMock()
    m._radio.search = AsyncMock(return_value=[])
    m._radio.get_by_name = AsyncMock(return_value=None)
    m._radio.click = AsyncMock()
    m._usb = MagicMock()
    m._usb.scan = AsyncMock(return_value=[])
    m.speak = AsyncMock()
    return m


@pytest.fixture
def handler(module):
    from system_modules.media_player.voice_handler import MediaVoiceHandler

    return MediaVoiceHandler(module)


@pytest.mark.asyncio
async def test_volume_up(handler, module):
    await handler.handle("media.volume_up", {})
    module._player.set_volume.assert_called_once_with(85)
    module.speak.assert_called_once()


@pytest.mark.asyncio
async def test_volume_down(handler, module):
    await handler.handle("media.volume_down", {})
    module._player.set_volume.assert_called_once_with(55)


@pytest.mark.asyncio
async def test_volume_up_clamps_at_100(handler, module):
    module._player._volume = 95
    await handler.handle("media.volume_up", {})
    module._player.set_volume.assert_called_once_with(100)


@pytest.mark.asyncio
async def test_volume_down_clamps_at_0(handler, module):
    module._player._volume = 5
    await handler.handle("media.volume_down", {})
    module._player.set_volume.assert_called_once_with(0)


@pytest.mark.asyncio
async def test_volume_set(handler, module):
    await handler.handle("media.volume_set", {"level": "42"})
    module._player.set_volume.assert_called_once_with(42)


@pytest.mark.asyncio
async def test_volume_set_invalid(handler, module):
    await handler.handle("media.volume_set", {"level": "bad"})
    module._player.set_volume.assert_not_called()


@pytest.mark.asyncio
async def test_next(handler, module):
    await handler.handle("media.next", {})
    module._player.next.assert_called_once()


@pytest.mark.asyncio
async def test_previous(handler, module):
    await handler.handle("media.previous", {})
    module._player.previous.assert_called_once()


@pytest.mark.asyncio
async def test_stop(handler, module):
    await handler.handle("media.stop", {})
    module._player.stop.assert_called_once()


@pytest.mark.asyncio
async def test_pause_announces(handler, module):
    module._player.get_state.return_value = "playing"
    await handler.handle("media.pause", {})
    module._player.pause.assert_called_once()
    call_text = module.speak.call_args[0][0]
    from core.i18n import t
    assert t("media.paused") in call_text


@pytest.mark.asyncio
async def test_resume_when_paused(handler, module):
    module._player.get_state.return_value = "paused"
    await handler.handle("media.resume", {})
    module._player.pause.assert_called_once()  # toggles via pause()


@pytest.mark.asyncio
async def test_resume_when_already_playing(handler, module):
    module._player.get_state.return_value = "playing"
    await handler.handle("media.resume", {})
    module._player.pause.assert_not_called()


@pytest.mark.asyncio
async def test_whats_playing_with_track(handler, module):
    track = MagicMock()
    track.title = "Clair de Lune"
    track.artist = "Debussy"
    track.album = "Suite bergamasque"
    module._player.get_current_track.return_value = track
    module._player.get_state.return_value = "playing"
    await handler.handle("media.whats_playing", {})
    call_text = module.speak.call_args[0][0]
    assert "Clair de Lune" in call_text
    assert "Debussy" in call_text


@pytest.mark.asyncio
async def test_whats_playing_includes_album(handler, module):
    track = MagicMock()
    track.title = "Clair de Lune"
    track.artist = "Debussy"
    track.album = "Suite bergamasque"
    module._player.get_current_track.return_value = track
    module._player.get_state.return_value = "playing"
    await handler.handle("media.whats_playing", {})
    call_text = module.speak.call_args[0][0]
    assert "Suite bergamasque" in call_text


@pytest.mark.asyncio
async def test_whats_playing_nothing(handler, module):
    module._player.get_state.return_value = "stopped"
    module._player.get_current_track.return_value = None
    await handler.handle("media.whats_playing", {})
    call_text = module.speak.call_args[0][0]
    assert "nothing" in call_text.lower()


@pytest.mark.asyncio
async def test_shuffle_toggle_on(handler, module):
    module._player._shuffle = False
    await handler.handle("media.shuffle_toggle", {})
    module._player.set_shuffle.assert_called_once_with(True)
    call_text = module.speak.call_args[0][0]
    assert "enabled" in call_text.lower()


@pytest.mark.asyncio
async def test_shuffle_toggle_off(handler, module):
    module._player._shuffle = True
    await handler.handle("media.shuffle_toggle", {})
    module._player.set_shuffle.assert_called_once_with(False)
    call_text = module.speak.call_args[0][0]
    assert "disabled" in call_text.lower()


@pytest.mark.asyncio
async def test_play_radio_when_stations_found(handler, module):
    module._radio.search = AsyncMock(return_value=[
        {"name": "Test FM", "url": "https://stream.test/radio", "uuid": "u1"}
    ])
    await handler.handle("media.play_radio", {})
    module._player.play_url.assert_called_once_with(
        "https://stream.test/radio", "radio"
    )
    module._radio.click.assert_called_once_with("u1")
    call_text = module.speak.call_args[0][0]
    assert "Test FM" in call_text


@pytest.mark.asyncio
async def test_play_radio_name_not_found(handler, module):
    module._radio.get_by_name = AsyncMock(return_value=None)
    await handler.handle("media.play_radio_name", {"station_name": "Unknown FM"})
    module._player.play_url.assert_not_called()
    call_text = module.speak.call_args[0][0]
    assert "Unknown FM" in call_text


@pytest.mark.asyncio
async def test_unknown_intent_is_ignored(handler, module):
    await handler.handle("media.unknown_intent_xyz", {})
    module.speak.assert_not_called()
    module._player.stop.assert_not_called()
