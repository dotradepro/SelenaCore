"""
core/module_loader/sandbox.py — Docker-изоляция модулей
"""
from __future__ import annotations

import asyncio
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODULES_DATA_DIR = "/var/lib/selena/modules"
DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")


class ModuleStatus(str, Enum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    REMOVED = "REMOVED"


@dataclass
class ModuleInfo:
    name: str
    version: str
    type: str
    status: ModuleStatus
    runtime_mode: str
    port: int
    installed_at: float
    container_id: str | None = None
    error: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)


class DockerSandbox:
    """Manages module containers via Docker SDK."""

    def __init__(self) -> None:
        import docker
        self._client = docker.DockerClient(base_url=f"unix://{DOCKER_SOCKET}")
        self._modules: dict[str, ModuleInfo] = {}

    def list_modules(self) -> list[ModuleInfo]:
        return list(self._modules.values())

    def get_module(self, name: str) -> ModuleInfo | None:
        return self._modules.get(name)

    async def install(self, zip_path: Path, manifest: dict[str, Any]) -> ModuleInfo:
        """Extract ZIP and prepare module, then start container."""
        name = manifest["name"]
        module_dir = Path(MODULES_DATA_DIR) / name
        module_dir.mkdir(parents=True, exist_ok=True)

        # Extract ZIP
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(module_dir)

        info = ModuleInfo(
            name=name,
            version=manifest["version"],
            type=manifest["type"],
            status=ModuleStatus.READY,
            runtime_mode=manifest.get("runtime_mode", "always_on"),
            port=manifest["port"],
            installed_at=datetime.now(timezone.utc).timestamp(),
            manifest=manifest,
        )
        self._modules[name] = info

        if info.runtime_mode == "always_on":
            await self.start(name)

        return info

    async def start(self, name: str) -> ModuleInfo:
        info = self._modules.get(name)
        if info is None:
            raise KeyError(f"Module not found: {name}")

        info.status = ModuleStatus.RUNNING
        loop = asyncio.get_event_loop()

        try:
            module_dir = Path(MODULES_DATA_DIR) / name
            resources = info.manifest.get("resources", {})
            mem_limit = f"{resources.get('memory_mb', 256)}m"
            cpu_quota = int(resources.get("cpu", 0.5) * 100_000)

            container = await loop.run_in_executor(
                None,
                lambda: self._client.containers.run(
                    os.environ.get("MODULE_CONTAINER_IMAGE", "smarthome-modules:latest"),
                    detach=True,
                    name=f"selena-module-{name}",
                    volumes={str(module_dir): {"bind": "/opt/selena-module", "mode": "ro"}},
                    ports={f"{info.port}/tcp": info.port},
                    network="selena_selena_internal",
                    mem_limit=mem_limit,
                    cpu_quota=cpu_quota,
                    restart_policy={"Name": "unless-stopped"},
                    environment={
                        "MODULE_NAME": name,
                        "CORE_API_URL": "http://selena-core:7070/api/v1",
                    },
                    remove=False,
                    auto_remove=False,
                ),
            )
            info.container_id = container.id
            logger.info("Module %s started: container=%s port=%s", name, container.short_id, info.port)
        except Exception as e:
            info.status = ModuleStatus.ERROR
            info.error = str(e)
            logger.error("Failed to start module %s: %s", name, e)
            raise

        return info

    async def stop(self, name: str) -> ModuleInfo:
        info = self._modules.get(name)
        if info is None:
            raise KeyError(f"Module not found: {name}")
        if info.type == "SYSTEM":
            raise PermissionError("Cannot stop SYSTEM modules")

        loop = asyncio.get_event_loop()
        if info.container_id:
            try:
                container = await loop.run_in_executor(
                    None, lambda: self._client.containers.get(info.container_id)
                )
                await loop.run_in_executor(None, container.stop)
                logger.info("Module %s stopped", name)
            except Exception as e:
                logger.warning("Error stopping container for %s: %s", name, e)

        info.status = ModuleStatus.STOPPED
        info.container_id = None
        return info

    async def remove(self, name: str) -> None:
        info = self._modules.get(name)
        if info is None:
            raise KeyError(f"Module not found: {name}")
        if info.type == "SYSTEM":
            raise PermissionError("Cannot remove SYSTEM modules")

        if info.status == ModuleStatus.RUNNING:
            await self.stop(name)

        # Remove module directory
        module_dir = Path(MODULES_DATA_DIR) / name
        if module_dir.exists():
            import shutil
            shutil.rmtree(module_dir)

        info.status = ModuleStatus.REMOVED
        del self._modules[name]
        logger.info("Module %s removed", name)


# Singleton
_sandbox: DockerSandbox | None = None


def get_sandbox() -> DockerSandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = DockerSandbox()
    return _sandbox
