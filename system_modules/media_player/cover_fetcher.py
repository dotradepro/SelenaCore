# system_modules/media_player/cover_fetcher.py
# Three-level cover art lookup: file tags → MusicBrainz → Last.fm
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CACHE_DIR = Path("/var/lib/selena/modules/media-player/covers")

_MB_HEADERS = {
    "User-Agent": "SelenaCore/0.3 (https://github.com/dotradepro/SelenaCore)"
}


def _ensure_cache_dir() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


class CoverFetcher:
    def __init__(self, config: dict) -> None:
        self._config = config
        _ensure_cache_dir()

    # ── Public API ────────────────────────────────────────────────────────────

    async def fetch(self, artist: str, title: str) -> Optional[str]:
        """Return local cover URL or None.  Checks cache before hitting the network."""
        cache_key = hashlib.md5(f"{artist}|{title}".encode()).hexdigest()
        cache_path = CACHE_DIR / f"{cache_key}.jpg"

        if cache_path.exists():
            return f"/api/ui/modules/media-player/covers/{cache_key}.jpg"

        cover_url = await self._musicbrainz(artist, title)
        if not cover_url:
            cover_url = await self._lastfm(artist, title)

        if cover_url:
            await self._download(cover_url, cache_path)
            if cache_path.exists():
                return f"/api/ui/modules/media-player/covers/{cache_key}.jpg"

        return None

    async def fetch_from_file(self, filepath: str) -> Optional[str]:
        """Extract embedded cover art from MP3/FLAC tags via mutagen."""
        try:
            if filepath.lower().endswith(".flac"):
                from mutagen.flac import FLAC  # type: ignore[import-untyped]

                audio = FLAC(filepath)
                if audio.pictures:
                    data = audio.pictures[0].data
                    return self._save_raw(data)

            elif filepath.lower().endswith(".mp3"):
                from mutagen.id3 import APIC, ID3  # type: ignore[import-untyped]

                tags = ID3(filepath)
                for tag in tags.values():
                    if isinstance(tag, APIC):
                        return self._save_raw(tag.data)

        except Exception as exc:
            logger.warning("Cover extract from file failed (%s): %s", filepath, exc)
        return None

    def cache_size_mb(self) -> float:
        _ensure_cache_dir()
        total = sum(f.stat().st_size for f in CACHE_DIR.iterdir() if f.is_file())
        return round(total / 1024 / 1024, 2)

    def clear_cache(self) -> int:
        _ensure_cache_dir()
        count = 0
        for f in CACHE_DIR.iterdir():
            if f.is_file():
                f.unlink()
                count += 1
        return count

    # ── Private helpers ───────────────────────────────────────────────────────

    def _save_raw(self, data: bytes) -> str:
        _ensure_cache_dir()
        key = hashlib.md5(data).hexdigest()
        out = CACHE_DIR / f"{key}.jpg"
        out.write_bytes(data)
        return f"/api/ui/modules/media-player/covers/{key}.jpg"

    async def _musicbrainz(self, artist: str, title: str) -> Optional[str]:
        """MusicBrainz + Cover Art Archive — free, no key required.

        Rate limit: 1 req/sec per MusicBrainz TOS.
        """
        try:
            async with httpx.AsyncClient(
                timeout=6.0, headers=_MB_HEADERS
            ) as client:
                resp = await client.get(
                    "https://musicbrainz.org/ws/2/recording",
                    params={
                        "query": f'artist:"{artist}" AND recording:"{title}"',
                        "fmt": "json",
                        "limit": 1,
                    },
                )
                data = resp.json()
                recordings = data.get("recordings", [])
                if not recordings:
                    return None

                for release in recordings[0].get("releases", [])[:3]:
                    rid = release.get("id")
                    if not rid:
                        continue
                    try:
                        r2 = await client.get(
                            f"https://coverartarchive.org/release/{rid}/front-250",
                            follow_redirects=True,
                        )
                        if r2.status_code == 200:
                            return str(r2.url)
                    except Exception:
                        continue
        except Exception as exc:
            logger.debug("MusicBrainz lookup failed: %s", exc)
        return None

    async def _lastfm(self, artist: str, title: str) -> Optional[str]:
        """Last.fm API — free key from last.fm/api."""
        api_key = self._config.get("lastfm_api_key", "")
        if not api_key:
            return None
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://ws.audioscrobbler.com/2.0/",
                    params={
                        "method": "track.getInfo",
                        "api_key": api_key,
                        "artist": artist,
                        "track": title,
                        "format": "json",
                    },
                )
                data = resp.json()
                images = (
                    data.get("track", {}).get("album", {}).get("image", [])
                )
                for img in reversed(images):
                    if img.get("#text"):
                        return img["#text"]
        except Exception as exc:
            logger.debug("Last.fm lookup failed: %s", exc)
        return None

    async def _download(self, url: str, path: Path) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code == 200:
                    path.write_bytes(resp.content)
        except Exception as exc:
            logger.warning("Cover download failed (%s): %s", url, exc)
