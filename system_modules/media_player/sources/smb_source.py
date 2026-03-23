# system_modules/media_player/sources/smb_source.py
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .usb_source import USBSource

logger = logging.getLogger(__name__)


class SMBSource:
    """Mount SMB (Samba/Windows) or NFS shares and scan audio files.

    Requires: cifs-utils (SMB) and nfs-common (NFS) installed in the host OS.
    All subprocess calls use explicit argument lists — no shell=True.
    """

    async def mount_smb(
        self,
        host: str,
        share: str,
        username: str = "guest",
        password: str = "",
        domain: str = "WORKGROUP",
    ) -> str:
        """Mount a CIFS/SMB share and return the mount-point path."""
        safe_host = host.replace(".", "_").replace("/", "_")
        safe_share = share.replace("/", "_")
        mount_point = f"/tmp/selena_smb_{safe_host}_{safe_share}"
        Path(mount_point).mkdir(parents=True, exist_ok=True)

        options = (
            f"username={username},password={password},"
            f"domain={domain},iocharset=utf8,ro"
        )
        proc = await asyncio.create_subprocess_exec(
            "mount", "-t", "cifs",
            f"//{host}/{share}", mount_point,
            "-o", options,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"SMB mount failed: {stderr.decode()}")
        logger.info("Mounted //%s/%s at %s", host, share, mount_point)
        return mount_point

    async def mount_nfs(self, host: str, export: str) -> str:
        """Mount an NFS share and return the mount-point path."""
        safe_host = host.replace(".", "_").replace("/", "_")
        mount_point = f"/tmp/selena_nfs_{safe_host}"
        Path(mount_point).mkdir(parents=True, exist_ok=True)

        proc = await asyncio.create_subprocess_exec(
            "mount", "-t", "nfs",
            f"{host}:{export}", mount_point,
            "-o", "ro,soft,timeo=10",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"NFS mount failed for {host}:{export}: {stderr.decode()}")
        logger.info("Mounted NFS %s:%s at %s", host, export, mount_point)
        return mount_point

    async def umount(self, mount_point: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "umount", mount_point,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

    async def scan(self, mount_point: str) -> list[dict]:
        return await USBSource().scan(mount_base=mount_point)
