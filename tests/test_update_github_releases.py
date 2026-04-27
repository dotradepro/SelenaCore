"""tests/test_update_github_releases.py — GithubReleasesSource client tests."""
from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _api_release(
    tag: str = "v0.4.150-rc",
    *,
    prerelease: bool = True,
    include_meta: bool = False,
    missing: tuple[str, ...] = (),
) -> dict:
    """Build a minimal GitHub Releases API entry."""
    name_tar = f"selenacore-{tag}.tar.gz"
    name_sha = f"selenacore-{tag}.tar.gz.sha256"
    name_meta = f"selenacore-{tag}.meta.json"
    assets = []
    if name_tar not in missing:
        assets.append(
            {
                "name": name_tar,
                "browser_download_url": f"https://example.test/{name_tar}",
                "size": 12345,
            }
        )
    if name_sha not in missing:
        assets.append(
            {
                "name": name_sha,
                "browser_download_url": f"https://example.test/{name_sha}",
            }
        )
    if include_meta and name_meta not in missing:
        assets.append(
            {
                "name": name_meta,
                "browser_download_url": f"https://example.test/{name_meta}",
            }
        )
    return {
        "tag_name": tag,
        "name": f"Selena {tag}",
        "body": "release notes",
        "published_at": "2026-04-25T10:00:00Z",
        "prerelease": prerelease,
        "assets": assets,
    }


# ── Parsing & filtering ─────────────────────────────────────────────────────


class TestParseRelease:
    def test_valid_release_parsed(self):
        from system_modules.update_manager.sources.github_releases import _parse_release

        r = _parse_release(_api_release("v0.4.150-rc"))
        assert r is not None
        assert r.tag == "v0.4.150-rc"
        assert r.version == "0.4.150-rc"
        assert r.size_bytes == 12345
        assert r.sha256_url.endswith(".sha256")
        assert r.meta_url is None

    def test_release_with_meta_asset(self):
        from system_modules.update_manager.sources.github_releases import _parse_release

        r = _parse_release(_api_release("v0.4.150-rc", include_meta=True))
        assert r is not None and r.meta_url and r.meta_url.endswith(".meta.json")

    def test_release_missing_tarball_skipped(self):
        from system_modules.update_manager.sources.github_releases import _parse_release

        api = _api_release("v0.4.150-rc", missing=("selenacore-v0.4.150-rc.tar.gz",))
        assert _parse_release(api) is None

    def test_release_missing_sha_skipped(self):
        from system_modules.update_manager.sources.github_releases import _parse_release

        api = _api_release("v0.4.150-rc", missing=("selenacore-v0.4.150-rc.tar.gz.sha256",))
        assert _parse_release(api) is None


class TestChannelFilter:
    def test_stable_channel_excludes_prerelease(self):
        from system_modules.update_manager.sources.github_releases import (
            _filter_channel,
            _parse_release,
        )

        rc = _parse_release(_api_release("v0.5.0-rc", prerelease=True))
        stable = _parse_release(_api_release("v0.4.0", prerelease=False))
        out = _filter_channel([rc, stable], "stable")
        assert len(out) == 1 and out[0].tag == "v0.4.0"

    def test_rc_channel_includes_both(self):
        from system_modules.update_manager.sources.github_releases import (
            _filter_channel,
            _parse_release,
        )

        rc = _parse_release(_api_release("v0.5.0-rc", prerelease=True))
        stable = _parse_release(_api_release("v0.4.0", prerelease=False))
        out = _filter_channel([rc, stable], "rc")
        assert len(out) == 2


# ── fetch_releases (200 + 304 + cache) ─────────────────────────────────────


