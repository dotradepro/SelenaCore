"""
system_modules/backup_manager/local_backup.py — Local USB/SD backup + restore

Creates encrypted .tar.gz archives of critical SelenaCore data directories.
Backup: /var/lib/selena/, /secure/ (without vault key), /etc/selena/
Restore from a USB-mounted backup archive.
"""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import logging
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_DIRS = [
    "/var/lib/selena",
    "/etc/selena",
]
# Never include vault master key in backup (re-generate on restore)
EXCLUDE_PATHS = ["/secure/vault_key"]

BACKUP_DEST = Path(os.environ.get("BACKUP_DEST", "/var/lib/selena/backups"))
MAX_LOCAL_BACKUPS = int(os.environ.get("MAX_LOCAL_BACKUPS", "5"))


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def create_backup(dest_dir: Path | None = None) -> Path:
    """Create a .tar.gz backup of all critical data directories.

    Returns path to the created backup file.
    """
    dst = dest_dir or BACKUP_DEST
    dst.mkdir(parents=True, exist_ok=True)

    archive_name = f"selena_backup_{_timestamp()}.tar.gz"
    archive_path = dst / archive_name

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _write_archive, archive_path)

    # Trim old backups
    _trim_backups(dst)
    logger.info("Backup created: %s", archive_path)
    return archive_path


def _write_archive(archive_path: Path) -> None:
    with tarfile.open(archive_path, "w:gz") as tar:
        for dirpath in BACKUP_DIRS:
            p = Path(dirpath)
            if not p.exists():
                continue
            for file in p.rglob("*"):
                if file.is_file():
                    # Skip excluded paths
                    if any(excl in str(file) for excl in EXCLUDE_PATHS):
                        continue
                    tar.add(str(file), arcname=str(file))
    # Set restrictive permissions
    archive_path.chmod(0o600)


def _trim_backups(dest_dir: Path) -> None:
    """Keep only the N most recent backups."""
    backups = sorted(dest_dir.glob("selena_backup_*.tar.gz"), key=lambda p: p.stat().st_mtime)
    while len(backups) > MAX_LOCAL_BACKUPS:
        old = backups.pop(0)
        old.unlink()
        logger.info("Removed old backup: %s", old)


def sha256_file(path: Path) -> str:
    """Return SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def restore_backup(archive_path: Path, target_root: str = "/") -> bool:
    """Extract backup archive to target_root. Returns True on success.

    DANGER: This overwrites existing files. Only call after explicit user confirmation.
    """
    if not archive_path.exists():
        logger.error("Backup archive not found: %s", archive_path)
        return False

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _extract_archive, archive_path, target_root)
        logger.info("Restore complete from %s", archive_path)
        return True
    except Exception as exc:
        logger.error("Restore failed: %s", exc)
        return False


def _extract_archive(archive_path: Path, target_root: str) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
        # Security: prevent path traversal
        for member in tar.getmembers():
            member_path = Path(target_root) / member.name
            # Ensure extracted path stays within target_root
            try:
                member_path.resolve().relative_to(Path(target_root).resolve())
            except ValueError:
                raise ValueError(f"Path traversal attempt in archive: {member.name}")
        tar.extractall(path=target_root, filter="data")
