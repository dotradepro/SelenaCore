# system_modules/media_player/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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

    async def handle(self, intent: str, params: dict) -> dict | None:
        player = self._module._player
        lib = self._module._library

        match intent:
            case "media.play_radio":
                if player.get_state() == "playing" and player._current_source_type == "radio":
                    track = player.get_current_track()
                    name = track.title if track else ""
                    return {"action": "already_playing_radio", "station": name}
                stations = lib.search(limit=1)
                if stations:
                    s = stations[0]
                    ctx = {"action": "play_radio", "station": s["name"]}
                    await player.play_url(
                        s["url"], "radio",
                        title=s.get("name", ""),
                        cover_url=s.get("logo") or None,
                    )
                    return ctx
                else:
                    return {"action": "no_stations"}

            case "media.play_genre":
                genre_raw = params.get("genre", "")
                genre_en = GENRE_MAP.get(genre_raw.lower(), genre_raw)
                stations = lib.search(genre=genre_en, limit=5)
                if stations:
                    s = stations[0]
                    ctx = {"action": "play_genre", "genre": genre_raw, "station": s["name"]}
                    await player.play_url(
                        s["url"], "radio",
                        title=s.get("name", ""),
                        cover_url=s.get("logo") or None,
                    )
                    return ctx
                else:
                    return {"action": "genre_not_found", "genre": genre_raw}

            case "media.play_radio_name":
                station_found = await self._resolve_station_by_ref(params, player)
                if station_found:
                    return station_found
                name = params.get("station_name", "")
                results = lib.search(name=name, limit=1)
                if results:
                    s = results[0]
                    await player.play_url(
                        s["url"], "radio",
                        title=s.get("name", ""),
                        cover_url=s.get("logo") or None,
                    )
                    return {"action": "play_station", "station": s["name"]}
                else:
                    return {"action": "station_not_found", "name": name}

            case "media.play_search":
                query = params.get("query", "")
                usb_tracks = await self._module._usb.scan()
                matches = [
                    tr for tr in usb_tracks
                    if query.lower() in (tr["title"] + tr["artist"]).lower()
                ]
                if matches:
                    track = matches[0]
                    label = track["title"]
                    if track["artist"]:
                        label += f" — {track['artist']}"
                    await player.play_url(track["path"], "usb")
                    return {"action": "play_track", "label": label}
                else:
                    return {"action": "usb_not_found", "query": query}

            case "media.next":
                await player.next()
                return {"action": "next"}

            case "media.previous":
                await player.previous()
                return {"action": "previous"}

            case "media.stop":
                await player.stop()
                return {"action": "stop"}

            case "media.pause":
                state = player.get_state()
                await player.pause()
                if state == "playing":
                    return {"action": "paused"}
                else:
                    return {"action": "resumed"}

            case "media.resume":
                if player.get_state() == "paused":
                    await player.pause()  # toggle
                    return {"action": "resumed"}
                return None

            case "media.volume_down":
                vol = max(0, player._volume - 15)
                await player.set_volume(vol)
                return {"action": "volume_level", "level": vol}

            case "media.volume_up":
                vol = min(100, player._volume + 15)
                await player.set_volume(vol)
                return {"action": "volume_level", "level": vol}

            case "media.volume_set":
                try:
                    level = int(params.get("level", 50))
                    await player.set_volume(level)
                    return {"action": "volume_set", "level": level}
                except (ValueError, TypeError):
                    return None

            case "media.whats_playing":
                track = player.get_current_track()
                state = player.get_state()
                if state not in ("playing", "paused") or not track:
                    return {"action": "nothing_playing"}
                ctx: dict = {"action": "now_playing"}
                if track.title:
                    ctx["title"] = track.title
                if track.artist:
                    ctx["artist"] = track.artist
                if track.album:
                    ctx["album"] = track.album
                return ctx

            case "media.shuffle_toggle":
                new_state = not player._shuffle
                player.set_shuffle(new_state)
                return {"action": "shuffle_on" if new_state else "shuffle_off"}

            case _:
                logger.debug("MediaVoiceHandler: unhandled intent '%s'", intent)
                return None

    async def _resolve_station_by_ref(self, params: dict, player) -> dict | None:
        """Try to resolve and play station via entity_ref from pattern match.

        entity_ref format: "radio_station:42" → lookup RadioStation by DB id → play.
        Returns action context dict if station was found and played, None otherwise.
        """
        entity_ref = params.get("entity_ref", "")
        if not entity_ref or not entity_ref.startswith("radio_station:"):
            return None

        try:
            station_id = int(entity_ref.split(":")[1])
        except (ValueError, IndexError):
            return None

        try:
            from core.module_loader.sandbox import get_sandbox
            sf = get_sandbox()._session_factory
            if sf is None:
                return None

            from core.registry.models import RadioStation
            async with sf() as session:
                db_station = await session.get(RadioStation, station_id)
                if not db_station or not db_station.stream_url:
                    return None

                url = db_station.stream_url
                name = db_station.name_user or db_station.name_en
                logo = db_station.logo_url or ""

            await player.play_url(
                url, "radio",
                title=name,
                cover_url=logo or None,
            )
            return {"action": "play_station", "station": name}

        except Exception as exc:
            logger.debug("Station resolve by ref failed: %s", exc)
            return None
