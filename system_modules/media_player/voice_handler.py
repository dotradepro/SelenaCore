# system_modules/media_player/voice_handler.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .module import MediaPlayerModule

logger = logging.getLogger(__name__)

GENRE_MAP: dict[str, str] = {
    "рок": "rock",
    "джаз": "jazz",
    "классику": "classical",
    "классическую": "classical",
    "ambient": "ambient",
    "эмбиент": "ambient",
    "lofi": "lofi",
    "поп": "pop",
    "новости": "news",
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
                stations = lib.search(limit=1)
                if stations:
                    await player.play_url(stations[0]["url"], "radio")
                    await m.speak(f"Включаю радио. Станция {stations[0]['name']}")

            case "media.play_genre":
                genre_ru = params.get("genre", "")
                genre_en = GENRE_MAP.get(genre_ru.lower(), genre_ru)
                stations = lib.search(genre=genre_en, limit=5)
                if stations:
                    await player.play_url(stations[0]["url"], "radio")
                    await m.speak(
                        f"Включаю {genre_ru}. Станция {stations[0]['name']}"
                    )
                else:
                    await m.speak(f"Не нашёл радиостанции с жанром {genre_ru}")

            case "media.play_radio_name":
                name = params.get("station_name", "")
                results = lib.search(name=name, limit=1)
                if results:
                    await player.play_url(results[0]["url"], "radio")
                    await m.speak(f"Включаю {results[0]['name']}")
                else:
                    await m.speak(f"Радиостанция {name} не найдена")

            case "media.play_search":
                query = params.get("query", "")
                usb_tracks = await m._usb.scan()
                matches = [
                    t for t in usb_tracks
                    if query.lower() in (t["title"] + t["artist"]).lower()
                ]
                if matches:
                    track = matches[0]
                    await player.play_url(track["path"], "usb")
                    label = track["title"]
                    if track["artist"]:
                        label += f" — {track['artist']}"
                    await m.speak(f"Играю {label}")
                else:
                    await m.speak(
                        f"Не нашёл {query} на USB. Попробуй сказать 'включи радио'"
                    )

            case "media.next":
                await player.next()

            case "media.previous":
                await player.previous()

            case "media.stop":
                await player.stop()

            case "media.pause":
                state = player.get_state()
                await player.pause()
                await m.speak("Пауза" if state == "playing" else "Продолжаю")

            case "media.resume":
                if player.get_state() == "paused":
                    await player.pause()  # toggle

            case "media.volume_down":
                vol = max(0, player._volume - 15)
                await player.set_volume(vol)
                await m.speak(f"Громкость {vol}")

            case "media.volume_up":
                vol = min(100, player._volume + 15)
                await player.set_volume(vol)
                await m.speak(f"Громкость {vol}")

            case "media.volume_set":
                try:
                    level = int(params.get("level", 50))
                    await player.set_volume(level)
                    await m.speak(f"Громкость установлена на {level}")
                except (ValueError, TypeError):
                    pass

            case "media.whats_playing":
                track = player.get_current_track()
                state = player.get_state()
                if state not in ("playing", "paused") or not track:
                    await m.speak("Сейчас ничего не играет")
                else:
                    text = (
                        f"Играет {track.title}"
                        if track.title
                        else "Играет трек без названия"
                    )
                    if track.artist:
                        text += f" — {track.artist}"
                    if track.album:
                        text += f", альбом {track.album}"
                    await m.speak(text)

            case "media.shuffle_toggle":
                new_state = not player._shuffle
                player.set_shuffle(new_state)
                await m.speak(
                    "Перемешивание включено" if new_state else "Перемешивание выключено"
                )

            case _:
                logger.debug("MediaVoiceHandler: unhandled intent '%s'", intent)
