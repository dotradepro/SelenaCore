"""
core/module_loader/sandbox.py — Docker-изоляция модулей
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
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
    module_dir: str | None = None


class DockerSandbox:
    """Manages module containers via Docker SDK."""

    def __init__(self) -> None:
        try:
            import docker
            self._client = docker.DockerClient(base_url=f"unix://{DOCKER_SOCKET}")
        except Exception:
            self._client = None
            logger.warning("Docker SDK unavailable — only local module execution supported")
        self._modules: dict[str, ModuleInfo] = {}
        self._processes: dict[str, subprocess.Popen] = {}

    def list_modules(self) -> list[ModuleInfo]:
        return list(self._modules.values())

    def get_module(self, name: str) -> ModuleInfo | None:
        return self._modules.get(name)

    def register_from_manifest(
        self, manifest: dict[str, Any], module_dir: Path | None = None,
    ) -> ModuleInfo:
        """Register a module from a parsed manifest (no ZIP extraction).

        Used by auto-discovery to register modules found on disk.
        Skips modules that are already registered.
        """
        name = manifest["name"]
        if name in self._modules:
            return self._modules[name]

        info = ModuleInfo(
            name=name,
            version=manifest["version"],
            type=manifest["type"],
            status=ModuleStatus.READY,
            runtime_mode=manifest.get("runtime_mode", "always_on"),
            port=manifest["port"],
            installed_at=datetime.now(timezone.utc).timestamp(),
            manifest=manifest,
            module_dir=str(module_dir) if module_dir else None,
        )
        self._modules[name] = info
        logger.info("Registered module '%s' v%s (port %d)", name, info.version, info.port)
        return info

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

    async def start_local(self, name: str) -> ModuleInfo:
        """Start a locally-discovered module as a subprocess."""
        info = self._modules.get(name)
        if info is None:
            raise KeyError(f"Module not found: {name}")
        if not info.module_dir:
            raise ValueError(f"Module {name} has no module_dir — cannot start locally")

        module_dir = info.module_dir
        project_root = str(Path(module_dir).parent.parent)
        env = {**os.environ, "PYTHONPATH": f"{project_root}:{module_dir}"}

        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "main:app",
                "--host", "127.0.0.1", "--port", str(info.port),
            ],
            cwd=module_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._processes[name] = proc

        # Poll for health readiness
        import httpx
        for _ in range(30):
            await asyncio.sleep(0.5)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                info.status = ModuleStatus.ERROR
                info.error = f"Process exited: {stderr[:500]}"
                logger.error("Module %s process exited: %s", name, info.error)
                return info
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(
                        f"http://127.0.0.1:{info.port}/health", timeout=2.0,
                    )
                    if resp.status_code == 200:
                        info.status = ModuleStatus.RUNNING
                        logger.info(
                            "Local module %s started on port %d (pid=%d)",
                            name, info.port, proc.pid,
                        )
                        return info
            except Exception:
                pass

        info.status = ModuleStatus.ERROR
        info.error = "Startup timeout (15s)"
        proc.terminate()
        logger.error("Module %s startup timed out", name)
        return info

    async def start(self, name: str) -> ModuleInfo:
        info = self._modules.get(name)
        if info is None:
            raise KeyError(f"Module not found: {name}")

        # Local module — start as subprocess
        if info.module_dir:
            return await self.start_local(name)

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

        # Stop local subprocess
        if name in self._processes:
            proc = self._processes.pop(name)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            info.status = ModuleStatus.STOPPED
            logger.info("Local module %s stopped (pid=%d)", name, proc.pid)
            return info

        # Stop Docker container
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
