"""SystemModule wrapper: REST + scheduler glue for local backup."""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core.module_loader.system_module import SystemModule

from . import state as state_mod
from .local_backup import (
    PRERESTORE_PREFIX,
    REGULAR_PREFIX,
    _get_backup_dest,
    create_backup,
    list_backups,
    restore_backup,
    sha256_file,
)

logger = logging.getLogger(__name__)

SCHEDULER_JOB_ID = "backup-manager.scheduled"
SCHEDULER_FIRE_EVENT = "backup.scheduled.fire"


class BackupManagerModule(SystemModule):
    name = "backup-manager"

    def __init__(self) -> None:
        super().__init__()
        self._busy = asyncio.Lock()
        self._scheduled: tuple[bool, str] | None = None

    async def start(self) -> None:
        _get_backup_dest().mkdir(parents=True, exist_ok=True)
        self.subscribe([SCHEDULER_FIRE_EVENT], self._on_scheduled_fire)
        await self._sync_schedule(state_mod.load_settings())
        logger.info("BackupManager module started")
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        await self.publish("scheduler.unregister", {"job_id": SCHEDULER_JOB_ID})
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    async def _on_scheduled_fire(self, _event: Any) -> None:
        logger.info("Scheduled backup fired")
        try:
            await self._run_backup(prefix=REGULAR_PREFIX)
        except Exception as exc:
            logger.error("Scheduled backup failed: %s", exc, exc_info=True)

    async def _sync_schedule(self, settings: dict[str, Any]) -> None:
        sched = settings.get("schedule", {})
        enabled = bool(sched.get("enabled"))
        trigger = str(sched.get("trigger") or "")
        # Skip churn: scheduler.register removes+re-adds the job, which
        # would advance the next-fire time on every settings save.
        if self._scheduled == (enabled, trigger):
            return
        self._scheduled = (enabled, trigger)
        if enabled and trigger:
            await self.publish("scheduler.register", {
                "job_id": SCHEDULER_JOB_ID,
                "trigger": trigger,
                "event_type": SCHEDULER_FIRE_EVENT,
                "payload": {},
                "owner": self.name,
            })
            logger.info("Backup schedule registered: %s", trigger)
        else:
            await self.publish("scheduler.unregister", {"job_id": SCHEDULER_JOB_ID})
            logger.info("Backup schedule disabled")

    async def _run_backup(
        self,
        *,
        prefix: str = REGULAR_PREFIX,
        paths: list[str] | None = None,
    ) -> Path:
        async with self._busy:
            settings = state_mod.load_settings()
            archive = await create_backup(
                prefix=prefix,
                paths=paths if paths is not None else None,
                max_backups=settings["max_backups"],
            )
            await self.publish("backup.created", {
                "archive": archive.name,
                "prefix": prefix,
                "size_bytes": archive.stat().st_size,
            })
            return archive

    def get_router(self) -> APIRouter:
        router = APIRouter()
        svc = self

        @router.get("/config")
        async def get_config() -> dict:
            return {
                "settings": state_mod.load_settings(),
                "backup_dir": str(_get_backup_dest()),
            }

        @router.patch("/config")
        async def patch_config(body: dict) -> dict:
            merged = state_mod.save_settings(body or {})
            await svc._sync_schedule(merged)
            return {"ok": True, "settings": merged}

        @router.get("/list")
        async def list_archives() -> dict:
            dest = _get_backup_dest()
            return {
                "backups": list_backups(dest),
                "backup_dir": str(dest),
            }

        @router.post("/backup/create")
        async def create_now() -> dict:
            try:
                archive = await svc._run_backup(prefix=REGULAR_PREFIX)
            except Exception as exc:
                logger.error("Manual backup failed: %s", exc, exc_info=True)
                raise HTTPException(500, str(exc)) from exc
            return {
                "ok": True,
                "archive": archive.name,
                "size_bytes": archive.stat().st_size,
                "sha256": sha256_file(archive),
            }

        @router.delete("/backup/{name}")
        async def delete_archive(name: str) -> dict:
            target = _resolve_archive(name)
            try:
                target.unlink()
            except OSError as exc:
                raise HTTPException(500, f"delete failed: {exc}") from exc
            return {"ok": True, "deleted": name}

        @router.get("/backup/{name}/download")
        async def download_archive(name: str):
            target = _resolve_archive(name)
            return FileResponse(
                str(target),
                media_type="application/gzip",
                filename=name,
            )

        @router.post("/backup/upload")
        async def upload_archive(file: UploadFile = File(...)) -> dict:
            fname = (file.filename or "").strip()
            if not fname.endswith(".tar.gz"):
                raise HTTPException(400, "only .tar.gz archives are accepted")
            safe = Path(fname).name  # strip any path components
            dest_dir = _get_backup_dest()
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / safe
            try:
                with dest.open("wb") as out:
                    while True:
                        chunk = await file.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                dest.chmod(0o600)
            except Exception as exc:
                if dest.exists():
                    dest.unlink(missing_ok=True)
                raise HTTPException(500, f"upload failed: {exc}") from exc
            return {
                "ok": True,
                "name": dest.name,
                "size_bytes": dest.stat().st_size,
            }

        @router.post("/backup/{name}/restore")
        async def restore_archive(name: str, body: dict | None = None) -> dict:
            body = body or {}
            confirm = body.get("confirm")
            if confirm != name:
                raise HTTPException(
                    400,
                    "confirm field must equal the archive name",
                )
            target = _resolve_archive(name)

            pre_snapshot: Path | None = None
            try:
                pre_snapshot = await svc._run_backup(prefix=PRERESTORE_PREFIX)
            except Exception as exc:
                logger.warning("pre-restore snapshot failed: %s", exc)

            ok = await restore_backup(target)
            if not ok:
                raise HTTPException(500, "restore failed — see logs")

            await svc.publish("backup.restored", {
                "archive": name,
                "pre_snapshot": pre_snapshot.name if pre_snapshot else None,
            })

            restart = _restart_core_service()
            return {
                "ok": True,
                "restored": name,
                "pre_snapshot": pre_snapshot.name if pre_snapshot else None,
                "restart": restart,
            }

        @router.get("/widget/data/state")
        async def widget_state() -> dict:
            settings = state_mod.load_settings()
            backups = list_backups(_get_backup_dest())
            latest = next(
                (b for b in backups if b["kind"] == "regular"),
                None,
            )
            sched = settings["schedule"]
            if sched["enabled"]:
                pill = {"tone": "ok", "text": "Scheduled", "icon": "clock"}
            else:
                pill = {"tone": "neutral", "text": "Manual only", "icon": "save"}
            rows = [
                {
                    "label": "Latest",
                    "value": latest["name"] if latest else "—",
                    "icon": "archive",
                },
                {
                    "label": "Total",
                    "value": str(len(backups)),
                    "icon": "list",
                },
            ]
            return {
                "label": "Backup",
                "pill": pill,
                "rows": rows,
                "actions": [
                    {"id": "create", "label": "Backup now", "icon": "save", "tone": "info"},
                ],
            }

        @router.post("/widget/action/create")
        async def widget_create() -> dict:
            try:
                archive = await svc._run_backup(prefix=REGULAR_PREFIX)
            except Exception as exc:
                raise HTTPException(500, str(exc)) from exc
            return {"ok": True, "archive": archive.name}

        self._register_html_routes(router, __file__)
        return router


def _resolve_archive(name: str) -> Path:
    """Return absolute path inside the configured backup dir, raising 400 on traversal."""
    safe = Path(name).name
    if safe != name or not safe.endswith(".tar.gz"):
        raise HTTPException(400, "invalid archive name")
    target = _get_backup_dest() / safe
    if not target.exists():
        raise HTTPException(404, f"archive not found: {safe}")
    return target


def _restart_core_service() -> dict[str, Any]:
    """Try to restart selena-core via systemctl; report outcome to caller."""
    if shutil.which("systemctl") is None:
        return {"attempted": False, "reason": "systemctl not available"}
    try:
        # Detached: don't wait for our own process to die mid-response.
        subprocess.Popen(
            ["systemctl", "restart", "selena-core"],
            start_new_session=True,
        )
        return {"attempted": True}
    except Exception as exc:
        logger.warning("systemctl restart failed: %s", exc)
        return {"attempted": False, "reason": str(exc)}
