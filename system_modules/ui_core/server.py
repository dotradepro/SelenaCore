"""
system_modules/ui_core/server.py — DEPRECATED

The UI proxy server has been merged into core/main.py (unified on port 80).
Static files, SPA fallback, and PWA endpoints are now served directly by Core API.
This file is kept as a minimal stub for backward compatibility.

See: core/main.py (_mount_static_files), core/api/routes/pwa.py
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
logger.info("ui_core/server.py is deprecated — UI is served directly by Core API on port 80")
