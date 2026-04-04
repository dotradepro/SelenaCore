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
                    s = stations[0]
                    await m.speak(t("media.playing_radio", station=s["name"]))
                    await player.play_url(
                        s["url"], "radio",
                        title=s.get("name", ""),
                        cover_url=s.get("logo") or None,
                    )
                else:
                    await m.speak(t("media.no_stations"))

            case "media.play_genre":
                genre_raw = params.get("genre", "")
                genre_en = GENRE_MAP.get(genre_raw.lower(), genre_raw)
                stations = lib.search(genre=genre_en, limit=5)
                if stations:
                    s = stations[0]
                    await m.speak(
                        t("media.playing_genre", genre=genre_raw, station=s["name"])
                    )
                    await player.play_url(
                        s["url"], "radio",
                        title=s.get("name", ""),
                        cover_url=s.get("logo") or None,
                    )
                else:
                    await m.speak(t("media.genre_not_found", genre=genre_raw))

            case "media.play_radio_name":
                # Try entity_ref first (from pattern match → exact DB lookup)
                station_found = await self._resolve_station_by_ref(params, player, m)
                if not station_found:
                    # Fallback: search by name in local library
                    name = params.get("station_name", "")
                    results = lib.search(name=name, limit=1)
                    if results:
                        s = results[0]
                        await m.speak(t("media.playing_station", station=s["name"]))
                        await player.play_url(
                            s["url"], "radio",
                            title=s.get("name", ""),
                            cover_url=s.get("logo") or None,
                        )
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
                    label = track["title"]
                    if track["artist"]:
                        label += f" — {track['artist']}"
                    await m.speak(t("media.playing_track", label=label))
                    await player.play_url(track["path"], "usb")
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

    async def _resolve_station_by_ref(self, params: dict, player, m) -> bool:
        """Try to resolve and play station via entity_ref from pattern match.

        entity_ref format: "radio_station:42" → lookup RadioStation by DB id → play.
        Returns True if station was found and played, False otherwise.
        """
        entity_ref = params.get("entity_ref", "")
        if not entity_ref or not entity_ref.startswith("radio_station:"):
            return False

        try:
            station_id = int(entity_ref.split(":")[1])
        except (ValueError, IndexError):
            return False

        try:
            from core.module_loader.sandbox import get_sandbox
            sf = get_sandbox()._session_factory
            if sf is None:
                return False

            from core.registry.models import RadioStation
            async with sf() as session:
                db_station = await session.get(RadioStation, station_id)
                if not db_station or not db_station.stream_url:
                    return False

                url = db_station.stream_url
                name = db_station.name_user or db_station.name_en
                logo = db_station.logo_url or ""

            # Speak FIRST, then play
            await m.speak(t("media.playing_station", station=name))
            await player.play_url(
                url, "radio",
                title=name,
                cover_url=logo or None,
            )
            return True

        except Exception as exc:
            logger.debug("Station resolve by ref failed: %s", exc)
            return False
