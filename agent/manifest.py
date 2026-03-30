"""
agent/manifest.py — SHA256 manifest creation and verification for core files
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CORE_FILES_GLOB = "/opt/selena-core/core/**/*.py"
MANIFEST_PATH = "/secure/core.manifest"
MASTER_HASH_PATH = "/secure/master.hash"
BACKUP_DIR = "/secure/core_backup/v0.3.0/"


def sha256_file(path: str | Path) -> str:
    """Compute SHA256 hash of a file."""
    h = hashlib.sha256()
    p = Path(path)
    if not p.exists():
        return ""
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_string(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def create_manifest() -> dict[str, str]:
    """
    Scan all core .py files, compute SHA256 hashes, write manifest + master hash.
    Called once at first init.
    """
    manifest: dict[str, str] = {}
    files = sorted(glob.glob(CORE_FILES_GLOB, recursive=True))
    if not files:
        logger.warning("No core files found matching: %s", CORE_FILES_GLOB)

    for filepath in files:
        manifest[filepath] = sha256_file(filepath)

    # Ensure /secure dir exists
    manifest_path = Path(MANIFEST_PATH)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_json = json.dumps(manifest, sort_keys=True, indent=2)
    manifest_path.write_text(manifest_json)

    # Write master hash of the manifest itself
    master_hash = sha256_string(manifest_json)
    Path(MASTER_HASH_PATH).write_text(master_hash)

    logger.info(
        "Manifest created: %d files, master_hash=%s", len(manifest), master_hash[:16]
    )
    return manifest


def load_manifest() -> dict[str, str]:
    """Load manifest from disk."""
    p = Path(MANIFEST_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST_PATH}")
    return json.loads(p.read_text())


def verify_manifest_hash() -> bool:
    """Verify that manifest file itself has not been tampered with."""
    manifest_path = Path(MANIFEST_PATH)
    master_hash_path = Path(MASTER_HASH_PATH)
    if not manifest_path.exists() or not master_hash_path.exists():
        return False
    stored_hash = master_hash_path.read_text().strip()
    actual_hash = sha256_string(manifest_path.read_text())
    return actual_hash == stored_hash


def check_files(manifest: dict[str, str]) -> list[dict[str, str]]:
    """Check all files against manifest. Returns list of changed files."""
    changed: list[dict[str, str]] = []
    for filepath, expected_hash in manifest.items():
        actual = sha256_file(filepath)
        if actual != expected_hash:
            changed.append({
                "path": filepath,
                "expected": expected_hash,
                "actual": actual,
            })
    return changed
