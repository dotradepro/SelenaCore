"""system_modules/update_manager/module.py — In-process SystemModule wrapper.

Endpoints follow ``docs/TZ_system_modules.md §9.4`` — mounted at
``/api/ui/modules/update-manager/`` by the loader, so paths are bare
(``/status``, ``/check``, ...). The previous ``/update/...`` paths
duplicated the segment and have been removed.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from core.module_loader.system_module import SystemModule
from system_modules.update_manager.updater import UpdateManager, VALID_CHANNELS

logger = logging.getLogger(__name__)


def _read_core_yaml_section() -> dict:
    """Read the ``update_manager:`` section from config/core.yaml if present."""
    try:
        import yaml  # type: ignore

        cfg_path = Path(os.getenv("CORE_CONFIG_PATH", "/opt/selena-core/config/core.yaml"))
        if not cfg_path.exists():
            return {}
        with cfg_path.open() as fh:
            data = yaml.safe_load(fh) or {}
        section = data.get("update_manager") or {}
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("could not read core.yaml update_manager section: %s", exc)
        return {}


def _read_dotversion() -> str:
    """Read /opt/selena-core/.version (written by install.sh + apply-update.sh)."""
    try:
        return Path(
            os.getenv("UPDATE_DOTVERSION_PATH", "/opt/selena-core/.version")
        ).read_text().strip() or "0.1.0"
    except Exception:
        return os.getenv("CURRENT_VERSION", "0.1.0")


class UpdateManagerModule(SystemModule):
    name = "update-manager"

    def __init__(self) -> None:
        super().__init__()
        self._manager: UpdateManager | None = None

    async def _on_apply_core(self, event) -> None:
        """Handle ``update.apply_core`` published by core/cloud_sync/commands.py."""
        if self._manager is None:
            logger.warning("update.apply_core ignored — UpdateManager not running")
            return
        payload = event.payload or {}
        url = payload.get("url", "")
        sha256 = payload.get("sha256", "")
        version = payload.get("version", "")
        try:
            await self._manager.apply_update_from_url(url, sha256, version)
        except Exception as exc:
            logger.error("update.apply_core failed: %s", exc, exc_info=True)

    async def start(self) -> None:
        cfg = _read_core_yaml_section()
        self._manager = UpdateManager(
            publish_event_cb=self.publish,
            current_version=os.getenv("CURRENT_VERSION") or _read_dotversion(),
            repo=os.getenv("UPDATE_REPO", cfg.get("repo", "dotradepro/SelenaCore")),
            channel=os.getenv("UPDATE_CHANNEL", cfg.get("channel", "rc")),
            install_dir=os.getenv("UPDATE_INSTALL_DIR", "/opt/selena-core"),
            backup_dir=os.getenv("UPDATE_BACKUP_DIR", "/opt/selena-backup"),
            check_interval_sec=int(
                os.getenv(
                    "UPDATE_CHECK_INTERVAL",
                    str(cfg.get("check_interval_sec", 21600)),
                )
            ),
            auto_check=bool(cfg.get("auto_check", False)),
            backups_keep=int(cfg.get("backups_keep", 3)),
        )
        await self._manager.start()
        self.subscribe(["update.apply_core"], self._on_apply_core)
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        if self._manager:
            await self._manager.stop()
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        def _req() -> UpdateManager:
            if svc._manager is None:
                raise HTTPException(503, "Service not ready")
            return svc._manager

        svc._register_health_endpoint(router)

        @router.get("/status")
        async def get_status() -> JSONResponse:
            return JSONResponse(_req().get_status())

        @router.post("/check")
        async def check() -> JSONResponse:
            try:
                info = await _req().check()
            except Exception as exc:
                raise HTTPException(502, f"check failed: {exc}") from exc
            return JSONResponse(info)

        @router.get("/versions")
        async def list_versions() -> JSONResponse:
            return JSONResponse({"versions": _req().list_versions()})

        @router.get("/version/{tag}")
        async def get_version(tag: str) -> JSONResponse:
            details = _req().get_version_details(tag)
            if details is None:
                raise HTTPException(404, f"version not found: {tag}")
            return JSONResponse(details)

        @router.post("/install")
        async def install(payload: dict = Body(...)) -> JSONResponse:
            tag = (payload or {}).get("tag")
            if not tag or not isinstance(tag, str):
                raise HTTPException(400, "tag is required")
            try:
                result = await _req().install_version(tag)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(500, str(exc)) from exc
            return JSONResponse(result)

        @router.post("/rollback")
        async def rollback() -> JSONResponse:
            try:
                result = await _req().rollback()
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
            except Exception as exc:
                raise HTTPException(500, str(exc)) from exc
            return JSONResponse(result)

        @router.post("/config")
        async def set_config(payload: dict = Body(...)) -> JSONResponse:
            mgr = _req()
            payload = payload or {}
            if "channel" in payload:
                ch = payload["channel"]
                if ch not in VALID_CHANNELS:
                    raise HTTPException(400, f"invalid channel: {ch!r}")
                mgr.set_channel(ch)
            if "auto_check" in payload:
                mgr.set_auto_check(bool(payload["auto_check"]))
            if "check_interval_sec" in payload:
                try:
                    mgr.set_check_interval(int(payload["check_interval_sec"]))
                except (TypeError, ValueError) as exc:
                    raise HTTPException(400, str(exc)) from exc
            return JSONResponse(mgr.get_status())

        @router.get("/log")
        async def get_log(tag: str | None = None, lines: int = 200) -> JSONResponse:
            return JSONResponse({"log": _req().get_apply_log(tag=tag, max_lines=lines)})

        # ── Dashboard V2 status template ────────────────────────────────────
        @router.get("/widget/data/state")
        async def widget_state() -> dict:
            if svc._manager is None:
                return {
                    "label": "Updates",
                    "pill": {"tone": "neutral", "text": "Not ready", "icon": "alert"},
                    "rows": [],
                }
            s = svc._manager.get_status()
            state = s.get("state", "idle")
            current = s.get("current_version") or "—"
            latest = s.get("latest_version") or "—"
            available = bool(s.get("update_available"))
            error = s.get("error")
            channel = s.get("channel", "stable")

            if error:
                pill = {"tone": "alert", "text": str(error)[:30], "icon": "x"}
            elif state == "checking":
                pill = {"tone": "info", "text": "Checking", "icon": "refresh"}
            elif state == "downloading":
                pill = {"tone": "info", "text": "Downloading", "icon": "refresh"}
            elif state == "installing":
                pill = {"tone": "warn", "text": "Installing", "icon": "clock"}
            elif available:
                pill = {"tone": "warn", "text": f"v{latest} available", "icon": "alert"}
            else:
                pill = {"tone": "ok", "text": "Up to date", "icon": "check"}

            rows = [
                {"label": "Current", "value": str(current)},
                {"label": "Channel", "value": str(channel)},
            ]
            if available and latest != current:
                rows.append({"label": "Latest", "value": str(latest)})
            return {"label": "Updates", "pill": pill, "rows": rows[:4]}

        svc._register_html_routes(router, __file__)
        return router
