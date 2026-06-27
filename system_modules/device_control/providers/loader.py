"""
system_modules/device_control/providers/loader.py

ProviderLoader — runtime install / load / unload of device-protocol
provider packages. Reads ``driver_providers`` SQLite table on startup,
imports each enabled provider's driver class via ``importlib``, and
exposes the result as a runtime DRIVERS dict for ``drivers/registry.py``.

Restart resilience contract
---------------------------
* Built-in providers (tinytuya, greeclimate) are auto-seeded on first
  startup if their package is importable. They never need a UI install.
* User-installed providers are persisted in the registry DB. The DB row
  is INSERTed only AFTER ``pip install`` returns success — partial
  installs leave no half-state, the next install retry is safe.
* If a previously-enabled provider's package becomes un-importable
  (e.g. site-packages wiped), ``load_enabled()`` writes the ImportError
  to ``last_error`` and skips it. device-control still starts; the user
  sees a red badge in the Providers tab and can click "Reinstall".

Hot-reload contract
-------------------
After ``install()`` succeeds the loader mutates ``drivers.registry.DRIVERS``
in place — new devices use the freshly imported driver immediately, no
container restart needed. Existing watchers continue with their cached
driver instances; they don't need to reload.

Integrity-agent compatibility
-----------------------------
The agent at ``agent/integrity_agent.py`` watches only
``/opt/selena-core/core/**/*.py``. The provider catalog, the loader,
the driver classes, the pip-installed site-packages, and the registry
DB all live OUTSIDE that scope, so installing/uninstalling providers
never triggers an integrity violation.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import subprocess
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from .catalog import PROVIDERS, ProviderSpec, builtin_provider_ids, get_provider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ..drivers.base import DeviceDriver

logger = logging.getLogger(__name__)

#: Maximum time we wait for a single ``pip install`` to complete.
PIP_TIMEOUT_SECONDS = 300


class ProviderError(Exception):
    """Raised on any provider lifecycle error (install / import / uninstall)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProviderLoader:
    """Owns the runtime DRIVERS dict for device-control."""

    def __init__(self, session_factory: "async_sessionmaker") -> None:
        self._session_factory = session_factory
        # Driver class cache. Mutable — install/uninstall mutate this in place
        # so the registry sees changes without re-importing.
        self.drivers: dict[str, type["DeviceDriver"]] = {}

    # ── Persistence helpers ─────────────────────────────────────────────

    async def _load_rows(self) -> list[Any]:
        from core.registry.models import DriverProvider
        async with self._session_factory() as session:
            res = await session.execute(select(DriverProvider))
            return list(res.scalars())

    async def _upsert_row(
        self,
        provider_id: str,
        *,
        package: str | None,
        version: str,
        enabled: bool,
        auto_detected: bool,
        last_error: str | None = None,
    ) -> None:
        from core.registry.models import DriverProvider
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(DriverProvider, provider_id)
                if row is None:
                    row = DriverProvider(
                        id=provider_id,
                        package=package,
                        version=version,
                        enabled=enabled,
                        auto_detected=auto_detected,
                        installed_at=_utcnow(),
                        last_error=last_error,
                    )
                    session.add(row)
                else:
                    row.package = package
                    row.version = version
                    row.enabled = enabled
                    row.auto_detected = auto_detected
                    row.last_error = last_error

    async def _delete_row(self, provider_id: str) -> None:
        from core.registry.models import DriverProvider
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(DriverProvider, provider_id)
                if row is not None:
                    await session.delete(row)

    async def _set_error(self, provider_id: str, message: str | None) -> None:
        from core.registry.models import DriverProvider
        async with self._session_factory() as session:
            async with session.begin():
                row = await session.get(DriverProvider, provider_id)
                if row is not None:
                    row.last_error = message

    # ── Bootstrap ───────────────────────────────────────────────────────

    async def bootstrap_builtins(self) -> None:
        """On first start, auto-detect each built-in provider.

        For every entry in ``catalog.PROVIDERS`` with ``builtin=True``,
        if its package is importable AND no DB row exists yet, insert
        an enabled row marked ``auto_detected=True``. Idempotent: re-runs
        on every startup do nothing if the row is already present.
        """
        existing = {row.id for row in await self._load_rows()}
        for pid in builtin_provider_ids():
            if pid in existing:
                continue
            spec = PROVIDERS[pid]
            module_path = spec["driver_module"]
            try:
                # Import the driver MODULE first (which lazily imports the
                # underlying pip package via _build_dev / _enum_maps).
                # We don't actually instantiate any driver here — just
                # confirm the file is loadable.
                importlib.import_module(module_path)
                importable = True
                err = None
            except Exception as exc:
                importable = False
                err = f"{type(exc).__name__}: {exc}"
            await self._upsert_row(
                pid,
                package=spec.get("package"),
                version=spec.get("version", ""),
                enabled=importable,
                auto_detected=True,
                last_error=err,
            )

    # ── Loading ─────────────────────────────────────────────────────────

    async def load_enabled(self) -> dict[str, type["DeviceDriver"]]:
        """Walk the DB table, import each enabled provider, populate ``drivers``.

        Failures are logged + recorded in ``last_error`` but never crash
        the caller — device-control must always start.
        """
        self.drivers = {}
        for row in await self._load_rows():
            if not row.enabled:
                continue
            spec = get_provider(row.id)
            if spec is None:
                logger.warning(
                    "provider-loader: ignoring DB row for unknown id %r", row.id,
                )
                continue
            module_path = spec["driver_module"]
            class_name = spec["driver_class"]
            try:
                module = importlib.import_module(module_path)
                cls = getattr(module, class_name)
                self.drivers[row.id] = cls
                if row.last_error:
                    await self._set_error(row.id, None)
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "provider-loader: failed to import %s (%s): %s",
                    row.id, module_path, msg,
                )
                await self._set_error(row.id, msg)
        logger.info(
            "provider-loader: %d driver(s) loaded: %s",
            len(self.drivers), sorted(self.drivers.keys()),
        )
        return self.drivers

    # ── Install / uninstall ─────────────────────────────────────────────

    @staticmethod
    def _pip_install(package_spec: str) -> tuple[bool, str]:
        """Run ``pip install <spec>`` synchronously. Returns (ok, output)."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_spec],
                capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS,
            )
            return result.returncode == 0, (result.stdout + result.stderr)
        except subprocess.TimeoutExpired:
            return False, f"Timed out after {PIP_TIMEOUT_SECONDS}s"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _pip_uninstall(package: str) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "uninstall", "-y", package],
                capture_output=True, text=True, timeout=PIP_TIMEOUT_SECONDS,
            )
            return result.returncode == 0, (result.stdout + result.stderr)
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    async def install(self, provider_id: str) -> tuple[bool, str]:
        """Install a provider's pip package and register the driver class.

        On success, the new driver class is added to ``self.drivers`` and
        the DB row is INSERTed (committed only after pip succeeds — no
        half-state on power loss).
        """
        spec = get_provider(provider_id)
        if spec is None:
            return False, f"Unknown provider id: {provider_id!r}"

        package = spec.get("package")
        version = spec.get("version", "")

        # ── Step 1: pip install (skip for stub providers like mqtt) ────
        if package:
            spec_str = f"{package}{version}" if version else package
            ok, output = await asyncio.to_thread(self._pip_install, spec_str)
            if not ok:
                logger.warning("provider-loader: pip install %s failed:\n%s", spec_str, output)
                return False, output
        else:
            output = "(stub provider — no package to install)"

        # ── Step 2: import the driver module to verify it works ───────
        module_path = spec["driver_module"]
        class_name = spec["driver_class"]
        try:
            # Force re-import in case a previous failed attempt left a
            # cached half-loaded module.
            if module_path in sys.modules:
                importlib.reload(sys.modules[module_path])
            else:
                importlib.import_module(module_path)
            module = sys.modules[module_path]
            cls = getattr(module, class_name)
        except Exception as exc:
            msg = f"Driver import failed: {type(exc).__name__}: {exc}"
            logger.warning("provider-loader: %s", msg)
            return False, msg

        # ── Step 3: persist the row + register in runtime DRIVERS ─────
        await self._upsert_row(
            provider_id,
            package=package,
            version=version,
            enabled=True,
            auto_detected=False,
            last_error=None,
        )
        self.drivers[provider_id] = cls

        # Mirror into the static registry dict so existing code paths
        # (drivers/registry.get_driver) see the change without restart.
        try:
            from ..drivers import registry as drv_registry
            drv_registry.DRIVERS[provider_id] = cls
        except Exception as exc:
            logger.debug("provider-loader: drivers.registry mirror failed: %s", exc)

        logger.info("provider-loader: installed %s", provider_id)
        return True, output

    async def uninstall(self, provider_id: str, *, remove_package: bool = False) -> tuple[bool, str]:
        """Disable a provider. Optionally also pip-uninstall the package.

        By default we keep the package on disk and only flip ``enabled=False``
        in the DB. Set ``remove_package=True`` to also run ``pip uninstall``.
        Existing watchers using this driver continue running until their
        next reconnect — they fail gracefully then.
        """
        spec = get_provider(provider_id)
        if spec is None:
            return False, f"Unknown provider id: {provider_id!r}"

        # Built-in providers can be disabled but not uninstalled (the
        # package is in requirements.txt and gets reinstalled on next build).
        if spec.get("builtin") and remove_package:
            return False, "Built-in providers cannot be pip-uninstalled — disable instead."

        # Remove from runtime drivers map
        self.drivers.pop(provider_id, None)
        try:
            from ..drivers import registry as drv_registry
            drv_registry.DRIVERS.pop(provider_id, None)
        except Exception:
            pass

        if remove_package and spec.get("package"):
            ok, output = await asyncio.to_thread(self._pip_uninstall, spec["package"])
            if not ok:
                return False, output
        else:
            output = "Disabled (package left on disk)"

        await self._delete_row(provider_id)
        logger.info("provider-loader: uninstalled %s", provider_id)
        return True, output

    # ── Status ──────────────────────────────────────────────────────────

    async def list_state(self) -> list[dict[str, Any]]:
        """Return the catalog joined with each provider's DB state.

        UI consumes this to render the Providers tab cards.
        """
        rows = {row.id: row for row in await self._load_rows()}
        out: list[dict[str, Any]] = []
        for pid, spec in PROVIDERS.items():
            row = rows.get(pid)
            installed = row is not None and row.enabled
            entry = {
                "id": pid,
                "name": spec.get("name", pid),
                "description": spec.get("description", ""),
                "package": spec.get("package"),
                "version": spec.get("version", ""),
                "entity_types": spec.get("entity_types", []),
                "needs_cloud": spec.get("needs_cloud", False),
                "builtin": spec.get("builtin", False),
                "icon": spec.get("icon", ""),
                "homepage": spec.get("homepage", ""),
                "needs_external_service": spec.get("needs_external_service", False),
                "installed": installed,
                "auto_detected": bool(row.auto_detected) if row else False,
                "installed_at": row.installed_at.isoformat() if row else None,
                "last_error": row.last_error if row else None,
                "loaded": pid in self.drivers,
            }
            out.append(entry)
        return out
