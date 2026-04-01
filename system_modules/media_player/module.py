"""system_modules/media_player/module.py — MediaPlayerModule (SystemModule).

In-process system module — NOT a separate container.
Mounted at /api/ui/modules/media-player/ by the Plugin Manager.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from core.module_loader.system_module import SystemModule

from .cover_fetcher import CoverFetcher
from .player import MediaPlayer
from .sources.archive_source import InternetArchiveSource
from .sources.radio_browser import RadioBrowserSource
from .sources.radio_library import RadioLibrary
from .sources.smb_source import SMBSource
from .sources.usb_source import USBSource
from .voice_handler import MediaVoiceHandler

logger = logging.getLogger(__name__)

COVER_DIR = Path("/var/lib/selena/modules/media-player/covers")
MODULE_DIR = Path(__file__).parent


class PlayBody(BaseModel):
    url: str
    source_type: str = "http"
    title: str = ""
    cover_url: str = ""


class VolumeBody(BaseModel):
    volume: int


class SeekBody(BaseModel):
    position: float


class SMBMountBody(BaseModel):
    host: str
    share: str
    username: str = "guest"
    password: str = ""
    domain: str = "WORKGROUP"


class NFSMountBody(BaseModel):
    host: str
    export: str


class ConfigBody(BaseModel):
    lastfm_api_key: str | None = None
    default_volume: int | None = None
    stream_buffer_ms: int | None = None
    normalize_volume: bool | None = None


class AddStationBody(BaseModel):
    name: str
    url: str
    genre: str = ""
    country: str = ""
    language: str = ""
    bitrate: int = 0
    codec: str = ""
    logo: str = ""
    homepage: str = ""


class ImportM3UBody(BaseModel):
    content: str


class ImportRBBody(BaseModel):
    tag: str = ""
    name: str = ""
    country: str = ""
    limit: int = 100
    min_bitrate: int = 0
    codec: str = ""


class MediaPlayerModule(SystemModule):
    name = "media-player"

    def __init__(self) -> None:
        super().__init__()
        self._player: MediaPlayer
        self._cover: CoverFetcher
        self._library: RadioLibrary
        self._rb_importer: RadioBrowserSource
        self._usb: USBSource
        self._smb: SMBSource
        self._archive: InternetArchiveSource
        self._voice: MediaVoiceHandler
        self._config: dict[str, Any] = {}
        self._state_task: asyncio.Task | None = None
        # Audio ducking: lower media volume during TTS playback
        self._pre_duck_volume: int | None = None
        self._duck_volume: int = 15
        self._duck_generation: int = 0  # incremented on each tts_start

    async def start(self) -> None:
        COVER_DIR.mkdir(parents=True, exist_ok=True)

        self._config = self._load_config_from_env()
        self._player = MediaPlayer()
        self._cover = CoverFetcher(self._config)
        data_dir = COVER_DIR.parent
        self._library = RadioLibrary(data_file=data_dir / "stations.json")
        self._rb_importer = RadioBrowserSource()
        self._usb = USBSource()
        self._smb = SMBSource()
        self._archive = InternetArchiveSource()
        self._voice = MediaVoiceHandler(self)

        # Apply default volume
        default_vol = self._config.get("default_volume", 70)
        await self._player.set_volume(int(default_vol))

        # Subscribe to EventBus (DirectSubscription — no HTTP)
        self.subscribe(
            ["voice.intent", "device.state_changed"],
            self._on_event,
        )
        # Audio ducking: lower volume during TTS playback
        self.subscribe(["voice.tts_start"], self._on_tts_start)
        self.subscribe(["voice.tts_done"], self._on_tts_done)

        # Register voice intent patterns with IntentRouter (Tier 1.5)
        try:
            from system_modules.llm_engine.intent_router import get_intent_router
            from .intent_patterns import MEDIA_INTENTS
            intent_router = get_intent_router()
            for entry in MEDIA_INTENTS:
                intent_router.register_system_intent(entry)
            logger.info("MediaPlayer: registered %d voice intents", len(MEDIA_INTENTS))
        except Exception as exc:
            logger.warning("MediaPlayer: failed to register intents: %s", exc)

        # Broadcast state periodically while playing
        self._state_task = asyncio.create_task(self._state_broadcast_loop())

        await self.publish("module.started", {"name": self.name})
        logger.info("MediaPlayer module started")

    async def stop(self) -> None:
        if self._state_task:
            self._state_task.cancel()
        await self._player.stop()
        self._player.release()
        self._cleanup_subscriptions()
        try:
            from system_modules.llm_engine.intent_router import get_intent_router
            get_intent_router().unregister_system_intents(self.name)
        except Exception:
            pass
        await self.publish("module.stopped", {"name": self.name})
        logger.info("MediaPlayer module stopped")

    # ── EventBus handler ──────────────────────────────────────────────────────

    async def _on_event(self, event: Any) -> None:
        etype = event.type
        payload = event.payload

        if etype == "voice.intent":
            intent = payload.get("intent", "")
            if intent.startswith("media."):
                await self._voice.handle(intent, payload.get("params", {}))

    # ── Audio ducking (lower volume during TTS) ────────────────────────────────

    async def _on_tts_start(self, event: Any) -> None:
        """Duck media volume when TTS begins."""
        if self._player.get_state() == "playing":
            if self._pre_duck_volume is None:
                self._pre_duck_volume = self._player._volume
            self._duck_generation += 1
            await self._player.set_volume(self._duck_volume)
            logger.debug("Audio ducking: %d → %d", self._pre_duck_volume, self._duck_volume)

    async def _on_tts_done(self, event: Any) -> None:
        """Restore media volume when TTS ends."""
        if self._pre_duck_volume is not None:
            gen = self._duck_generation
            # Grace period: if another TTS starts within 300ms, stay ducked
            await asyncio.sleep(0.3)
            if self._pre_duck_volume is not None and self._duck_generation == gen:
                await self._player.set_volume(self._pre_duck_volume)
                logger.debug("Audio ducking restored: → %d", self._pre_duck_volume)
                self._pre_duck_volume = None

    # ── Background tasks ──────────────────────────────────────────────────────

    async def _state_broadcast_loop(self) -> None:
        """Broadcast media.state_changed every 3 s while playing/buffering."""
        while True:
            await asyncio.sleep(3)
            try:
                state = self._player.get_state()
                if state in ("playing", "buffering"):
                    status = self._player.get_status()
                    if state == "playing":
                        track = self._player.get_current_track()
                        if track and track.artist and track.title:
                            cover = await self._cover.fetch(track.artist, track.title)
                            if cover and status.get("track") is not None:
                                status["track"]["cover_url"] = cover
                                # Persist fetched cover so polling picks it up too
                                if self._player._stub_track:
                                    self._player._stub_track.cover_url = cover
                    await self.publish("media.state_changed", status)
            except Exception as exc:
                logger.debug("State broadcast error: %s", exc)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def speak(self, text: str) -> None:
        """Send text to TTS via EventBus → voice-core."""
        await self.publish("voice.speak", {"text": text})

    def _load_config_from_env(self) -> dict[str, Any]:
        return {
            "lastfm_api_key": os.getenv("MEDIA_LASTFM_API_KEY", ""),
            "default_volume": int(os.getenv("MEDIA_DEFAULT_VOLUME", "70")),
            "stream_buffer_ms": int(os.getenv("MEDIA_STREAM_BUFFER_MS", "1000")),
            "normalize_volume": os.getenv("MEDIA_NORMALIZE", "false").lower() == "true",
        }

    # ── Router ────────────────────────────────────────────────────────────────

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        # ── Widget / Settings HTML ────────────────────────────────────────────

        @router.get("/widget", response_class=HTMLResponse)
        async def serve_widget() -> HTMLResponse:
            path = MODULE_DIR / "widget.html"
            return HTMLResponse(path.read_text(encoding="utf-8"))

        @router.get("/settings", response_class=HTMLResponse)
        async def serve_settings() -> HTMLResponse:
            path = MODULE_DIR / "settings.html"
            return HTMLResponse(path.read_text(encoding="utf-8"))

        # ── Playback controls ─────────────────────────────────────────────────

        @router.get("/player/state")
        async def get_state() -> dict:
            return svc._player.get_status()

        @router.post("/player/play")
        async def play(body: PlayBody) -> dict:
            await svc._player.play_url(
                body.url, body.source_type,
                title=body.title, cover_url=body.cover_url or None,
            )
            state = svc._player.get_state()
            if state == "error":
                raise HTTPException(status_code=502, detail="VLC failed to start playback")
            return {"ok": True, "state": state}

        @router.post("/player/pause")
        async def pause() -> dict:
            await svc._player.pause()
            return {"state": svc._player.get_state()}

        @router.post("/player/stop")
        async def stop() -> dict:
            await svc._player.stop()
            return {"ok": True}

        @router.post("/player/next")
        async def next_track() -> dict:
            await svc._player.next()
            return {"ok": True}

        @router.post("/player/previous")
        async def prev_track() -> dict:
            await svc._player.previous()
            return {"ok": True}

        @router.post("/player/volume")
        async def set_volume(body: VolumeBody) -> dict:
            await svc._player.set_volume(body.volume)
            return {"volume": svc._player._volume}

        @router.post("/player/seek")
        async def seek(body: SeekBody) -> dict:
            await svc._player.seek(body.position)
            return {"position": svc._player.get_position()}

        @router.post("/player/shuffle")
        async def toggle_shuffle(body: dict) -> dict:
            enabled = bool(body.get("enabled", not svc._player._shuffle))
            svc._player.set_shuffle(enabled)
            return {"shuffle": svc._player._shuffle}

        # ── Sources ───────────────────────────────────────────────────────────

        # ── Radio library — local search/filter (no network) ─────────────────

        @router.get("/sources/radio/stats")
        async def radio_stats() -> dict:
            return svc._library.stats()

        @router.get("/sources/radio/search")
        async def radio_search(
            name: str = Query(""),
            genre: str = Query(""),
            country: str = Query(""),
            min_bitrate: int = Query(0),
            codec: str = Query(""),
            limit: int = Query(50),
        ) -> list:
            """Filter stations from local library — no external API calls."""
            return svc._library.search(
                name=name,
                genre=genre,
                country=country,
                min_bitrate=min_bitrate,
                codec=codec,
                limit=limit,
            )

        @router.get("/sources/radio/page")
        async def radio_page(
            name: str = Query(""),
            country: str = Query(""),
            min_bitrate: int = Query(0),
            codec: str = Query(""),
            limit: int = Query(20),
            offset: int = Query(0),
        ) -> dict:
            """Paginated radio library search for the widget."""
            return svc._library.search_paged(
                name=name,
                country=country,
                min_bitrate=min_bitrate,
                codec=codec,
                limit=min(limit, 100),
                offset=offset,
            )

        @router.get("/sources/radio/genres")
        async def radio_genres() -> list:
            return svc._library.get_genres()

        @router.get("/sources/radio/countries")
        async def radio_countries() -> list:
            return svc._library.get_countries()

        @router.get("/sources/radio/codecs")
        async def radio_codecs() -> list:
            return svc._library.get_codecs()

        @router.get("/sources/radio/favourites")
        async def radio_favourites() -> list:
            return svc._library.favourites()

        @router.post("/sources/radio/favourite/{station_id}")
        async def radio_toggle_favourite(station_id: str) -> dict:
            result = svc._library.toggle_favourite(station_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Station not found")
            return {"id": station_id, "favourite": result}

        @router.post("/sources/radio/add")
        async def radio_add(body: AddStationBody) -> dict:
            station = svc._library.add({
                "name": body.name,
                "url": body.url,
                "genre": body.genre,
                "tags": [body.genre] if body.genre else [],
                "country": body.country,
                "language": body.language,
                "bitrate": body.bitrate,
                "codec": body.codec.upper(),
                "logo": body.logo,
                "homepage": body.homepage,
            })
            return station

        @router.post("/sources/radio/add-many")
        async def radio_add_many(body: list) -> dict:  # type: ignore[type-arg]
            added = svc._library.add_many(body)
            return {"added": added, "total": svc._library.count()}

        @router.delete("/sources/radio/station/{station_id}")
        async def radio_remove(station_id: str) -> dict:
            ok = svc._library.remove(station_id)
            if not ok:
                raise HTTPException(status_code=404, detail="Station not found")
            return {"ok": True}

        @router.post("/sources/radio/clear")
        async def radio_clear() -> dict:
            count = svc._library.clear()
            return {"cleared": count}

        @router.get("/sources/radio/export.m3u")
        async def radio_export_m3u() -> Response:
            content = svc._library.export_m3u()
            return Response(
                content=content.encode("utf-8"),
                media_type="audio/x-mpegurl",
                headers={"Content-Disposition": 'attachment; filename="stations.m3u"'},
            )

        @router.post("/sources/radio/import/m3u")
        async def radio_import_m3u(body: ImportM3UBody) -> dict:
            added = svc._library.import_m3u(body.content)
            return {"added": added, "total": svc._library.count()}

        @router.get("/sources/radio-browser/search")
        async def rb_browse(
            name: str = Query(""),
            tag: str = Query(""),
            countrycode: str = Query(""),
            codec: str = Query(""),
            min_bitrate: int = Query(0),
            order: str = Query("votes"),
            limit: int = Query(20),
            offset: int = Query(0),
        ) -> dict:
            """Browse radio-browser.info directly with pagination (no local import)."""
            limit = min(max(limit, 1), 100)
            items = await svc._rb_importer.search(
                name=name,
                tag=tag,
                countrycode=countrycode,
                codec=codec,
                min_bitrate=min_bitrate,
                order=order,
                limit=limit,
                offset=offset,
            )
            return {"items": items, "limit": limit, "offset": offset}

        @router.get("/sources/radio-browser/countries")
        async def rb_countries() -> list:
            """Country list from radio-browser.info ordered by station count."""
            return await svc._rb_importer.get_countries()

        @router.post("/sources/radio/import/radio-browser")
        async def radio_import_rb(body: ImportRBBody) -> dict:
            """Fetch stations from radio-browser.info and add to local library."""
            stations = await svc._rb_importer.search(
                tag=body.tag,
                name=body.name,
                country=body.country,
                limit=body.limit,
                min_bitrate=body.min_bitrate,
                codec=body.codec,
            )
            records = [
                {
                    "id": s.get("uuid") or "",
                    "name": s.get("name", ""),
                    "url": s.get("url", ""),
                    "genre": s.get("tags", "").split(",")[0].strip() if s.get("tags") else "",
                    "tags": [t.strip() for t in s.get("tags", "").split(",") if t.strip()],
                    "country": s.get("country", ""),
                    "bitrate": s.get("bitrate", 0),
                    "codec": s.get("codec", ""),
                    "logo": s.get("favicon", ""),
                    "homepage": "",
                }
                for s in stations
            ]
            added = svc._library.add_many(records)
            return {"fetched": len(stations), "added": added, "total": svc._library.count()}

        @router.get("/sources/usb/scan")
        async def usb_scan() -> list:
            return await svc._usb.scan()

        @router.get("/sources/usb/devices")
        async def usb_devices() -> list:
            return svc._usb.get_mounted_devices()

        @router.post("/sources/smb/mount")
        async def smb_mount(body: SMBMountBody) -> dict:
            try:
                mp = await svc._smb.mount_smb(
                    host=body.host,
                    share=body.share,
                    username=body.username,
                    password=body.password,
                    domain=body.domain,
                )
                return {"mount_point": mp, "ok": True}
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @router.post("/sources/nfs/mount")
        async def nfs_mount(body: NFSMountBody) -> dict:
            try:
                mp = await svc._smb.mount_nfs(host=body.host, export=body.export)
                return {"mount_point": mp, "ok": True}
            except RuntimeError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc

        @router.get("/sources/archive/search")
        async def archive_search(
            q: str = Query(""),
            rows: int = Query(10),
            collection: str = Query(""),
        ) -> list:
            return await svc._archive.search(
                query=q, rows=rows, collection=collection
            )

        @router.get("/sources/archive/item/{identifier}")
        async def archive_item(identifier: str) -> dict:
            item = await svc._archive.get_item(identifier)
            if item is None:
                raise HTTPException(status_code=404, detail="Item not found")
            return item

        # ── Covers ────────────────────────────────────────────────────────────

        @router.get("/covers/{filename}")
        async def serve_cover(filename: str) -> Response:
            # Restrict to safe filenames — only hex + extension
            safe_chars = set("0123456789abcdef.")
            name_lower = filename.lower()
            if not all(c in safe_chars for c in name_lower):
                raise HTTPException(status_code=400, detail="Invalid filename")
            if not name_lower.endswith((".jpg", ".jpeg", ".png")):
                raise HTTPException(status_code=400, detail="Invalid file type")
            path = COVER_DIR / filename
            if not path.exists() or not path.is_file():
                raise HTTPException(status_code=404, detail="Cover not found")
            return FileResponse(str(path))

        @router.get("/covers/cache/info")
        async def cover_cache_info() -> dict:
            return {"size_mb": svc._cover.cache_size_mb()}

        @router.post("/covers/cache/clear")
        async def cover_cache_clear() -> dict:
            count = svc._cover.clear_cache()
            return {"cleared": count}

        # ── Config ────────────────────────────────────────────────────────────

        @router.get("/config")
        async def get_config() -> dict:
            # Never expose API keys in plain text — mask them
            cfg = dict(svc._config)
            if cfg.get("lastfm_api_key"):
                cfg["lastfm_api_key"] = "****"
            return cfg

        @router.post("/config")
        async def update_config(body: ConfigBody) -> dict:
            if body.lastfm_api_key is not None:
                svc._config["lastfm_api_key"] = body.lastfm_api_key
                svc._cover._config["lastfm_api_key"] = body.lastfm_api_key
            if body.default_volume is not None:
                vol = max(0, min(100, body.default_volume))
                svc._config["default_volume"] = vol
            if body.stream_buffer_ms is not None:
                svc._config["stream_buffer_ms"] = body.stream_buffer_ms
            if body.normalize_volume is not None:
                svc._config["normalize_volume"] = body.normalize_volume
            return {"ok": True}

        @router.post("/config/lastfm/verify")
        async def verify_lastfm() -> dict:
            key = svc._config.get("lastfm_api_key", "")
            if not key:
                return {"ok": False, "error": "No API key configured"}
            import httpx
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(
                        "https://ws.audioscrobbler.com/2.0/",
                        params={
                            "method": "chart.getTopTracks",
                            "api_key": key,
                            "format": "json",
                            "limit": 1,
                        },
                    )
                    data = resp.json()
                    if "error" in data:
                        return {"ok": False, "error": data.get("message", "Invalid key")}
                    return {"ok": True}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

        # ── Health ────────────────────────────────────────────────────────────

        @router.get("/health")
        async def health() -> dict:
            return {
                "status": "ok",
                "name": svc.name,
                "version": "0.1.0",
                "player_state": svc._player.get_state(),
            }

        return router
