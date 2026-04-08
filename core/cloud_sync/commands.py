"""
core/cloud_sync/commands.py — handlers for platform commands:
  - INSTALL_MODULE: download and install a module ZIP from platform URL
  - STOP_MODULE: stop a running module
  - REBOOT: graceful system reboot
  - UPDATE_CORE: reserved for future core self-update
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Registered command handlers: command_type -> coroutine(payload) -> bool
_HANDLERS: dict[str, Any] = {}


def command_handler(cmd_type: str):
    """Decorator to register a command handler function."""
    def decorator(fn):
        _HANDLERS[cmd_type] = fn
        return fn
    return decorator


async def dispatch_command(command: dict) -> bool:
    """Dispatch a received platform command to the appropriate handler.

    Returns True if handled successfully, False on error.
    """
    cmd_type = command.get("type", "")
    payload = command.get("payload", {})
    handler = _HANDLERS.get(cmd_type)
    if handler is None:
        logger.warning("CloudSync: unknown command type '%s'", cmd_type)
        return False
    try:
        result = await handler(payload)
        return result is not False  # None or True = success
    except Exception as e:
        logger.error("CloudSync: handler for '%s' raised: %s", cmd_type, e, exc_info=True)
        return False


# ------------------------------------------------------------------ #
# Handlers                                                             #
# ------------------------------------------------------------------ #

@command_handler("INSTALL_MODULE")
async def handle_install_module(payload: dict) -> bool:
    """Download a module ZIP from platform and install it.

    Payload: { "url": "https://...", "checksum_sha256": "abc123..." }
    """
    url = payload.get("url", "")
    expected_sha256 = payload.get("checksum_sha256", "")

    if not url:
        logger.error("INSTALL_MODULE: missing 'url' in payload")
        return False

    # Download ZIP to temp file
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()
            tmp_path.write_bytes(resp.content)

        # Verify checksum if provided
        if expected_sha256:
            import hashlib
            actual = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
            if actual != expected_sha256:
                logger.error(
                    "INSTALL_MODULE: checksum mismatch (expected=%s actual=%s)",
                    expected_sha256,
                    actual,
                )
                return False

        # Delegate to PluginManager
        from core.module_loader.loader import get_plugin_manager
        manager = get_plugin_manager()
        info = await manager.install(tmp_path)
        logger.info("INSTALL_MODULE: installed '%s' v%s", info.name, info.version)
        return True
    except Exception as e:
        logger.error("INSTALL_MODULE: failed: %s", e, exc_info=True)
        return False
    finally:
        tmp_path.unlink(missing_ok=True)


@command_handler("STOP_MODULE")
async def handle_stop_module(payload: dict) -> bool:
    """Stop a running module.

    Payload: { "name": "climate-module" }
    """
    name = payload.get("name", "")
    if not name:
        logger.error("STOP_MODULE: missing 'name' in payload")
        return False

    try:
        from core.module_loader.loader import get_plugin_manager
        manager = get_plugin_manager()
        info = manager.get_module(name)
        if info is None:
            logger.warning("STOP_MODULE: module '%s' not found", name)
            return False
        if info.type == "SYSTEM":
            logger.error("STOP_MODULE: cannot stop SYSTEM module '%s'", name)
            return False
        await manager.stop(name)
        logger.info("STOP_MODULE: stopped '%s'", name)
        return True
    except Exception as e:
        logger.error("STOP_MODULE: failed for '%s': %s", name, e, exc_info=True)
        return False


@command_handler("REBOOT")
async def handle_reboot(payload: dict) -> bool:
    """Schedule a graceful system reboot after a short delay.

    Payload: { "delay_sec": 5 }  (optional, default 5)
    """
    delay = int(payload.get("delay_sec", 5))
    logger.warning("REBOOT: system reboot in %d seconds", delay)

    async def _do_reboot():
        await asyncio.sleep(delay)
        logger.warning("REBOOT: executing now")
        os.system("systemctl reboot")  # nosec: controlled admin command

    asyncio.create_task(_do_reboot())
    return True


@command_handler("UPDATE_CORE")
async def handle_update_core(payload: dict) -> bool:
    """Trigger a core self-update via the update_manager system module.

    Payload: { "url": "https://...", "sha256": "abc123...", "version": "0.4.1" }

    The download / SHA256 verification / atomic swap all live in
    ``system_modules.update_manager.updater.UpdateManager``. We just publish
    an ``update.apply_core`` event and let the module pick it up — keeps the
    cloud_sync layer free of update logic and means the same event can be
    triggered from other sources (UI button, integrity agent recovery).
    """
    url = payload.get("url", "")
    sha256 = payload.get("sha256") or payload.get("checksum_sha256") or ""
    version = payload.get("version", "")

    if not url:
        logger.error("UPDATE_CORE: missing 'url' in payload")
        return False
    if not sha256:
        logger.error("UPDATE_CORE: missing 'sha256' in payload — refusing unverified update")
        return False

    try:
        from core.eventbus.bus import get_event_bus
        bus = get_event_bus()
        await bus.publish(
            type="update.started",
            source="core.cloud_sync",
            payload={"url": url, "version": version},
        )
        await bus.publish(
            type="update.apply_core",
            source="core.cloud_sync",
            payload={"url": url, "sha256": sha256, "version": version},
        )
        logger.info("UPDATE_CORE: dispatched update.apply_core (version=%s)", version)
        return True
    except Exception as e:
        logger.error("UPDATE_CORE: dispatch failed: %s", e, exc_info=True)
        return False
