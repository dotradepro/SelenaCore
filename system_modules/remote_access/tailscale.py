"""
system_modules/remote_access/tailscale.py — Tailscale integration

Provides:
  - Start Tailscale via tailscale up with auth key
  - Get current Tailscale status (IP, hostname, connected)
  - Disconnect / reconnect
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TAILSCALE_AUTH_KEY = os.environ.get("TAILSCALE_AUTH_KEY", "")


@dataclass
class TailscaleStatus:
    connected: bool
    tailscale_ip: str | None
    hostname: str | None
    version: str | None


async def get_status() -> TailscaleStatus:
    """Return current Tailscale connection status."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tailscale", "status", "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        data = json.loads(stdout)

        self_node = data.get("Self", {})
        tailscale_ip: str | None = None
        ips = self_node.get("TailscaleIPs", [])
        if ips:
            tailscale_ip = ips[0]

        return TailscaleStatus(
            connected=data.get("BackendState") == "Running",
            tailscale_ip=tailscale_ip,
            hostname=self_node.get("HostName"),
            version=data.get("Version"),
        )
    except FileNotFoundError:
        logger.warning("tailscale binary not found")
        return TailscaleStatus(connected=False, tailscale_ip=None, hostname=None, version=None)
    except Exception as e:
        logger.error("Failed to get Tailscale status: %s", e)
        return TailscaleStatus(connected=False, tailscale_ip=None, hostname=None, version=None)


async def connect(auth_key: str | None = None) -> bool:
    """Connect to Tailscale network.

    Uses TAILSCALE_AUTH_KEY env var if auth_key not provided.
    Returns True on success.
    """
    key = auth_key or TAILSCALE_AUTH_KEY
    if not key:
        logger.error("Tailscale: no auth key provided")
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "tailscale", "up",
            "--auth-key", key,
            "--accept-routes",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0:
            logger.info("Tailscale connected successfully")
            return True
        else:
            logger.error("Tailscale up failed: %s", stderr.decode()[:200])
            return False
    except FileNotFoundError:
        logger.error("tailscale binary not found — install Tailscale first")
        return False
    except asyncio.TimeoutError:
        logger.error("Tailscale up timed out")
        return False
    except Exception as e:
        logger.error("Tailscale connect error: %s", e)
        return False


async def disconnect() -> bool:
    """Disconnect from Tailscale."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "tailscale", "down",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=15)
        logger.info("Tailscale disconnected")
        return True
    except Exception as e:
        logger.error("Tailscale disconnect error: %s", e)
        return False
