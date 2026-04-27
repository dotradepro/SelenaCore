"""system_modules/update_manager/updater.py — UpdateManager core logic.

Source of truth: GitHub Releases API
(``https://api.github.com/repos/<owner>/<repo>/releases``).

Lifecycle::

    idle  →  checking → up_to_date | update_available
                                       └─ install_version(tag)
                                              ↓
                          downloading → downloaded
                                              ↓
                                      applying  (handed off to systemd unit)
                                              ↓
                          (smarthome-core SIGTERM, then restart on new ver.)

The heavy lifting (rsync, manifest rebaseline, restart) lives in
``scripts/apply-update.sh``. This module only:

  1. fetches the release list,
  2. downloads + verifies the tarball,
  3. extracts staging,
  4. dispatches the external script via ``systemd-run``.

Self-update inside the smarthome-core process is not possible: the systemd
unit runs under ``ReadOnlyPaths=/opt/selena-core /secure``, and
``Restart=always`` would race with any rsync attempt. See
``docs/TZ_system_modules.md §9`` and the project plan for details.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from dataclasses import asdict
from enum import Enum
from pathlib import Path
from typing import Any

from system_modules.update_manager.installer import (
    INSTALL_LOCK_PATH,
    acquire_install_lock,
    dispatch_external,
    last_apply_result,
    precheck,
    release_install_lock,
)
from system_modules.update_manager.sources.github_releases import (
    GithubReleasesSource,
    Release,
)

logger = logging.getLogger(__name__)


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    UPDATE_AVAILABLE = "update_available"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    APPLYING = "applying"
    APPLIED = "applied"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    ERROR = "error"


VALID_CHANNELS = ("rc", "stable")


class UpdateManager:
    """In-process orchestrator for SelenaCore self-update.

    Reads channel and other runtime knobs from a state file
    (``/var/lib/selena/update_manager.state.json``) so the user can flip
    channel from the UI without round-tripping ``core.yaml`` (which would
    require a comment-preserving YAML dumper).
    """

    def __init__(
        self,
        publish_event_cb: Any,
        *,
        current_version: str = "0.1.0",
        repo: str = "dotradepro/SelenaCore",
        channel: str = "rc",
        install_dir: str | Path = "/opt/selena-core",
        backup_dir: str | Path = "/opt/selena-backup",
        staging_dir: str | Path = "/var/lib/selena/update/staging",
        cache_dir: str | Path = "/var/lib/selena/update/cache",
        state_file: str | Path = "/var/lib/selena/update_manager.state.json",
        check_interval_sec: int = 21600,
        auto_check: bool = False,
        backups_keep: int = 3,
    ) -> None:
        self._publish = publish_event_cb
        self._current_version = current_version
        self._repo = repo
        self._default_channel = channel if channel in VALID_CHANNELS else "rc"
        self._install_dir = Path(install_dir)
        self._backup_dir = Path(backup_dir)
        self._staging_dir = Path(staging_dir)
        self._cache_dir = Path(cache_dir)
        self._state_file = Path(state_file)
        self._check_interval = check_interval_sec
        self._auto_check = auto_check
        self._backups_keep = backups_keep

        self.state: UpdateState = UpdateState.IDLE
        self._error: str | None = None
        self._releases: list[Release] = []
        self._latest: Release | None = None
        self._task: asyncio.Task | None = None
        self._install_lock = asyncio.Lock()

        self._source = GithubReleasesSource(repo=self._repo, cache_dir=self._cache_dir)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def current_version(self) -> str:
        return self._current_version

    @property
    def latest_version(self) -> str | None:
        return self._latest.version if self._latest else None

    @property
    def channel(self) -> str:
        return self._read_state().get("channel", self._default_channel)

    def get_status(self) -> dict[str, Any]:
        last_backup = self._latest_backup_path()
        return {
            "state": self.state.value,
            "current_version": self._current_version,
            "latest_version": self.latest_version,
            "update_available": self.state == UpdateState.UPDATE_AVAILABLE,
            "channel": self.channel,
            "auto_check": self._auto_check,
            "check_interval_sec": self._check_interval,
            "repo": self._repo,
            "error": self._error,
            "has_backup": last_backup is not None,
            "last_backup": str(last_backup) if last_backup else None,
            "install_lock_held": INSTALL_LOCK_PATH.exists(),
        }

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> dict[str, Any]:
        """Fetch the release list and pick the newest one for the channel."""
        self.state = UpdateState.CHECKING
        self._error = None
        try:
            releases = await self._source.fetch_releases(channel=self.channel)
        except Exception as exc:
            self._error = str(exc)
            self.state = UpdateState.ERROR
            logger.error("release fetch failed: %s", exc, exc_info=True)
            raise

        self._releases = releases

        if not releases:
            self.state = UpdateState.UP_TO_DATE
            return {"update_available": False, "reason": "no releases found"}

        latest = releases[0]
        self._latest = latest

        if self._version_gt(latest.version, self._current_version):
            self.state = UpdateState.UPDATE_AVAILABLE
            await self._publish(
                "update.available",
                {
                    "current_version": self._current_version,
                    "latest_version": latest.version,
                    "tag": latest.tag,
                    "published_at": latest.published_at,
                    "notes": latest.body[:500],
                },
            )
            return {"update_available": True, "version": latest.version, "tag": latest.tag}

        self.state = UpdateState.UP_TO_DATE
        return {"update_available": False, "version": latest.version}

    # ── Listing ───────────────────────────────────────────────────────────────

    def list_versions(self) -> list[dict[str, Any]]:
        """Return cached release list as dicts for UI consumption."""
        return [r.to_dict() for r in self._releases]

    def get_version_details(self, tag: str) -> dict[str, Any] | None:
        for r in self._releases:
            if r.tag == tag or r.version == tag.lstrip("v"):
                return r.to_dict()
        return None

    # ── Install ───────────────────────────────────────────────────────────────

    async def install_version(self, tag: str) -> dict[str, Any]:
        """Download + verify + extract + dispatch external installer.

        Returns once the systemd unit has been queued — does NOT wait for the
        unit to finish, because by then smarthome-core will have been stopped
        by the script and this code path is no longer running.
        """
        async with self._install_lock:
            release = self._find_release(tag)
            if release is None:
                raise ValueError(f"unknown release tag: {tag}")

            meta = await self._source.fetch_meta(release)
            ok, reason = precheck(
                release.size_bytes,
                self._install_dir,
                meta,
                staging_root=self._staging_dir,
            )
            if not ok:
                self._error = reason
                self.state = UpdateState.ERROR
                await self._publish("update.failed", {"tag": tag, "reason": reason})
                raise RuntimeError(reason)

            if not acquire_install_lock(tag):
                msg = "install lock held by another process"
                self._error = msg
                self.state = UpdateState.ERROR
                raise RuntimeError(msg)

            try:
                self.state = UpdateState.DOWNLOADING
                await self._publish(
                    "update.downloading",
                    {"tag": tag, "version": release.version},
                )

                tmp_root = self._cache_dir / "downloads"
                tmp_root.mkdir(parents=True, exist_ok=True)
                tarball_path = tmp_root / f"selenacore-{tag}.tar.gz"

                await self._source.download_tarball(release, tarball_path)

                # Mandatory SHA256 — if it fails, the tarball stays for
                # operator inspection; staging is never touched.
                try:
                    await self._source.verify_sha256(tarball_path, release.sha256_url)
                except Exception as exc:
                    self.state = UpdateState.ERROR
                    self._error = str(exc)
                    await self._publish(
                        "update.failed",
                        {"tag": tag, "reason": "sha256_mismatch", "detail": str(exc)},
                    )
                    raise

                self.state = UpdateState.DOWNLOADED

                staging_for_tag = self._staging_dir / tag
                self._source.extract(tarball_path, staging_for_tag)

                self.state = UpdateState.APPLYING
                await self._publish(
                    "update.applying",
                    {"tag": tag, "version": release.version},
                )

                # Hand off to the external script. From this point smarthome-
                # core is racing against `systemctl stop smarthome-core` from
                # the new transient unit; we publish and return cleanly so
                # systemd has a clean process to terminate.
                unit = dispatch_external(tag, action="install")

                return {
                    "ok": True,
                    "tag": tag,
                    "version": release.version,
                    "unit": unit,
                }
            except Exception:
                # Lock is intentionally NOT released on success: the external
                # script will run while we're being stopped, and the lock
                # signals "install in flight" to anyone polling. The script
                # cleans up via UPDATE_FLAG handling; the lock file lingers
                # until the next core boot's start() does a stale check.
                release_install_lock()
                raise

    async def rollback(self) -> dict[str, Any]:
        """Dispatch the apply-update.sh in rollback mode.

        Picks the most recent backup; ``apply-update.sh`` exits 2 if none
        exist, which surfaces here as a CalledProcessError.
        """
        if not acquire_install_lock("rollback"):
            raise RuntimeError("install lock held by another process")
        try:
            self.state = UpdateState.ROLLING_BACK
            await self._publish("update.rolling_back", {})
            unit = dispatch_external(self._current_version or "rollback", action="rollback")
            return {"ok": True, "unit": unit}
        except Exception:
            release_install_lock()
            raise

    # ── Cloud-triggered (UPDATE_CORE) ─────────────────────────────────────────

    async def apply_update_from_url(
        self, url: str, sha256: str, version: str = "",
    ) -> dict[str, Any]:
        """Cloud-triggered self-update: direct URL + SHA256 (no release lookup).

        Used by ``cloud_sync.commands.handle_update_core`` via the
        ``update.apply_core`` event when the platform pushes a specific
        artifact (e.g. emergency hotfix not yet on GitHub Releases).

        Goes through the same ``dispatch_external`` flow as
        :meth:`install_version` so the stop-then-rsync-then-start path is
        identical and ReadOnlyPaths-safe.
        """
        if not url or not sha256:
            raise ValueError("apply_update_from_url requires url and sha256")

        # Validate sha256 shape early so a malformed cloud payload is a
        # ValueError, not a vague "expected ... got ..." after a 200 MB
        # download.
        sha256 = sha256.strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("sha256 must be a 64-char lowercase hex string")

        tag = version or "cloud-push"
        async with self._install_lock:
            if not acquire_install_lock(tag):
                raise RuntimeError("install lock held by another process")
            try:
                self.state = UpdateState.DOWNLOADING
                await self._publish(
                    "update.downloading",
                    {"tag": tag, "version": version, "source": "cloud"},
                )

                tmp_root = self._cache_dir / "downloads"
                tmp_root.mkdir(parents=True, exist_ok=True)
                tarball_path = tmp_root / f"selenacore-{tag}.tar.gz"

                await self._stream_url_to_file(url, tarball_path)

                actual = GithubReleasesSource._compute_sha256(tarball_path)
                if actual != sha256:
                    self.state = UpdateState.ERROR
                    self._error = f"sha256 mismatch: expected {sha256}, got {actual}"
                    await self._publish(
                        "update.failed",
                        {
                            "tag": tag,
                            "reason": "sha256_mismatch",
                            "stage": "apply_update_from_url",
                            "url": url,
                        },
                    )
                    raise ValueError(self._error)

                self.state = UpdateState.DOWNLOADED

                staging_for_tag = self._staging_dir / tag
                GithubReleasesSource.extract(tarball_path, staging_for_tag)

                self.state = UpdateState.APPLYING
                await self._publish(
                    "update.applying",
                    {"tag": tag, "version": version, "source": "cloud"},
                )

                unit = dispatch_external(tag, action="install")
                return {"ok": True, "tag": tag, "unit": unit}
            except Exception as exc:
                release_install_lock()
                if self.state != UpdateState.ERROR:
                    self.state = UpdateState.ERROR
                    self._error = str(exc)
                    try:
                        await self._publish(
                            "update.failed",
                            {
                                "tag": tag,
                                "stage": "apply_update_from_url",
                                "url": url,
                                "error": str(exc),
                            },
                        )
                    except Exception:
                        pass
                raise

    @staticmethod
    async def _stream_url_to_file(url: str, dest: Path) -> None:
        """Stream a URL to disk with no SHA / signature handling."""
        import httpx

        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, read=300.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        fh.write(chunk)

    # ── Channel / config ──────────────────────────────────────────────────────

    def set_channel(self, channel: str) -> None:
        if channel not in VALID_CHANNELS:
            raise ValueError(f"invalid channel: {channel!r}; expected one of {VALID_CHANNELS}")
        state = self._read_state()
        state["channel"] = channel
        self._write_state(state)

    def set_auto_check(self, enabled: bool) -> None:
        self._auto_check = bool(enabled)
        state = self._read_state()
        state["auto_check"] = bool(enabled)
        self._write_state(state)

    def set_check_interval(self, seconds: int) -> None:
        if seconds < 60:
            raise ValueError("check_interval_sec must be >= 60")
        self._check_interval = int(seconds)
        state = self._read_state()
        state["check_interval_sec"] = int(seconds)
        self._write_state(state)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        # Apply persisted state on boot so a previously-saved channel takes
        # effect even if core.yaml still has the default.
        s = self._read_state()
        if "auto_check" in s:
            self._auto_check = bool(s["auto_check"])
        if "check_interval_sec" in s:
            try:
                self._check_interval = max(60, int(s["check_interval_sec"]))
            except (TypeError, ValueError):
                pass
        if self._auto_check:
            self._task = asyncio.get_event_loop().create_task(self._check_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _check_loop(self) -> None:
        while True:
            try:
                await self.check()
            except Exception:
                pass
            await asyncio.sleep(self._check_interval)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_release(self, tag: str) -> Release | None:
        normalized = tag.lstrip("v")
        for r in self._releases:
            if r.tag == tag or r.version == normalized:
                return r
        return None

    def _latest_backup_path(self) -> Path | None:
        if not self._backup_dir.exists():
            return None
        try:
            entries = sorted(
                (p for p in self._backup_dir.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return None
        return entries[0] if entries else None

    def get_apply_log(self, tag: str | None = None, max_lines: int = 200) -> str:
        return last_apply_result(tag=tag, max_lines=max_lines)

    def _read_state(self) -> dict[str, Any]:
        try:
            return json.loads(self._state_file.read_text())
        except (FileNotFoundError, ValueError):
            return {}
        except OSError as exc:
            logger.warning("could not read state file: %s", exc)
            return {}

    def _write_state(self, state: dict[str, Any]) -> None:
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
            tmp.write_text(json.dumps(state, sort_keys=True, indent=2))
            tmp.replace(self._state_file)
        except OSError as exc:
            logger.warning("could not persist state file: %s", exc)

    # ── Version comparison ────────────────────────────────────────────────────

    @staticmethod
    def _version_gt(v1: str, v2: str) -> bool:
        """Return True if semver-ish v1 > v2.

        Strips any leading ``v`` and compares dotted integer parts. Ignores
        pre-release / build metadata after the first ``-`` or ``+``.
        """

        def parts(v: str) -> tuple[int, ...]:
            v = v.strip().lstrip("v")
            v = re.split(r"[-+]", v, maxsplit=1)[0]
            try:
                return tuple(int(x) for x in v.split("."))
            except ValueError:
                return (0, 0, 0)

        return parts(v1) > parts(v2)
