"""
core/module_loader/sandbox.py — Docker isolation for modules
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
    installed_at: float
    port: int = 0  # Legacy field, unused — modules communicate via WebSocket bus
    container_id: str | None = None
    error: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    module_dir: str | None = None


class DockerSandbox:
    """Manages module lifecycle.

    SYSTEM modules (type=SYSTEM) are loaded in-process via importlib.
    User modules (UI/INTEGRATION/DRIVER/etc.) run as subprocesses,
    communicating with core via the WebSocket Module Bus.
    """

    def __init__(self) -> None:
        try:
            import docker
            self._client = docker.DockerClient(base_url=f"unix://{DOCKER_SOCKET}")
        except Exception:
            self._client = None
            logger.warning("Docker SDK unavailable — only local module execution supported")
        self._modules: dict[str, ModuleInfo] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        # In-process instances for SYSTEM modules
        self._in_process: dict[str, Any] = {}  # name -> SystemModule instance
        self._session_factory: Any = None  # async_sessionmaker, set by main.py

    def set_session_factory(self, factory: Any) -> None:
        """Inject the SQLAlchemy async_sessionmaker for SYSTEM modules.

        Must be called before scan_local_modules() starts SYSTEM modules.
        """
        self._session_factory = factory

    def get_in_process_module(self, name: str) -> Any | None:
        """Return the in-process SystemModule instance, or None."""
        return self._in_process.get(name)

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
            port=0,  # Legacy field — modules use WebSocket bus, not individual ports
            installed_at=datetime.now(timezone.utc).timestamp(),
            manifest=manifest,
            module_dir=str(module_dir) if module_dir else None,
        )
        self._modules[name] = info
        logger.info("Registered module '%s' v%s type=%s", name, info.version, info.type)
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
            port=0,  # Legacy field — modules use WebSocket bus, not individual ports
            installed_at=datetime.now(timezone.utc).timestamp(),
            manifest=manifest,
        )
        self._modules[name] = info

        if info.runtime_mode == "always_on":
            await self.start(name)

        return info

    async def start_local(self, name: str) -> ModuleInfo:
        """Start a locally-discovered module.

        SYSTEM modules are loaded in-process via importlib (no subprocess).
        User modules are launched as subprocesses that connect to the bus.
        """
        info = self._modules.get(name)
        if info is None:
            raise KeyError(f"Module not found: {name}")
        if not info.module_dir:
            raise ValueError(f"Module {name} has no module_dir — cannot start locally")

        # SYSTEM modules run inside the core process — no subprocess, no port
        if info.type == "SYSTEM":
            return await self._start_in_process(info)

        module_dir = info.module_dir
        project_root = str(Path(module_dir).parent.parent)

        from core.config import get_settings
        settings = get_settings()
        bus_url = f"ws://localhost:{settings.core_port}/api/v1/bus"

        env = {
            **os.environ,
            "PYTHONPATH": f"{project_root}:{module_dir}",
            "MODULE_DIR": module_dir,
            "SELENA_BUS_URL": bus_url,
            "MODULE_TOKEN": info.manifest.get("token", os.environ.get("MODULE_TOKEN", "")),
        }

        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=module_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._processes[name] = proc

        # Poll for bus connection readiness
        from core.module_bus import get_module_bus
        bus = get_module_bus()
        for _ in range(30):
            await asyncio.sleep(0.5)
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                info.status = ModuleStatus.ERROR
                info.error = f"Process exited: {stderr[:500]}"
                logger.error("Module %s process exited: %s", name, info.error)
                return info
            if bus.is_connected(name):
                info.status = ModuleStatus.RUNNING
                logger.info(
                    "Local module %s connected to bus (pid=%d)",
                    name, proc.pid,
                )
                return info

        info.status = ModuleStatus.ERROR
        info.error = "Startup timeout (15s) — module did not connect to bus"
        proc.terminate()
        logger.error("Module %s startup timed out (no bus connection)", name)
        return info

    async def _start_in_process(self, info: ModuleInfo) -> ModuleInfo:
        """Load and start a SYSTEM module in-process via importlib."""
        import importlib

        from core.eventbus.bus import get_event_bus
        from core.module_loader.system_module import SystemModule

        module_dir = Path(info.module_dir)
        pkg_name = f"system_modules.{module_dir.name}"

        try:
            pkg = importlib.import_module(pkg_name)
        except ImportError as exc:
            info.status = ModuleStatus.ERROR
            info.error = f"Import failed: {exc}"
            logger.error("Failed to import system module %s: %s", pkg_name, exc)
            return info

        module_cls = getattr(pkg, "module_class", None)
        if module_cls is None:
            info.status = ModuleStatus.ERROR
            info.error = (
                f"No 'module_class' exported from {pkg_name}. "
                "Add 'from .module import XxxModule as module_class' to __init__.py"
            )
            logger.error(info.error)
            return info

        if not (isinstance(module_cls, type) and issubclass(module_cls, SystemModule)):
            info.status = ModuleStatus.ERROR
            info.error = f"{module_cls} is not a SystemModule subclass"
            logger.error(info.error)
            return info

        instance: SystemModule = module_cls()

        if self._session_factory is None:
            logger.warning(
                "session_factory not set — module %s will have no DB access. "
                "Call sandbox.set_session_factory() before scan_local_modules().",
                info.name,
            )
        instance.setup(get_event_bus(), self._session_factory)

        try:
            await instance.start()
        except Exception as exc:
            info.status = ModuleStatus.ERROR
            info.error = f"start() raised: {exc}"
            logger.error(
                "System module %s start() failed: %s", info.name, exc, exc_info=True
            )
            return info

        self._in_process[info.name] = instance
        info.status = ModuleStatus.RUNNING
        logger.info("System module '%s' started in-process", info.name)
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
                    network="selena_selena_internal",
                    mem_limit=mem_limit,
                    cpu_quota=cpu_quota,
                    restart_policy={"Name": "unless-stopped"},
                    environment={
                        "MODULE_NAME": name,
                        "MODULE_DIR": "/opt/selena-module",
                        "SELENA_BUS_URL": "ws://selena-core:7070/api/v1/bus",
                        "MODULE_TOKEN": info.manifest.get("token", ""),
                    },
                    command=["python", "main.py"],
                    remove=False,
                    auto_remove=False,
                ),
            )
            info.container_id = container.id
            logger.info("Module %s started: container=%s", name, container.short_id)
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
            raise PermissionError("Cannot stop SYSTEM modules via the user API")

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

    async def shutdown_in_process_modules(self) -> None:
        """Gracefully stop all in-process SYSTEM modules. Called on core shutdown."""
        for name, instance in list(self._in_process.items()):
            try:
                await instance.stop()
                if name in self._modules:
                    self._modules[name].status = ModuleStatus.STOPPED
                logger.info("System module '%s' stopped gracefully", name)
            except Exception as exc:
                logger.error(
                    "Error stopping system module '%s': %s", name, exc, exc_info=True
                )
        self._in_process.clear()

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
