"""tar.gz backup + restore. SQLite via Online Backup API for consistency.

`selena_backup_*` and `selena_prerestore_*` are two retention pools — the
caller's choice of `prefix` decides which.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .state import EXCLUDE_PATHS, load_settings, resolve_paths

logger = logging.getLogger(__name__)


REGULAR_PREFIX = "selena_backup_"
PRERESTORE_PREFIX = "selena_prerestore_"
SQLITE_PATH = Path("/var/lib/selena/selena.db")


def _get_backup_dest() -> Path:
    return Path(os.environ.get("BACKUP_DEST", "/var/lib/selena/backups"))


def _get_prerestore_retention() -> int:
    try:
        return int(os.environ.get("PRERESTORE_RETENTION", "3"))
    except ValueError:
        return 3


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


async def create_backup(
    *,
    prefix: str = REGULAR_PREFIX,
    dest_dir: Path | None = None,
    paths: list[str] | None = None,
    max_backups: int | None = None,
) -> Path:
    """Create a tar.gz backup. Returns absolute path of created archive.

    `paths` defaults to the categories saved in settings.json. `prefix`
    selects which retention pool the new archive joins.
    """
    dst = dest_dir or _get_backup_dest()
    dst.mkdir(parents=True, exist_ok=True)

    if paths is None:
        settings = load_settings()
        paths = resolve_paths(settings["categories"])
        if max_backups is None:
            max_backups = settings["max_backups"]

    archive_name = f"{prefix}{_timestamp()}.tar.gz"
    archive_path = dst / archive_name

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write_archive, archive_path, list(paths))

    retention = (
        _get_prerestore_retention()
        if prefix == PRERESTORE_PREFIX
        else (max_backups or 5)
    )
    _trim_backups(dst, prefix=prefix, keep=retention)
    logger.info("Backup created: %s", archive_path)
    return archive_path


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _write_archive(archive_path: Path, paths: list[str]) -> None:
    sqlite_target_in_paths = any(
        Path(p) == SQLITE_PATH or _is_under(SQLITE_PATH, Path(p))
        for p in paths
    )

    tmp_db: Path | None = None
    if sqlite_target_in_paths and SQLITE_PATH.exists():
        tmp_db = _snapshot_sqlite(SQLITE_PATH)

    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            for raw in paths:
                root = Path(raw)
                if not root.exists():
                    continue
                if root.is_file():
                    if root == SQLITE_PATH and tmp_db is not None:
                        tar.add(str(tmp_db), arcname=str(root))
                    elif _is_excluded(root):
                        continue
                    else:
                        tar.add(str(root), arcname=str(root))
                    continue
                for f in root.rglob("*"):
                    if not f.is_file():
                        continue
                    if _is_excluded(f):
                        continue
                    if f == SQLITE_PATH and tmp_db is not None:
                        tar.add(str(tmp_db), arcname=str(f))
                        continue
                    tar.add(str(f), arcname=str(f))
        archive_path.chmod(0o600)
    finally:
        if tmp_db is not None:
            shutil.rmtree(tmp_db.parent, ignore_errors=True)


def _is_excluded(path: Path) -> bool:
    s = str(path)
    return any(s == ex or s.startswith(ex + os.sep) for ex in EXCLUDE_PATHS)


def _snapshot_sqlite(db_path: Path) -> Path:
    """Use SQLite Online Backup API to copy db_path → tempfile.

    Returns the tempfile path. Caller must remove its parent dir afterwards.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="selena_backup_db_"))
    target = tmp_dir / db_path.name
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return target


def _trim_backups(dest_dir: Path, *, prefix: str, keep: int) -> None:
    """Keep only the N most recent backups matching prefix."""
    backups = sorted(
        dest_dir.glob(f"{prefix}*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
    )
    while len(backups) > keep:
        old = backups.pop(0)
        try:
            old.unlink()
        except OSError as exc:
            logger.warning("Failed to remove old backup %s: %s", old, exc)
            continue
        logger.info("Removed old backup: %s", old)


def list_backups(dest_dir: Path | None = None) -> list[dict]:
    """Return metadata for all backups in dest_dir (regular + prerestore)."""
    dst = dest_dir or _get_backup_dest()
    if not dst.exists():
        return []
    rows: list[dict] = []
    for f in dst.glob("*.tar.gz"):
        kind = "prerestore" if f.name.startswith(PRERESTORE_PREFIX) else "regular"
        st = f.stat()
        rows.append({
            "name": f.name,
            "size_bytes": st.st_size,
            "size_mb": round(st.st_size / 1e6, 2),
            "created": st.st_mtime,
            "kind": kind,
        })
    rows.sort(key=lambda r: r["created"], reverse=True)
    return rows


def sha256_file(path: Path) -> str:
    """Return SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def restore_backup(archive_path: Path, target_root: str = "/") -> bool:
    """Extract backup archive to target_root. Returns True on success.

    DANGER: This overwrites existing files. Only call after explicit user
    confirmation. The caller (module.py) is responsible for taking a
    pre-restore snapshot first.
    """
    if not archive_path.exists():
        logger.error("Backup archive not found: %s", archive_path)
        return False

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _extract_archive, archive_path, target_root)
        logger.info("Restore complete from %s", archive_path)
        return True
    except Exception as exc:
        logger.error("Restore failed: %s", exc)
        return False


def _extract_archive(archive_path: Path, target_root: str) -> None:
    with tarfile.open(archive_path, "r:gz") as tar:
        target_resolved = Path(target_root).resolve()
        for member in tar.getmembers():
            member_path = (Path(target_root) / member.name).resolve()
            try:
                member_path.relative_to(target_resolved)
            except ValueError as exc:
                raise ValueError(
                    f"Path traversal attempt in archive: {member.name}"
                ) from exc
        tar.extractall(path=target_root, filter="data")
