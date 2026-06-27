"""
system_modules/backup_manager/module.py — In-process SystemModule wrapper.

Provides API endpoints for local and cloud backup operations.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from core.module_loader.system_module import SystemModule

logger = logging.getLogger(__name__)


class BackupManagerModule(SystemModule):
    name = "backup-manager"

    def __init__(self) -> None:
        super().__init__()

    async def start(self) -> None:
        logger.info("BackupManager module started")
        await self.publish("module.started", {"name": self.name})

    async def stop(self) -> None:
        self._cleanup_subscriptions()
        await self.publish("module.stopped", {"name": self.name})

    def get_router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/status")
        async def status() -> dict:
            from system_modules.backup_manager.local_backup import BACKUP_DEST

            backups: list[dict] = []
            if BACKUP_DEST.exists():
                for f in sorted(BACKUP_DEST.glob("*.tar.gz"), reverse=True)[:10]:
                    backups.append({
                        "name": f.name,
                        "size_mb": round(f.stat().st_size / 1e6, 2),
                        "created": f.stat().st_mtime,
                    })
            return {"backups": backups, "backup_dir": str(BACKUP_DEST)}

        @router.post("/backup/local")
        async def create_local_backup() -> dict:
            from system_modules.backup_manager.local_backup import create_backup

            try:
                result = await create_backup()
                return {"status": "ok", "backup": result}
            except Exception as e:
                logger.error("Local backup failed: %s", e, exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        @router.post("/restore/local")
        async def restore_local_backup(body: dict) -> dict:
            from system_modules.backup_manager.local_backup import restore_backup

            archive = body.get("archive")
            if not archive:
                raise HTTPException(status_code=400, detail="archive path required")
            try:
                await restore_backup(archive)
                return {"status": "ok", "message": "Restore completed"}
            except Exception as e:
                logger.error("Local restore failed: %s", e, exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))

        return router