class TestFetchReleases:
    @pytest.mark.asyncio
    async def test_fetch_200_writes_cache_and_etag(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        src = GithubReleasesSource(repo="x/y", cache_dir=tmp_path)
        api_data = [_api_release("v0.4.150-rc")]

        req = httpx.Request("GET", "https://api.github.com/repos/x/y/releases")
        resp = httpx.Response(200, json=api_data, request=req, headers={"etag": '"abc"'})

        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            inst.get.return_value = resp
            releases = await src.fetch_releases(channel="rc")

        assert len(releases) == 1
        assert (tmp_path / "releases.etag").read_text() == '"abc"'
        cached = json.loads((tmp_path / "releases.json").read_text())
        assert cached[0]["tag_name"] == "v0.4.150-rc"

    @pytest.mark.asyncio
    async def test_fetch_304_uses_cache(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        # Pre-seed cache + etag
        (tmp_path / "releases.etag").write_text('"cached"')
        (tmp_path / "releases.json").write_text(json.dumps([_api_release("v0.3.0")]))

        src = GithubReleasesSource(repo="x/y", cache_dir=tmp_path)
        req = httpx.Request("GET", "https://api.github.com/repos/x/y/releases")
        resp = httpx.Response(304, request=req)

        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            inst.get.return_value = resp
            releases = await src.fetch_releases(channel="rc")

        assert len(releases) == 1
        assert releases[0].tag == "v0.3.0"

    @pytest.mark.asyncio
    async def test_fetch_network_error_falls_back_to_cache(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        (tmp_path / "releases.json").write_text(json.dumps([_api_release("v0.3.0")]))
        src = GithubReleasesSource(repo="x/y", cache_dir=tmp_path)

        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            inst.get.side_effect = httpx.ConnectError("offline")
            releases = await src.fetch_releases(channel="rc")

        assert len(releases) == 1


# ── verify_sha256 ───────────────────────────────────────────────────────────


class TestVerifySha256:
    @pytest.mark.asyncio
    async def test_match(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        payload = b"hello"
        digest = hashlib.sha256(payload).hexdigest()
        f = tmp_path / "x.tar.gz"
        f.write_bytes(payload)

        src = GithubReleasesSource(repo="x/y", cache_dir=tmp_path / "cache")
        req = httpx.Request("GET", "https://example.test/x.tar.gz.sha256")
        resp = httpx.Response(
            200, text=f"{digest}  selenacore-vTest.tar.gz\n", request=req
        )
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            inst.get.return_value = resp
            actual = await src.verify_sha256(f, "https://example.test/x.tar.gz.sha256")

        assert actual == digest

    @pytest.mark.asyncio
    async def test_mismatch_raises(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        f = tmp_path / "x.tar.gz"
        f.write_bytes(b"actual")

        src = GithubReleasesSource(repo="x/y", cache_dir=tmp_path / "cache")
        wrong = "0" * 64
        req = httpx.Request("GET", "https://example.test/x.tar.gz.sha256")
        resp = httpx.Response(200, text=f"{wrong}  x.tar.gz", request=req)
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            inst.get.return_value = resp
            with pytest.raises(ValueError, match="sha256 mismatch"):
                await src.verify_sha256(f, "https://example.test/x.tar.gz.sha256")

    @pytest.mark.asyncio
    async def test_malformed_sha_file(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        src = GithubReleasesSource(repo="x/y", cache_dir=tmp_path / "cache")
        req = httpx.Request("GET", "https://example.test/x.sha256")
        resp = httpx.Response(200, text="not-a-hex", request=req)
        f = tmp_path / "x.tar.gz"
        f.write_bytes(b"")
        with patch("httpx.AsyncClient") as mc:
            inst = AsyncMock()
            mc.return_value.__aenter__.return_value = inst
            inst.get.return_value = resp
            with pytest.raises(ValueError, match="unexpected format"):
                await src.verify_sha256(f, "https://example.test/x.sha256")


# ── extract --strip-components=1 ────────────────────────────────────────────


class TestExtract:
    def test_strip_leading_dir(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        src_dir = tmp_path / "selenacore-vTest"
        (src_dir / "core").mkdir(parents=True)
        (src_dir / "core" / "main.py").write_text("# ok")
        (src_dir / "README.md").write_text("hi")

        tar_path = tmp_path / "x.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(src_dir, arcname=src_dir.name)

        staging = tmp_path / "staging"
        GithubReleasesSource.extract(tar_path, staging)
        # Leading "selenacore-vTest/" stripped — files at staging root.
        assert (staging / "core" / "main.py").read_text() == "# ok"
        assert (staging / "README.md").read_text() == "hi"

    def test_unsafe_member_skipped(self, tmp_path):
        from system_modules.update_manager.sources.github_releases import (
            GithubReleasesSource,
        )

        # Build a tar with a path-traversal entry.
        tar_path = tmp_path / "evil.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            inner = tmp_path / "inner.txt"
            inner.write_text("ok")
            ti = tf.gettarinfo(inner, arcname="selenacore/../etc/passwd")
            tf.addfile(ti, open(inner, "rb"))

        staging = tmp_path / "staging"
        GithubReleasesSource.extract(tar_path, staging)
        # No file outside staging
        assert not (tmp_path / "etc" / "passwd").exists()
