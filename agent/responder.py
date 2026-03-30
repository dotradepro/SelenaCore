"""
agent/responder.py — integrity violation response chain
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx

from agent.manifest import BACKUP_DIR, MANIFEST_PATH, create_manifest, sha256_file

logger = logging.getLogger(__name__)

CORE_API_URL = "http://localhost:7070/api/v1"
PLATFORM_API_URL = ""  # loaded from env


def _load_env() -> None:
    global PLATFORM_API_URL
    import os
    PLATFORM_API_URL = os.environ.get("PLATFORM_API_URL", "")


async def stop_all_modules() -> None:
    """Stop all running modules via Core API."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{CORE_API_URL}/modules")
            if resp.status_code != 200:
                return
            modules = resp.json().get("modules", [])
            for module in modules:
                if module.get("status") == "RUNNING":
                    name = module.get("name")
                    await client.post(f"{CORE_API_URL}/modules/{name}/stop")
                    logger.info("Stopped module: %s", name)
    except Exception as e:
        logger.error("Failed to stop modules: %s", e)


async def notify_platform(reason: str, changed: list[dict]) -> None:
    """Notify SmartHome LK platform about integrity violation."""
    if not PLATFORM_API_URL:
        logger.warning("PLATFORM_API_URL not configured — skipping notification")
        return
    _load_env()
    payload = {
        "event": "integrity_violation",
        "reason": reason,
        "changed_files": changed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for attempt in range(1, 6):
        try:
            key_path = Path("/secure/platform.key")
            api_key = key_path.read_text().strip() if key_path.exists() else ""
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{PLATFORM_API_URL}/device/integrity",
                    json=payload,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code < 500:
                    logger.info("Platform notified (violation), status=%s", resp.status_code)
                    return
        except Exception as e:
            logger.warning("Platform notify attempt %d failed: %s", attempt, e)
        await asyncio.sleep(2**attempt)
    logger.error("Could not notify platform after 5 attempts")


async def notify_platform_restored() -> None:
    if not PLATFORM_API_URL:
        return
    try:
        key_path = Path("/secure/platform.key")
        api_key = key_path.read_text().strip() if key_path.exists() else ""
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{PLATFORM_API_URL}/device/integrity",
                json={
                    "event": "integrity_restored",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        logger.warning("Failed to notify platform of restore: %s", e)


async def restore_from_backup(changed: list[dict]) -> bool:
    """Restore changed files from backup directory."""
    backup_path = Path(BACKUP_DIR)
    if not backup_path.exists():
        logger.error("Backup directory not found: %s", BACKUP_DIR)
        return False

    try:
        for entry in changed:
            src_rel = entry["path"].lstrip("/")
            backup_file = backup_path / src_rel
            if not backup_file.exists():
                logger.error("Backup file not found: %s", backup_file)
                return False
            dest = Path(entry["path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(backup_file), str(dest))
            logger.info("Restored: %s", dest)
        return True
    except Exception as e:
        logger.error("Restore failed: %s", e, exc_info=True)
        return False


async def restart_core() -> None:
    """Restart the core service via systemctl."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "restart", "smarthome-core",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        logger.info("Core restart requested via systemctl")
    except Exception as e:
        logger.error("Failed to restart core: %s", e)


async def enter_safe_mode() -> None:
    """Signal core API to enter SAFE MODE."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{CORE_API_URL}/system/safe-mode/enter")
    except Exception as e:
        logger.warning("Could not enter safe mode via API: %s", e)
    logger.critical("SAFE MODE ENTERED — core integrity could not be restored")


async def notify_platform_safe_mode() -> None:
    if not PLATFORM_API_URL:
        return
    try:
        key_path = Path("/secure/platform.key")
        api_key = key_path.read_text().strip() if key_path.exists() else ""
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{PLATFORM_API_URL}/device/integrity",
                json={
                    "event": "safe_mode_entered",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except Exception as e:
        logger.warning("Failed to notify platform of safe mode: %s", e)
