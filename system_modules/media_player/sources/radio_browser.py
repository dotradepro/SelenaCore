# system_modules/media_player/sources/radio_browser.py
# radio-browser.info — free public API, no registration required.
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RADIO_BROWSER_SERVERS = [
    "https://de1.api.radio-browser.info",
    "https://nl1.api.radio-browser.info",
    "https://at1.api.radio-browser.info",
]

_HEADERS = {"User-Agent": "SelenaCore/0.3 (https://github.com/dotradepro/SelenaCore)"}


class RadioBrowserSource:
    def __init__(self) -> None:
        self._base = RADIO_BROWSER_SERVERS[0]

    async def search(
        self,
        tag: str = "",
        name: str = "",
        country: str = "",
        countrycode: str = "",
        limit: int = 20,
        offset: int = 0,
        order: str = "votes",
        min_bitrate: int = 0,
        codec: str = "",
        is_https: bool = True,
    ) -> list[dict]:
        params: dict = {
            "tag": tag,
            "name": name,
            "country": country,
            "limit": limit,
            "offset": offset,
            "order": order,
            "reverse": "true",
            "hidebroken": "true",
            "min_bitrate": min_bitrate,
        }
        if countrycode:
            params["countrycode"] = countrycode
        if codec:
            params["codec"] = codec
        if is_https:
            params["is_https"] = "true"

        try:
            async with httpx.AsyncClient(timeout=8.0, headers=_HEADERS) as client:
                resp = await client.get(
                    f"{self._base}/json/stations/search", params=params
                )
                resp.raise_for_status()
                stations: list[dict] = resp.json()
        except Exception as exc:
            logger.error("RadioBrowser search failed: %s", exc)
            return []

        return [
            {
                "uuid": s.get("stationuuid", ""),
                "name": s.get("name", ""),
                "url": s.get("url_resolved") or s.get("url", ""),
                "bitrate": s.get("bitrate", 0),
                "codec": s.get("codec", ""),
                "country": s.get("country", ""),
                "tags": s.get("tags", ""),
                "favicon": s.get("favicon", ""),
                "votes": s.get("votes", 0),
            }
            for s in stations
            if s.get("url_resolved") or s.get("url")
        ]

    async def get_by_name(self, name: str) -> Optional[dict]:
        results = await self.search(name=name, limit=1)
        return results[0] if results else None

    async def click(self, station_uuid: str) -> None:
        """Notify radio-browser.info of a listen (improves station ranking)."""
        if not station_uuid:
            return
        try:
            async with httpx.AsyncClient(timeout=3.0, headers=_HEADERS) as client:
                await client.get(f"{self._base}/json/url/{station_uuid}")
        except Exception:
            pass

    async def get_countries(self, limit: int = 300) -> list[dict]:
        """Return list of {name, countrycode, stationcount} sorted by station count."""
        try:
            async with httpx.AsyncClient(timeout=8.0, headers=_HEADERS) as client:
                resp = await client.get(
                    f"{self._base}/json/countrycodes",
                    params={
                        "limit": limit,
                        "order": "stationcount",
                        "reverse": "true",
                        "hidebroken": "true",
                    },
                )
                resp.raise_for_status()
                rows = resp.json()
                return [
                    {"code": r["name"], "stationcount": r["stationcount"]}
                    for r in rows
                    if r.get("name")
                ]
        except Exception as exc:
            logger.warning("RadioBrowser countries fetch failed: %s", exc)
            return []

    async def get_tags(self, limit: int = 50) -> list[str]:
        """Top genres/tags for display in settings."""
        try:
            async with httpx.AsyncClient(timeout=5.0, headers=_HEADERS) as client:
                resp = await client.get(
                    f"{self._base}/json/tags",
                    params={
                        "limit": limit,
                        "order": "stationcount",
                        "reverse": "true",
                        "hidebroken": "true",
                    },
                )
                resp.raise_for_status()
                return [t["name"] for t in resp.json() if t.get("name")]
        except Exception as exc:
            logger.warning("RadioBrowser tags fetch failed: %s", exc)
            return ["classical", "jazz", "ambient", "lofi", "rock", "pop", "news"]
