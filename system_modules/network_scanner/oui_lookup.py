"""
system_modules/network_scanner/oui_lookup.py — OUI database MAC manufacturer lookup

Downloads/updates the IEEE OUI database and looks up manufacturer names from MAC addresses.
Database stored at /var/lib/selena/oui.txt (updated on first use if absent).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

OUI_DB_PATH = Path(os.environ.get("OUI_DB_PATH", "/var/lib/selena/oui.txt"))
# IEEE public OUI registry URL
OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"

# In-memory cache: prefix (uppercase hex, no colons) → manufacturer
_oui_cache: dict[str, str] = {}
_loaded = False


def _normalize_mac_prefix(mac: str) -> str:
    """Extract first 3 bytes of MAC as uppercase hex, no separators."""
    clean = re.sub(r"[:\-\.]", "", mac).upper()
    return clean[:6]


def _load_db() -> None:
    global _loaded
    if _loaded:
        return
    if not OUI_DB_PATH.exists():
        logger.warning("OUI database not found at %s. Run update_oui_db() to download.", OUI_DB_PATH)
        _loaded = True
        return
    try:
        with OUI_DB_PATH.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                # Format: "AABBCC   (hex)      Manufacturer Name"
                m = re.match(r"^([0-9A-F]{2}-[0-9A-F]{2}-[0-9A-F]{2})\s+\(hex\)\s+(.+)$", line.strip())
                if m:
                    prefix = m.group(1).replace("-", "")
                    _oui_cache[prefix] = m.group(2).strip()
        logger.info("Loaded %d OUI entries from %s", len(_oui_cache), OUI_DB_PATH)
    except Exception as exc:
        logger.error("Failed to load OUI database: %s", exc)
    _loaded = True


def lookup(mac: str) -> str:
    """Return manufacturer name for MAC address, or 'Unknown'."""
    _load_db()
    prefix = _normalize_mac_prefix(mac)
    return _oui_cache.get(prefix, "Unknown")


async def update_oui_db() -> bool:
    """Download latest OUI database from IEEE. Returns True on success."""
    import httpx
    try:
        OUI_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(OUI_URL)
            resp.raise_for_status()
        OUI_DB_PATH.write_bytes(resp.content)
        logger.info("OUI database updated (%d bytes)", len(resp.content))
        # Reload cache
        global _loaded
        _loaded = False
        _oui_cache.clear()
        _load_db()
        return True
    except Exception as exc:
        logger.error("Failed to download OUI database: %s", exc)
        return False
