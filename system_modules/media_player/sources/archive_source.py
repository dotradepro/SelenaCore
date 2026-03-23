# system_modules/media_player/sources/archive_source.py
# Internet Archive (archive.org) — Public Domain + CC content, no API key required.
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

BASE = "https://archive.org"


class InternetArchiveSource:
    """Search and stream audio from archive.org.

    No API key required.  Rate limiting is generous for reasonable usage.
    Preferred collections: etree (live concerts), librivox (audiobooks),
    georgeblood (78rpm HD), classical_music_library.
    """

    async def search(
        self,
        query: str,
        rows: int = 10,
        collection: str = "",
        media_type: str = "audio",
    ) -> list[dict]:
        q = f"({query}) AND mediatype:{media_type}"
        if collection:
            q += f" AND collection:{collection}"

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(
                    f"{BASE}/advancedsearch.php",
                    params={
                        "q": q,
                        "fl": "identifier,title,creator,year,description",
                        "rows": rows,
                        "output": "json",
                        "sort": "downloads desc",
                    },
                )
                resp.raise_for_status()
                docs: list[dict] = (
                    resp.json().get("response", {}).get("docs", [])
                )
        except Exception as exc:
            logger.error("Archive.org search failed: %s", exc)
            return []

        results: list[dict] = []
        for doc in docs:
            iid = doc.get("identifier", "")
            if not iid:
                continue
            try:
                files = await self._get_files(iid)
                mp3s = [f for f in files if f.get("name", "").lower().endswith(".mp3")]
                flacs = [f for f in files if f.get("name", "").lower().endswith(".flac")]
                tracks = flacs or mp3s  # prefer FLAC
                if not tracks:
                    continue
                results.append(
                    {
                        "title": doc.get("title", iid),
                        "creator": doc.get("creator", ""),
                        "year": doc.get("year", ""),
                        "identifier": iid,
                        "page_url": f"{BASE}/details/{iid}",
                        "cover_url": f"{BASE}/services/img/{iid}",
                        "tracks": [
                            {
                                "name": f["name"],
                                "url": f"{BASE}/download/{iid}/{f['name']}",
                                "size_mb": round(
                                    int(f.get("size", 0)) / 1024 / 1024, 1
                                ),
                            }
                            for f in tracks
                        ],
                        "first_track_url": (
                            f"{BASE}/download/{iid}/{tracks[0]['name']}"
                        ),
                    }
                )
            except Exception as exc:
                logger.debug("Archive.org file list for %s failed: %s", iid, exc)

        return results

    async def _get_files(self, identifier: str) -> list[dict]:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{BASE}/metadata/{identifier}/files")
            resp.raise_for_status()
            return resp.json().get("result", [])

    async def get_item(self, identifier: str) -> Optional[dict]:
        """Return metadata + file list for a specific Archive.org item."""
        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"{BASE}/metadata/{identifier}")
                resp.raise_for_status()
                data = resp.json()
            meta = data.get("metadata", {})
            files: list[dict] = data.get("files", [])
            mp3s = [f for f in files if f.get("name", "").lower().endswith(".mp3")]
            return {
                "identifier": identifier,
                "title": meta.get("title", identifier),
                "creator": meta.get("creator", ""),
                "year": meta.get("year", ""),
                "tracks": [
                    {
                        "name": f["name"],
                        "url": f"{BASE}/download/{identifier}/{f['name']}",
                    }
                    for f in mp3s
                ],
            }
        except Exception as exc:
            logger.error("Archive.org get_item failed (%s): %s", identifier, exc)
            return None
