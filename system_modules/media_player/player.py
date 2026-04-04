# system_modules/media_player/player.py — Playback engine (python-vlc / libvlc).
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_sec: int = 0
    cover_url: Optional[str] = None
    source_type: str = "unknown"  # "usb" | "smb" | "radio" | "http" | "archive"
    source_path: str = ""


class MediaPlayer:
    """Thin async wrapper around libvlc via python-vlc.

    Runs headless (--no-video).  Supports streams (radio), local files, and
    M3U/PLS playlists.  Does NOT start a subprocess — libvlc threads are
    managed inside the process.
    """

    def __init__(self) -> None:
        try:
            import vlc  # type: ignore[import-untyped]
            self._vlc = vlc
            aout = os.getenv("MEDIA_AUDIO_OUTPUT", "alsa").strip()
            vlc_args = ["--no-video", "--no-xlib", "--network-caching=3000",
                        "--http-reconnect", f"--aout={aout}",
                        "--no-spu", "--no-osd",
                        "--no-video-title-show", "--no-snapshot-preview"]
            alsa_dev = os.getenv("MEDIA_ALSA_DEVICE", "").strip()
            if not alsa_dev and aout == "alsa":
                # Prefer mixer device (dmix for concurrent playback with TTS)
                try:
                    from core.audio_mixer import get_mixer
                    mixer = get_mixer()
                    if mixer.is_initialized():
                        alsa_dev = mixer.get_device("media-player") or ""
                except Exception:
                    pass
                if not alsa_dev:
                    try:
                        from core.config_writer import get_value
                        alsa_dev = get_value("voice", "audio_force_output") or ""
                    except Exception:
                        pass
            if aout == "alsa" and alsa_dev:
                vlc_args.append(f"--alsa-audio-device={alsa_dev}")
            self._instance = vlc.Instance(*vlc_args)
            self._player = self._instance.media_player_new()
            self._list_player = self._instance.media_list_player_new()
            self._list_player.set_media_player(self._player)
        except Exception as exc:
            logger.warning(
                "python-vlc not available — MediaPlayer running in stub mode: %s", exc
            )
            self._vlc = None
            self._instance = None
            self._player = None  # type: ignore[assignment]
            self._list_player = None  # type: ignore[assignment]

        self._volume: int = 70
        self._shuffle: bool = False
        self._current_source_type: str = "unknown"
        # Soft state for stub mode — tracks what the UI selected
        self._stub_state: str = "stopped"
        self._stub_track: Optional[TrackInfo] = None
        self._watchdog_task: Optional[asyncio.Task] = None

    # ── Playback ──────────────────────────────────────────────────────────────

    async def play_url(self, url: str, source_type: str = "http",
                       *, title: str = "", cover_url: Optional[str] = None) -> None:
        self._current_source_type = source_type
        # For radio streams, prefer HTTP over HTTPS to avoid gnutls issues
        # with non-standard TLS configurations on shoutcast/icecast servers
        play_url = url
        if source_type == "radio" and url.startswith("https://"):
            play_url = "http://" + url[8:]
            logger.info("Radio stream: using HTTP instead of HTTPS for %s", url)
        # Always track soft state so UI can display the selection
        self._stub_track = TrackInfo(
            title=title or url.rsplit("/", 1)[-1][:60],
            source_type=source_type,
            source_path=url,
            cover_url=cover_url,
        )
        if self._instance is None:
            self._stub_state = "playing"
            logger.info("[stub] play_url: %s [%s]", url, source_type)
            return
        # Release previous media to prevent memory leak
        old_media = self._player.get_media()
        media = self._instance.media_new(play_url)
        self._player.set_media(media)
        if old_media is not None:
            old_media.release()
        rc = self._player.play()
        if rc == -1:
            self._stub_state = "error"
            logger.error("VLC play() failed for: %s", play_url)
            return
        self._stub_state = "playing"
        self._start_watchdog()
        logger.info("Playing: %s [%s]", play_url, source_type)

    def _start_watchdog(self) -> None:
        """Restart stream if VLC drops to Ended/Error (e.g. TLS disconnect)."""
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = asyncio.ensure_future(self._watchdog_loop())

    async def _watchdog_loop(self) -> None:
        """Monitor playback and auto-reconnect radio streams on drop."""
        await asyncio.sleep(5)  # give VLC time to start
        while True:
            await asyncio.sleep(3)
            if self._instance is None:
                return
            state = self.get_state()
            if state in ("error", "ended") and self._stub_track:
                track = self._stub_track
                if track.source_type == "radio" and track.source_path:
                    logger.warning("Radio stream ended/error — reconnecting: %s",
                                   track.source_path)
                    url = track.source_path
                    play_url = url
                    if url.startswith("https://"):
                        play_url = "http://" + url[8:]
                    old_media = self._player.get_media()
                    media = self._instance.media_new(play_url)
                    self._player.set_media(media)
                    if old_media is not None:
                        old_media.release()
                    self._player.play()
                    await asyncio.sleep(5)
                else:
                    return
            elif state == "stopped":
                return

    async def play_playlist(self, urls: list[str]) -> None:
        if self._instance is None:
            logger.info("[stub] play_playlist: %d tracks", len(urls))
            return
        media_list = self._instance.media_list_new(urls)
        self._list_player.set_media_list(media_list)
        self._list_player.play()

    async def next(self) -> None:
        if self._list_player is None:
            return
        self._list_player.next()

    async def previous(self) -> None:
        if self._list_player is None:
            return
        self._list_player.previous()

    async def pause(self) -> None:
        """Toggle play / pause."""
        if self._player is None:
            # Stub: toggle soft state
            self._stub_state = "paused" if self._stub_state == "playing" else "playing"
            return
        self._player.pause()

    async def stop(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._player is None:
            self._stub_state = "stopped"
            self._stub_track = None
            return
        self._player.stop()
        # Release current media to free memory
        media = self._player.get_media()
        if media is not None:
            media.release()

    async def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, volume))
        if self._player is not None:
            self._player.audio_set_volume(self._volume)

    async def seek(self, position: float) -> None:
        """position: 0.0 – 1.0"""
        if self._player is None:
            return
        self._player.set_position(max(0.0, min(1.0, position)))

    # ── State queries ─────────────────────────────────────────────────────────

    def get_position(self) -> float:
        if self._player is None:
            return 0.0
        return self._player.get_position()

    def get_state(self) -> str:
        if self._player is None or self._vlc is None:
            return self._stub_state
        state = self._player.get_state()
        mapping = {
            self._vlc.State.NothingSpecial: "stopped",
            self._vlc.State.Opening: "buffering",
            self._vlc.State.Buffering: "buffering",
            self._vlc.State.Playing: "playing",
            self._vlc.State.Paused: "paused",
            self._vlc.State.Stopped: "stopped",
            self._vlc.State.Ended: "ended",
            self._vlc.State.Error: "error",
        }
        return mapping.get(state, "stopped")

    def get_current_track(self) -> Optional[TrackInfo]:
        if self._player is None or self._vlc is None:
            return self._stub_track
        media = self._player.get_media()
        if not media:
            return self._stub_track
        media.parse_with_options(self._vlc.MediaParseFlag.local, 0)
        vlc_title = media.get_meta(self._vlc.Meta.Title) or ""
        vlc_artist = media.get_meta(self._vlc.Meta.Artist) or ""
        # Enrich with stub metadata (station name, cover) when VLC has no/generic info
        stub = self._stub_track
        title = vlc_title
        cover = None
        if stub:
            if not vlc_title or vlc_title == stub.source_path.rsplit("/", 1)[-1]:
                title = stub.title or vlc_title
            cover = stub.cover_url
        return TrackInfo(
            title=title,
            artist=vlc_artist,
            album=media.get_meta(self._vlc.Meta.Album) or "",
            duration_sec=(media.get_duration() or 0) // 1000,
            cover_url=cover,
            source_type=self._current_source_type,
        )

    def set_shuffle(self, enabled: bool) -> None:
        self._shuffle = enabled
        if self._list_player is not None and self._vlc is not None:
            mode = (
                self._vlc.PlaybackMode.default
                if enabled
                else self._vlc.PlaybackMode.loop
            )
            self._list_player.set_playback_mode(mode)

    def release(self) -> None:
        """Release all VLC resources to free memory."""
        if self._player is not None:
            self._player.stop()
            media = self._player.get_media()
            if media is not None:
                media.release()
            self._player.release()
            self._player = None
        if self._list_player is not None:
            self._list_player.release()
            self._list_player = None
        if self._instance is not None:
            self._instance.release()
            self._instance = None
        self._vlc = None

    def get_status(self) -> dict:
        track = self.get_current_track()
        return {
            "state": self.get_state(),
            "volume": self._volume,
            "position": self.get_position(),
            "shuffle": self._shuffle,
            "track": (
                {
                    "title": track.title,
                    "artist": track.artist,
                    "album": track.album,
                    "duration_sec": track.duration_sec,
                    "cover_url": track.cover_url,
                    "source_type": track.source_type,
                    "source_path": track.source_path,
                }
                if track
                else None
            ),
        }
