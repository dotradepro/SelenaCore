"""GitHub Releases client for SelenaCore update_manager.

Fetches release list from `https://api.github.com/repos/<owner>/<repo>/releases`,
verifies SHA256 against a release-attached `.sha256` asset, and downloads the
release tarball to a staging directory.

Three release assets are expected:
  - selenacore-<tag>.tar.gz       (mandatory) — pre-built artifact
  - selenacore-<tag>.tar.gz.sha256 (mandatory) — text file: "<hash>  filename"
  - selenacore-<tag>.meta.json     (optional)  — { min_python, ... }

Releases missing the mandatory pair are skipped with a warning.

Source-archive URLs (`/archive/refs/tags/<tag>.tar.gz`) are NOT used: their
SHA is unstable across GitHub backend changes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
DEFAULT_HEADERS = {"Accept": "application/vnd.github.v3+json"}


@dataclass
class Release:
    tag: str
    version: str
    name: str
    body: str
    published_at: str
    prerelease: bool
    tarball_url: str
    sha256_url: str
    meta_url: str | None
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _filter_channel(releases: list[Release], channel: str) -> list[Release]:
    """rc channel sees both rc and stable; stable sees only stable."""
    if channel == "stable":
        return [r for r in releases if not r.prerelease]
    return list(releases)


def _parse_release(item: dict[str, Any]) -> Release | None:
    tag = item.get("tag_name") or ""
    if not tag:
        return None

    assets = {a.get("name", ""): a for a in item.get("assets", [])}

    tar_name = f"selenacore-{tag}.tar.gz"
    sha_name = f"selenacore-{tag}.tar.gz.sha256"
    meta_name = f"selenacore-{tag}.meta.json"

    tar_asset = assets.get(tar_name)
    sha_asset = assets.get(sha_name)
    if not tar_asset or not sha_asset:
        logger.warning(
            "Release %s skipped: missing %s and/or %s asset",
            tag,
            tar_name,
            sha_name,
        )
        return None

    meta_asset = assets.get(meta_name)

    return Release(
        tag=tag,
        version=tag.lstrip("v"),
        name=item.get("name") or tag,
        body=item.get("body") or "",
        published_at=item.get("published_at") or "",
        prerelease=bool(item.get("prerelease")),
        tarball_url=tar_asset["browser_download_url"],
        sha256_url=sha_asset["browser_download_url"],
        meta_url=meta_asset["browser_download_url"] if meta_asset else None,
        size_bytes=int(tar_asset.get("size") or 0),
    )


class GithubReleasesSource:
    """GitHub Releases-backed source for update_manager.

    Caches the last successful response in ``cache_dir`` and uses ETag /
    If-None-Match for conditional GETs to avoid burning rate limits.
    """

    def __init__(
        self,
        repo: str,
        cache_dir: str | Path = "/var/lib/selena/update/cache",
        timeout: float = 15.0,
    ) -> None:
        self._repo = repo
        self._cache_dir = Path(cache_dir)
        self._etag_file = self._cache_dir / "releases.etag"
        self._cache_file = self._cache_dir / "releases.json"
        self._timeout = timeout

    @property
    def repo(self) -> str:
        return self._repo

    async def fetch_releases(
        self, channel: str = "rc", per_page: int = 30
    ) -> list[Release]:
        """Fetch and parse releases for the configured repo.

        Returns releases ordered as GitHub returns them (newest first).
        """
        url = f"{GITHUB_API}/repos/{self._repo}/releases?per_page={per_page}"
        headers = dict(DEFAULT_HEADERS)
        etag = self._read_etag()
        if etag:
            headers["If-None-Match"] = etag

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("GitHub releases fetch failed: %s", exc)
            return self._parse_cached(channel)

        if resp.status_code == 304:
            return self._parse_cached(channel)

        if resp.status_code != 200:
            logger.warning(
                "GitHub releases returned %d: %s",
                resp.status_code,
                resp.text[:200],
            )
            return self._parse_cached(channel)

        try:
            data = resp.json()
        except ValueError as exc:
            logger.error("GitHub releases body is not JSON: %s", exc)
            return self._parse_cached(channel)

        self._write_cache(resp.headers.get("etag", ""), data)

        releases: list[Release] = []
        for item in data:
            r = _parse_release(item)
            if r is not None:
                releases.append(r)

        return _filter_channel(releases, channel)

    async def fetch_meta(self, release: Release) -> dict[str, Any]:
        """Fetch and parse the ``selenacore-<tag>.meta.json`` asset.

        Returns sensible defaults when the asset is missing or invalid so the
        installer can apply a precheck without a special-case branch.
        """
        defaults = {
            "min_python": "3.11",
            "min_core_version_for_upgrade": "0.3.0",
            "needs_db_migration": False,
            "needs_frontend_rebuild": False,
        }
        if not release.meta_url:
            return defaults
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                resp = await client.get(release.meta_url)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("meta.json fetch failed for %s: %s", release.tag, exc)
            return defaults

        if not isinstance(payload, dict):
            logger.warning("meta.json for %s is not a JSON object", release.tag)
            return defaults

        merged = dict(defaults)
        merged.update(payload)
        return merged

    async def download_tarball(
        self,
        release: Release,
        dest_path: Path,
        progress_cb: Callable[[int, int], Awaitable[None] | None] | None = None,
        chunk_size: int = 65536,
    ) -> Path:
        """Stream the tarball to ``dest_path``.

        Supports resume via Range header: writes to ``dest_path.partial``,
        and on retry continues from the current size. Returns the final path.
        """
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        partial = dest_path.with_suffix(dest_path.suffix + ".partial")

        existing = partial.stat().st_size if partial.exists() else 0
        headers: dict[str, str] = {}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
            logger.info(
                "Resuming download of %s from byte %d",
                release.tag,
                existing,
            )

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, read=300.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", release.tarball_url, headers=headers) as resp:
                if resp.status_code not in (200, 206):
                    raise RuntimeError(
                        f"tarball download failed: HTTP {resp.status_code}"
                    )
                total = int(resp.headers.get("content-length", 0)) + existing
                mode = "ab" if existing > 0 and resp.status_code == 206 else "wb"
                if mode == "wb":
                    existing = 0
                written = existing
                with open(partial, mode) as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                        fh.write(chunk)
                        written += len(chunk)
                        if progress_cb is not None:
                            r = progress_cb(written, total)
                            if hasattr(r, "__await__"):
                                await r  # type: ignore[misc]

        partial.rename(dest_path)
        logger.info("Downloaded %s -> %s (%d bytes)", release.tag, dest_path, dest_path.stat().st_size)
        return dest_path

    async def verify_sha256(self, tarball_path: Path, sha256_url: str) -> str:
        """Verify the tarball SHA256 against the published .sha256 file.

        Mandatory: a mismatch raises ValueError; the tarball is left intact
        for the caller to delete (or inspect for debugging).
        """
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                resp = await client.get(sha256_url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"sha256 fetch failed: {exc}") from exc

        # Format: "<hex>  <filename>" per `sha256sum` convention. Take the
        # first whitespace-delimited token so trailing comments / filenames
        # do not trip the comparison.
        text = resp.text.strip()
        if not text:
            raise ValueError("sha256 file is empty")
        expected = text.split()[0].lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError(f"sha256 file has unexpected format: {text!r}")

        actual = self._compute_sha256(tarball_path)
        if actual != expected:
            raise ValueError(
                f"sha256 mismatch: expected {expected}, got {actual}"
            )
        return actual

    @staticmethod
    def _compute_sha256(path: Path, chunk_size: int = 65536) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def extract(tarball_path: Path, staging_dir: Path) -> Path:
        """Extract tarball into ``staging_dir``, stripping the leading dir.

        GitHub Releases produced by `tar -czf selenacore-<tag>.tar.gz selenacore-<tag>/`
        wrap files in `selenacore-<tag>/`. This strips that single leading
        component so the staging dir layout matches `/opt/selena-core/`.
        """
        staging_dir = Path(staging_dir)
        if staging_dir.exists():
            # Cleanup leftovers from a prior failed attempt; staging is
            # ephemeral and reproducible from the tarball.
            import shutil

            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True, exist_ok=True)

        with tarfile.open(tarball_path, mode="r:gz") as tf:
            members = []
            for member in tf.getmembers():
                # Strip first path component (e.g. "selenacore-v0.4.142/").
                parts = member.name.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                # Guard against absolute or parent-traversing entries.
                stripped = parts[1]
                if stripped.startswith("/") or ".." in stripped.split("/"):
                    logger.warning("skipping unsafe tar entry: %s", member.name)
                    continue
                member.name = stripped
                members.append(member)
            tf.extractall(staging_dir, members=members)

        return staging_dir

    # ── ETag cache helpers ────────────────────────────────────────────────

    def _read_etag(self) -> str:
        try:
            return self._etag_file.read_text().strip()
        except FileNotFoundError:
            return ""
        except OSError as exc:
            logger.debug("etag read failed: %s", exc)
            return ""

    def _write_cache(self, etag: str, data: list[dict[str, Any]]) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            if etag:
                self._etag_file.write_text(etag)
            self._cache_file.write_text(json.dumps(data))
        except OSError as exc:
            logger.warning("releases cache write failed: %s", exc)

    def _parse_cached(self, channel: str) -> list[Release]:
        try:
            data = json.loads(self._cache_file.read_text())
        except (FileNotFoundError, ValueError):
            return []
        releases = [r for r in (_parse_release(item) for item in data) if r]
        return _filter_channel(releases, channel)
