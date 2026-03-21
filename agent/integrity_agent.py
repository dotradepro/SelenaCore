"""
agent/integrity_agent.py — Integrity Agent (отдельный процесс)

ВАЖНО: этот файл НЕ импортирует ничего из core/ — работает независимо.
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.config
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.manifest import (
    MANIFEST_PATH,
    MASTER_HASH_PATH,
    check_files,
    create_manifest,
    load_manifest,
    verify_manifest_hash,
)
from agent.responder import (
    enter_safe_mode,
    notify_platform,
    notify_platform_restored,
    notify_platform_safe_mode,
    restore_from_backup,
    restart_core,
    stop_all_modules,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

CHECK_INTERVAL_SEC = int(os.environ.get("AGENT_CHECK_INTERVAL", "30"))
MAX_RESTORE_ATTEMPTS = int(os.environ.get("AGENT_MAX_RESTORE_ATTEMPTS", "3"))
LOG_PATH = "/var/log/selena/integrity.log"


def log_incident(reason: str, changed: list[dict]) -> None:
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "changed_files": changed,
    }
    Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    logger.critical("INTEGRITY VIOLATION: %s | files=%s", reason, len(changed))


async def run_check() -> None:
    # Step 1: Verify manifest itself
    if not verify_manifest_hash():
        await trigger_response("manifest_tampered", [{"path": MANIFEST_PATH}])
        return

    # Step 2: Load and check all core files
    try:
        manifest = load_manifest()
    except FileNotFoundError:
        logger.warning("Manifest not found — creating initial manifest")
        create_manifest()
        return

    changed = check_files(manifest)
    if changed:
        await trigger_response("files_changed", changed)
    else:
        logger.debug("Integrity check passed (%d files)", len(manifest))


async def trigger_response(reason: str, changed: list[dict]) -> None:
    # Step 1: Log incident
    log_incident(reason, changed)

    # Step 2: Stop all modules
    await stop_all_modules()

    # Step 3: Notify platform
    await notify_platform(reason, changed)

    # Step 4: Restore from backup (MAX_RESTORE_ATTEMPTS tries)
    for attempt in range(1, MAX_RESTORE_ATTEMPTS + 1):
        logger.info("Restore attempt %d/%d...", attempt, MAX_RESTORE_ATTEMPTS)
        success = await restore_from_backup(changed)
        if success:
            await restart_core()
            await notify_platform_restored()
            # Recreate manifest after restore
            create_manifest()
            logger.info("Integrity restored after %d attempt(s)", attempt)
            return
        await asyncio.sleep(5)

    # Step 5: SAFE MODE — all restore attempts failed
    logger.critical(
        "All %d restore attempts failed — entering SAFE MODE", MAX_RESTORE_ATTEMPTS
    )
    await enter_safe_mode()
    await notify_platform_safe_mode()


async def check_loop() -> None:
    logger.info(
        "Integrity Agent started (interval=%ds, max_restore=%d)",
        CHECK_INTERVAL_SEC,
        MAX_RESTORE_ATTEMPTS,
    )

    # First run: create manifest if it doesn't exist
    if not Path(MANIFEST_PATH).exists() or not Path(MASTER_HASH_PATH).exists():
        logger.info("First init — creating integrity manifest")
        create_manifest()

    while True:
        await asyncio.sleep(CHECK_INTERVAL_SEC)
        try:
            await run_check()
        except Exception as e:
            logger.error("Check loop error: %s", e, exc_info=True)


def main() -> None:
    asyncio.run(check_loop())


if __name__ == "__main__":
    main()
