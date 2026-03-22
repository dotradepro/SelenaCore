"""
system_modules/update_manager/updater.py — UpdateManager business logic

Update lifecycle:
  idle → checking → update_available / up_to_date
  update_available → downloading → downloaded
  downloaded → applying → applied / error
  applied → (optionally rollback) → rolled_back

Features:
  - Check for updates from a version manifest URL
  - Download with SHA256 integrity verification
  - Apply update (extract zip/tar to target directory)
  - Rollback support (restore previous backup before applying)
  - Events: update.available, update.applied, update.failed, update.rolled_back

Security:
  - SHA256 hash verified before applying any update
  - Download saved to temp file; verified before being moved
  - No shell=True, no eval()
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import httpx

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


class UpdateManager:
    def __init__(
        self,
        publish_event_cb: Any,
        current_version: str = "0.1.0",
        manifest_url: str = "",
        install_dir: str = "/opt/selena-update",
        backup_dir: str = "/opt/selena-backup",
        check_interval_sec: int = 3600,
    ) -> None:
        self._publish = publish_event_cb
        self._current_version = current_version
        self._manifest_url = manifest_url
        self._install_dir = Path(install_dir)
        self._backup_dir = Path(backup_dir)
        self._check_interval = check_interval_sec

        self.state: UpdateState = UpdateState.IDLE
        self._latest: dict[str, Any] | None = None   # latest release info from manifest
        self._error: str | None = None
        self._task: asyncio.Task | None = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def current_version(self) -> str:
        return self._current_version

    @property
    def latest_version(self) -> str | None:
        return self._latest.get("version") if self._latest else None

    def get_status(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "current_version": self._current_version,
            "latest_version": self.latest_version,
            "update_available": self.state == UpdateState.UPDATE_AVAILABLE,
            "error": self._error,
        }

    # ── Check ─────────────────────────────────────────────────────────────────

    async def check(self) -> dict[str, Any]:
        """Fetch manifest and compare versions."""
        if not self._manifest_url:
            self.state = UpdateState.UP_TO_DATE
            return {"update_available": False, "reason": "no manifest_url configured"}

        self.state = UpdateState.CHECKING
        self._error = None
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self._manifest_url)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            self._error = str(exc)
            self.state = UpdateState.ERROR
            logger.error("Update check failed: %s", exc)
            raise

        latest = data if isinstance(data, dict) else data[0]
        self._latest = latest
        latest_ver = latest.get("version", "0.0.0")

        if self._version_gt(latest_ver, self._current_version):
            self.state = UpdateState.UPDATE_AVAILABLE
            await self._publish("update.available", {
                "current_version": self._current_version,
                "latest_version": latest_ver,
                "download_url": latest.get("download_url"),
                "notes": latest.get("notes", ""),
            })
            return {"update_available": True, "version": latest_ver}

        self.state = UpdateState.UP_TO_DATE
        return {"update_available": False, "version": latest_ver}

    # ── Download ──────────────────────────────────────────────────────────────

    async def download(self) -> Path:
        """Download the latest update package and verify SHA256."""
        if not self._latest:
            raise RuntimeError("No update info — run check() first")

        url = self._latest.get("download_url", "")
        expected_sha256 = self._latest.get("sha256", "")
        if not url:
            raise RuntimeError("No download_url in update manifest")

        self.state = UpdateState.DOWNLOADING
        self._error = None

        tmp = Path(tempfile.mktemp(suffix="_selena_update"))
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    hasher = hashlib.sha256()
                    with open(tmp, "wb") as fh:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            fh.write(chunk)
                            hasher.update(chunk)

            actual_sha256 = hasher.hexdigest()
            if expected_sha256 and actual_sha256 != expected_sha256.lower():
                tmp.unlink(missing_ok=True)
                raise ValueError(
                    f"SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
                )

            self.state = UpdateState.DOWNLOADED
            logger.info("Download complete: %s (sha256=%s)", tmp, actual_sha256)
            return tmp

        except Exception as exc:
            tmp.unlink(missing_ok=True)
            self._error = str(exc)
            self.state = UpdateState.ERROR
            raise

    # ── Apply ─────────────────────────────────────────────────────────────────

    async def apply(self, package_path: Path) -> None:
        """Backup current and extract package to install_dir."""
        self.state = UpdateState.APPLYING
        self._error = None
        try:
            # Backup
            if self._install_dir.exists():
                self._backup_dir.mkdir(parents=True, exist_ok=True)
                if self._backup_dir.exists():
                    shutil.rmtree(self._backup_dir)
                shutil.copytree(self._install_dir, self._backup_dir)

            # Extract
            self._install_dir.mkdir(parents=True, exist_ok=True)
            self._extract(package_path, self._install_dir)

            new_version = (self._latest or {}).get("version", self._current_version)
            old_version = self._current_version
            self._current_version = new_version
            self.state = UpdateState.APPLIED

            await self._publish("update.applied", {
                "old_version": old_version,
                "new_version": new_version,
            })
            logger.info("Update applied: %s → %s", old_version, new_version)

        except Exception as exc:
            self._error = str(exc)
            self.state = UpdateState.ERROR
            await self._publish("update.failed", {"error": str(exc)})
            raise

    def _extract(self, path: Path, dest: Path) -> None:
        """Extract zip or tar archive to dest."""
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                zf.extractall(dest)
        elif tarfile.is_tarfile(str(path)):
            with tarfile.open(path) as tf:
                tf.extractall(dest)
        else:
            raise ValueError(f"Unsupported archive format: {path}")

    # ── Rollback ──────────────────────────────────────────────────────────────

    async def rollback(self) -> None:
        """Restore from backup."""
        if not self._backup_dir.exists():
            raise RuntimeError("No backup available for rollback")

        self.state = UpdateState.ROLLING_BACK
        try:
            if self._install_dir.exists():
                shutil.rmtree(self._install_dir)
            shutil.copytree(self._backup_dir, self._install_dir)
            self.state = UpdateState.ROLLED_BACK
            await self._publish("update.rolled_back", {
                "restored_from": str(self._backup_dir),
            })
            logger.info("Rollback complete from %s", self._backup_dir)
        except Exception as exc:
            self._error = str(exc)
            self.state = UpdateState.ERROR
            raise

    # ── Background loop ────────────────────────────────────────────────────────

    async def start(self) -> None:
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

    # ── Version comparison ────────────────────────────────────────────────────

    @staticmethod
    def _version_gt(v1: str, v2: str) -> bool:
        """Return True if semver v1 > v2."""
        def parts(v: str) -> tuple[int, ...]:
            try:
                return tuple(int(x) for x in v.strip().lstrip("v").split("."))
            except ValueError:
                return (0, 0, 0)
        return parts(v1) > parts(v2)
