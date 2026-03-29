"""tests/test_player.py — Unit tests for MediaPlayer."""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def player():
    """Create a MediaPlayer with libvlc stubbed out."""
    with patch.dict("sys.modules", {"vlc": MagicMock()}):
        from system_modules.media_player.player import MediaPlayer

        p = MediaPlayer()
        # Replace internal VLC objects with simple mocks
        p._player = MagicMock()
        p._list_player = MagicMock()
        p._instance = MagicMock()
        p._vlc = MagicMock()
        return p


@pytest.mark.asyncio
async def test_set_volume_clamps_high(player):
    await player.set_volume(150)
    assert player._volume == 100


@pytest.mark.asyncio
async def test_set_volume_clamps_low(player):
    await player.set_volume(-10)
    assert player._volume == 0


@pytest.mark.asyncio
async def test_set_volume_normal(player):
    await player.set_volume(60)
    assert player._volume == 60
    player._player.audio_set_volume.assert_called_once_with(60)


@pytest.mark.asyncio
async def test_get_state_playing(player):
    player._vlc = MagicMock()
    # Simulate vlc.State.Playing == "playing" mapping
    class FakeState:
        pass

    playing_state = object()
    player._vlc.State.Playing = playing_state
    player._vlc.State.Paused = object()
    player._vlc.State.Stopped = object()
    player._vlc.State.Ended = object()
    player._player.get_state.return_value = playing_state
    assert player.get_state() == "playing"


@pytest.mark.asyncio
async def test_get_state_stopped_when_no_player(player):
    player._player = None
    assert player.get_state() == "stopped"


@pytest.mark.asyncio
async def test_seek_clamps_high(player):
    await player.seek(1.5)
    player._player.set_position.assert_called_with(1.0)


@pytest.mark.asyncio
async def test_seek_clamps_low(player):
    await player.seek(-0.5)
    player._player.set_position.assert_called_with(0.0)


@pytest.mark.asyncio
async def test_seek_normal(player):
    await player.seek(0.5)
    player._player.set_position.assert_called_with(0.5)


def test_shuffle_toggle_on(player):
    player.set_shuffle(True)
    assert player._shuffle is True


def test_shuffle_toggle_off(player):
    player._shuffle = True
    player.set_shuffle(False)
    assert player._shuffle is False


@pytest.mark.asyncio
async def test_play_url_calls_vlc(player):
    player._instance.media_new.return_value = MagicMock()
    await player.play_url("https://stream.example.com/radio", "radio")
    # Radio streams: HTTPS is converted to HTTP to avoid gnutls TLS issues
    player._instance.media_new.assert_called_once_with("http://stream.example.com/radio")
    player._player.set_media.assert_called_once()
    player._player.play.assert_called_once()
    assert player._current_source_type == "radio"


@pytest.mark.asyncio
async def test_stop(player):
    await player.stop()
    player._player.stop.assert_called_once()


@pytest.mark.asyncio
async def test_pause_toggle(player):
    await player.pause()
    player._player.pause.assert_called_once()


def test_get_status_keys(player):
    player._player.get_state.return_value = MagicMock()
    player._vlc.State.Playing = object()
    player._vlc.State.Paused = object()
    player._vlc.State.Stopped = object()
    player._vlc.State.Ended = object()
    player._player.get_media.return_value = None
    status = player.get_status()
    assert "state" in status
    assert "volume" in status
    assert "position" in status
    assert "shuffle" in status
