"""tests/test_sources.py — Unit tests for media source classes."""
import pytest
import httpx
from pathlib import Path
from unittest.mock import AsyncMock, patch


# ── RadioBrowserSource ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_radio_search_returns_stations(respx_mock):
    from system_modules.media_player.sources.radio_browser import RadioBrowserSource

    respx_mock.get("https://de1.api.radio-browser.info/json/stations/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "stationuuid": "abc123",
                    "name": "Test Jazz Radio",
                    "url_resolved": "https://stream.example.com/jazz",
                    "bitrate": 128,
                    "codec": "MP3",
                    "country": "DE",
                    "tags": "jazz",
                    "favicon": "",
                    "votes": 500,
                }
            ],
        )
    )
    source = RadioBrowserSource()
    results = await source.search(tag="jazz")
    assert len(results) == 1
    assert results[0]["name"] == "Test Jazz Radio"
    assert results[0]["url"] == "https://stream.example.com/jazz"
    assert results[0]["bitrate"] == 128


@pytest.mark.asyncio
async def test_radio_search_empty_on_error():
    from system_modules.media_player.sources.radio_browser import RadioBrowserSource

    source = RadioBrowserSource()
    source._base = "https://invalid.nonexistent.local"
    results = await source.search(tag="jazz")
    assert results == []


@pytest.mark.asyncio
async def test_radio_search_filters_missing_url(respx_mock):
    from system_modules.media_player.sources.radio_browser import RadioBrowserSource

    respx_mock.get("https://de1.api.radio-browser.info/json/stations/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"stationuuid": "x1", "name": "No URL", "url_resolved": "", "url": ""},
                {
                    "stationuuid": "x2",
                    "name": "Has URL",
                    "url_resolved": "https://stream.test/radio",
                    "url": "",
                    "bitrate": 64, "codec": "MP3", "country": "US",
                    "tags": "", "favicon": "", "votes": 10,
                },
            ],
        )
    )
    source = RadioBrowserSource()
    results = await source.search()
    assert len(results) == 1
    assert results[0]["name"] == "Has URL"


@pytest.mark.asyncio
async def test_radio_get_tags_fallback_on_error():
    from system_modules.media_player.sources.radio_browser import RadioBrowserSource

    source = RadioBrowserSource()
    source._base = "https://invalid.nonexistent.local"
    tags = await source.get_tags()
    assert isinstance(tags, list)
    assert len(tags) > 0  # returns fallback list


# ── USBSource ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usb_scan_finds_audio_files(tmp_path):
    from system_modules.media_player.sources.usb_source import USBSource

    (tmp_path / "song.mp3").write_bytes(b"fake mp3")
    (tmp_path / "track.flac").write_bytes(b"fake flac")
    (tmp_path / "document.pdf").write_bytes(b"not audio")
    (tmp_path / "image.jpg").write_bytes(b"not audio")

    source = USBSource()
    tracks = await source.scan(mount_base=str(tmp_path))

    names = [t["name"] for t in tracks]
    assert "song" in names
    assert "track" in names
    assert "document" not in names
    assert "image" not in names


@pytest.mark.asyncio
async def test_usb_scan_empty_on_no_audio(tmp_path):
    from system_modules.media_player.sources.usb_source import USBSource

    (tmp_path / "readme.txt").write_text("hello")
    source = USBSource()
    tracks = await source.scan(mount_base=str(tmp_path))
    assert tracks == []


@pytest.mark.asyncio
async def test_usb_scan_non_existent_dir():
    from system_modules.media_player.sources.usb_source import USBSource

    source = USBSource()
    tracks = await source.scan(mount_base="/tmp/nonexistent_selena_test_dir_xyz")
    assert tracks == []


def test_usb_get_mounted_devices():
    from system_modules.media_player.sources.usb_source import USBSource

    source = USBSource()
    # Should return a list even if empty (no USB mounted in test env)
    devices = source.get_mounted_devices()
    assert isinstance(devices, list)


# ── InternetArchiveSource ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_archive_search_empty_on_error():
    from system_modules.media_player.sources.archive_source import InternetArchiveSource

    source = InternetArchiveSource()
    # Patch httpx to simulate network failure
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__ = AsyncMock(side_effect=Exception("offline"))
        results = await source.search(query="jazz")
    assert results == []
