# system_modules/media_player/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.i18n import t

if TYPE_CHECKING:
    from .module import MediaPlayerModule

logger = logging.getLogger(__name__)

GENRE_MAP: dict[str, str] = {
    # Ukrainian
    "рок": "rock",
    "джаз": "jazz",
    "класику": "classical",
    "класичну": "classical",
    "ембієнт": "ambient",
    "поп": "pop",
    "новини": "news",
    # English (passthrough)
    "rock": "rock",
    "jazz": "jazz",
    "classical": "classical",
    "ambient": "ambient",
    "lofi": "lofi",
    "pop": "pop",
    "news": "news",
}


class MediaVoiceHandler:
    def __init__(self, module: "MediaPlayerModule") -> None:
        self._module = module

    async def handle(self, intent: str, params: dict) -> None:
        player = self._module._player
        lib = self._module._library
        m = self._module

        match intent:
            case "media.play_radio":
                if player.get_state() == "playing" and player._current_source_type == "radio":
                    track = player.get_current_track()
                    name = track.title if track else ""
                    await m.speak(t("media.already_playing_radio", station=name))
                    return
                stations = lib.search(limit=1)
                if stations:
                    await player.play_url(stations[0]["url"], "radio")
                    await m.speak(t("media.playing_radio", station=stations[0]["name"]))
                else:
                    await m.speak(t("media.no_stations"))

            case "media.play_genre":
                genre_raw = params.get("genre", "")
                genre_en = GENRE_MAP.get(genre_raw.lower(), genre_raw)
                stations = lib.search(genre=genre_en, limit=5)
                if stations:
                    await player.play_url(stations[0]["url"], "radio")
                    await m.speak(
                        t("media.playing_genre", genre=genre_raw, station=stations[0]["name"])
                    )
                else:
                    await m.speak(t("media.genre_not_found", genre=genre_raw))

            case "media.play_radio_name":
                name = params.get("station_name", "")
                results = lib.search(name=name, limit=1)
                if results:
                    await player.play_url(results[0]["url"], "radio")
                    await m.speak(t("media.playing_station", station=results[0]["name"]))
                else:
                    await m.speak(t("media.station_not_found", name=name))

            case "media.play_search":
                query = params.get("query", "")
                usb_tracks = await m._usb.scan()
                matches = [
                    tr for tr in usb_tracks
                    if query.lower() in (tr["title"] + tr["artist"]).lower()
                ]
                if matches:
                    track = matches[0]
                    await player.play_url(track["path"], "usb")
                    label = track["title"]
                    if track["artist"]:
                        label += f" — {track['artist']}"
                    await m.speak(t("media.playing_track", label=label))
                else:
                    await m.speak(t("media.usb_not_found", query=query))

            case "media.next":
                await player.next()

            case "media.previous":
                await player.previous()

            case "media.stop":
                await player.stop()

            case "media.pause":
                state = player.get_state()
                await player.pause()
                if state == "playing":
                    await m.speak(t("media.paused"))
                else:
                    await m.speak(t("media.resumed"))

            case "media.resume":
                if player.get_state() == "paused":
                    await player.pause()  # toggle

            case "media.volume_down":
                vol = max(0, player._volume - 15)
                await player.set_volume(vol)
                await m.speak(t("media.volume_level", level=vol))

            case "media.volume_up":
                vol = min(100, player._volume + 15)
                await player.set_volume(vol)
                await m.speak(t("media.volume_level", level=vol))

            case "media.volume_set":
                try:
                    level = int(params.get("level", 50))
                    await player.set_volume(level)
                    await m.speak(t("media.volume_set", level=level))
                except (ValueError, TypeError):
                    pass

            case "media.whats_playing":
                track = player.get_current_track()
                state = player.get_state()
                if state not in ("playing", "paused") or not track:
                    await m.speak(t("media.nothing_playing"))
                else:
                    if track.title:
                        text = t("media.now_playing", title=track.title)
                    else:
                        text = t("media.now_playing_untitled")
                    if track.artist:
                        text += t("media.now_playing_artist", artist=track.artist)
                    if track.album:
                        text += t("media.now_playing_album", album=track.album)
                    await m.speak(text)

            case "media.shuffle_toggle":
                new_state = not player._shuffle
                player.set_shuffle(new_state)
                if new_state:
                    await m.speak(t("media.shuffle_on"))
                else:
                    await m.speak(t("media.shuffle_off"))

            case _:
                logger.debug("MediaVoiceHandler: unhandled intent '%s'", intent)
