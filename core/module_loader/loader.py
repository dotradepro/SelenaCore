"""
core/module_loader/loader.py — Plugin Manager orchestrator
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.module_loader.sandbox import DockerSandbox, ModuleInfo, ModuleStatus, get_sandbox
from core.module_loader.validator import ValidationResult, validate_zip

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


_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _manager
    if _manager is None:
        _manager = PluginManager()
    return _manager
