# system_modules/media_player/sources/radio_library.py
"""Local radio station library.

Storage format:
  - Internal: JSON  — /var/lib/selena/modules/media-player/stations.json
  - Import/Export:  M3U8 Extended (#EXTM3U + #EXTINF with attributes)

M3U8 attribute set used by this library:
  tvg-id      — station UUID
  tvg-logo    — logo URL
  tvg-name    — display name (also after the trailing comma)
  group-title — genre / tag list (comma-separated)
  country     — ISO country name or code
  language    — language name
  bitrate     — kbps integer
  codec       — MP3 | AAC | OGG | FLAC | …
  homepage    — station website

JSON station schema:
  {
    "id":        str (UUID),
    "name":      str,
    "url":       str,
    "genre":     str   (primary genre),
    "tags":      [str] (all tags / genres),
    "country":   str,
    "language":  str,
    "bitrate":   int   (kbps, 0 = unknown),
    "codec":     str,
    "logo":      str   (URL),
    "homepage":  str   (URL),
    "favourite": bool,
    "added_at":  float (Unix timestamp),
  }
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_FILE = Path("/var/lib/selena/modules/media-player/stations.json")


class RadioLibrary:
    """In-memory radio station library backed by a JSON file."""

    def __init__(self, data_file: Path | None = None) -> None:
        self._file: Path = data_file or DEFAULT_FILE
        self._stations: list[dict] = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._file.exists():
            return
        try:
            raw = json.loads(self._file.read_text(encoding="utf-8"))
            self._stations = raw.get("stations", [])
            logger.info("RadioLibrary: loaded %d stations from %s", len(self._stations), self._file)
        except Exception as exc:
            logger.error("RadioLibrary: load failed (%s): %s", self._file, exc)
            self._stations = []

    def _save(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps({"stations": self._stations}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("RadioLibrary: save failed: %s", exc)

    # ── Read operations ───────────────────────────────────────────────────────

    def count(self) -> int:
        return len(self._stations)

    def all(self) -> list[dict]:
        return list(self._stations)

    def favourites(self) -> list[dict]:
        return [s for s in self._stations if s.get("favourite")]

    def _filter(
        self,
        name: str = "",
        genre: str = "",
        country: str = "",
        min_bitrate: int = 0,
        codec: str = "",
    ) -> list[dict]:
        """Return filtered list without slicing."""
        results: list[dict] = self._stations

        if name:
            nl = name.lower()
            results = [s for s in results if nl in s.get("name", "").lower()]

        if genre:
            gl = genre.lower()
            results = [
                s for s in results
                if gl in s.get("genre", "").lower()
                or any(gl in t.lower() for t in s.get("tags", []))
            ]

        if country:
            cl = country.lower()
            results = [s for s in results if cl in s.get("country", "").lower()]

        if min_bitrate > 0:
            results = [s for s in results if s.get("bitrate", 0) >= min_bitrate]

        if codec:
            cl = codec.lower()
            results = [s for s in results if s.get("codec", "").lower() == cl]

        return results

    def search(
        self,
        name: str = "",
        genre: str = "",
        country: str = "",
        min_bitrate: int = 0,
        codec: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Filter stations locally — no network calls."""
        results = self._filter(name=name, genre=genre, country=country,
                               min_bitrate=min_bitrate, codec=codec)
        return results[offset:offset + limit]

    def search_paged(
        self,
        name: str = "",
        genre: str = "",
        country: str = "",
        min_bitrate: int = 0,
        codec: str = "",
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        """Return paginated result with total count."""
        results = self._filter(name=name, genre=genre, country=country,
                               min_bitrate=min_bitrate, codec=codec)
        return {
            "items": results[offset:offset + limit],
            "total": len(results),
            "offset": offset,
            "limit": limit,
        }

    def get_genres(self) -> list[str]:
        seen: set[str] = set()
        for s in self._stations:
            for t in s.get("tags", []):
                if t:
                    seen.add(t)
            if s.get("genre"):
                seen.add(s["genre"])
        return sorted(seen)

    def get_countries(self) -> list[str]:
        return sorted({s["country"] for s in self._stations if s.get("country")})

    def get_codecs(self) -> list[str]:
        return sorted({s["codec"] for s in self._stations if s.get("codec")})

    def stats(self) -> dict:
        return {
            "total": len(self._stations),
            "favourites": len(self.favourites()),
            "genres": len(self.get_genres()),
            "countries": len(self.get_countries()),
        }

    # ── Write operations ──────────────────────────────────────────────────────

    def add(self, station: dict) -> dict:
        """Add one station. Skips duplicates (by URL). Returns the station record."""
        station = dict(station)
        if not station.get("id"):
            station["id"] = str(uuid.uuid4())
        station.setdefault("tags", [])
        station.setdefault("genre", station["tags"][0] if station["tags"] else "")
        station.setdefault("country", "")
        station.setdefault("language", "")
        station.setdefault("bitrate", 0)
        station.setdefault("codec", "")
        station.setdefault("logo", "")
        station.setdefault("homepage", "")
        station.setdefault("favourite", False)
        station.setdefault("added_at", time.time())

        url = station.get("url", "")
        if url and any(s.get("url") == url for s in self._stations):
            return station  # duplicate, skip silently

        self._stations.append(station)
        self._save()
        return station

    def add_many(self, stations: list[dict]) -> int:
        """Bulk-add stations, de-duplicating by URL. Returns count added."""
        existing = {s.get("url") for s in self._stations}
        added = 0
        for raw in stations:
            station = dict(raw)
            if not station.get("id"):
                station["id"] = str(uuid.uuid4())
            station.setdefault("tags", [])
            station.setdefault("genre", station["tags"][0] if station["tags"] else "")
            station.setdefault("country", "")
            station.setdefault("language", "")
            station.setdefault("bitrate", 0)
            station.setdefault("codec", "")
            station.setdefault("logo", "")
            station.setdefault("homepage", "")
            station.setdefault("favourite", False)
            station.setdefault("added_at", time.time())
            url = station.get("url", "")
            if url and url not in existing:
                self._stations.append(station)
                existing.add(url)
                added += 1
        if added > 0:
            self._save()
        return added

    def remove(self, station_id: str) -> bool:
        before = len(self._stations)
        self._stations = [s for s in self._stations if s.get("id") != station_id]
        if len(self._stations) < before:
            self._save()
            return True
        return False

    def toggle_favourite(self, station_id: str) -> bool | None:
        for s in self._stations:
            if s.get("id") == station_id:
                s["favourite"] = not s.get("favourite", False)
                self._save()
                return bool(s["favourite"])
        return None

    def clear(self) -> int:
        count = len(self._stations)
        self._stations = []
        self._save()
        return count

    # ── M3U8 Import ───────────────────────────────────────────────────────────

    def parse_m3u(self, content: str) -> list[dict]:
        """Parse M3U8 Extended content into a list of station dicts (not saved)."""
        stations: list[dict] = []
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTINF:"):
                meta = line[len("#EXTINF:"):]

                # Extract key="value" or key=value attributes
                attrs: dict[str, str] = {}
                for m in re.finditer(r'([\w-]+)=["\']?([^,"\'>\s][^,"\']*?)["\']?(?=\s+[\w-]+=|,|$)', meta):
                    attrs[m.group(1).lower()] = m.group(2).strip()

                # Display name is everything after the last unquoted comma
                display_name = ""
                comma_pos = meta.rfind(",")
                if comma_pos != -1:
                    display_name = meta[comma_pos + 1:].strip()

                # Find the URL on the next non-comment line
                url = ""
                j = i + 1
                while j < len(lines):
                    candidate = lines[j].strip()
                    if candidate and not candidate.startswith("#"):
                        url = candidate
                        i = j
                        break
                    j += 1

                if url:
                    raw_group = attrs.get("group-title", attrs.get("tags", ""))
                    tags = [t.strip() for t in re.split(r"[;,]", raw_group) if t.strip()]
                    stations.append({
                        "id": attrs.get("tvg-id") or str(uuid.uuid4()),
                        "name": display_name or attrs.get("tvg-name", url),
                        "url": url,
                        "genre": tags[0] if tags else "",
                        "tags": tags,
                        "country": attrs.get("country", attrs.get("tvg-country", "")),
                        "language": attrs.get("language", attrs.get("tvg-language", "")),
                        "bitrate": int(attrs.get("bitrate", "0") or "0"),
                        "codec": attrs.get("codec", "").upper(),
                        "logo": attrs.get("tvg-logo", attrs.get("logo", "")),
                        "homepage": attrs.get("homepage", ""),
                    })
            i += 1
        return stations

    def import_m3u(self, content: str) -> int:
        """Parse and add stations from M3U8 text. Returns count added."""
        parsed = self.parse_m3u(content)
        return self.add_many(parsed)

    # ── M3U8 Export ───────────────────────────────────────────────────────────

    def export_m3u(self, stations: list[dict] | None = None) -> str:
        """Export stations (all by default) as M3U8 Extended format string."""
        items = stations if stations is not None else self._stations
        lines = ["#EXTM3U"]
        for s in items:
            parts = [f'tvg-id="{s.get("id", "")}"']
            if s.get("logo"):
                parts.append(f'tvg-logo="{s["logo"]}"')
            tags = ",".join(s.get("tags", [])) or s.get("genre", "")
            if tags:
                parts.append(f'group-title="{tags}"')
            if s.get("country"):
                parts.append(f'country="{s["country"]}"')
            if s.get("language"):
                parts.append(f'language="{s["language"]}"')
            if s.get("bitrate"):
                parts.append(f'bitrate="{s["bitrate"]}"')
            if s.get("codec"):
                parts.append(f'codec="{s["codec"]}"')
            if s.get("homepage"):
                parts.append(f'homepage="{s["homepage"]}"')
            attrs = " ".join(parts)
            lines.append(f'#EXTINF:-1 {attrs},{s.get("name", "")}')
            lines.append(s.get("url", ""))
        return "\n".join(lines)
