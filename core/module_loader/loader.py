"""
core/module_loader/loader.py — Plugin Manager orchestrator
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core.module_loader.sandbox import DockerSandbox, ModuleInfo, ModuleStatus, get_sandbox
from core.module_loader.validator import ValidationResult, validate_manifest, validate_zip

logger = logging.getLogger(__name__)


class PluginManager:
    """High-level orchestrator that combines validation + sandbox lifecycle."""

    def __init__(self, sandbox: DockerSandbox | None = None) -> None:
        self._sandbox = sandbox or get_sandbox()

    async def install(self, zip_path: Path) -> ModuleInfo:
        """Validate, install, and start a module from a ZIP archive.

        Returns the running ModuleInfo.
        Raises ValueError on validation failure, RuntimeError for install errors.
        """
        result: ValidationResult = validate_zip(zip_path)
        if not result.valid:
            raise ValueError(f"Module manifest invalid: {result.errors}")

        manifest = result.manifest
        logger.info("Installing module '%s' v%s", manifest["name"], manifest["version"])

        info = await self._sandbox.install(zip_path, manifest)
        logger.info(
            "Module '%s' installed: status=%s port=%d",
            info.name,
            info.status,
            info.port,
        )
        return info

    async def start(self, name: str) -> ModuleInfo:
        return await self._sandbox.start(name)

    async def stop(self, name: str) -> ModuleInfo:
        return await self._sandbox.stop(name)

    async def remove(self, name: str) -> None:
        await self._sandbox.remove(name)

    def list_modules(self) -> list[ModuleInfo]:
        return self._sandbox.list_modules()

    def get_module(self, name: str) -> ModuleInfo | None:
        return self._sandbox.get_module(name)

    def scan_local_modules(self, modules_dir: Path) -> int:
        """Scan a directory for module subdirectories with manifest.json.

        Validates each manifest and registers valid modules in the sandbox.
        Returns the number of modules successfully registered.
        """
        if not modules_dir.is_dir():
            logger.debug("Modules directory does not exist: %s", modules_dir)
            return 0

        count = 0
        for subdir in sorted(modules_dir.iterdir()):
            if not subdir.is_dir():
                continue
            manifest_path = subdir / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to read manifest in %s: %s", subdir.name, e)
                continue

            result = validate_manifest(manifest)
            if not result.valid:
                logger.warning(
                    "Invalid manifest in %s: %s", subdir.name, result.errors
                )
                continue

            self._sandbox.register_from_manifest(result.manifest)
            count += 1

        logger.info("Auto-discovered %d module(s) from %s", count, modules_dir)
        return count


_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
