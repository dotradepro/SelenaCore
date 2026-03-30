"""
core/api/routes/intents.py — DEPRECATED: Legacy Module Intent Registry

Intent routing is now handled by Module Bus (core/module_bus.py).
Modules announce intents via WebSocket, IntentRouter queries the bus directly.

These endpoints are kept temporarily for backward compatibility but are no-ops.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from core.api.auth import verify_module_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/intents", tags=["intents (deprecated)"])


@router.post("/register", status_code=201)
async def register_intents(
    _token: str = Depends(verify_module_token),
) -> dict[str, Any]:
    """DEPRECATED: Intents are now registered via Module Bus announce."""
    logger.warning("Deprecated: POST /intents/register called — use Module Bus instead")
    return {"registered": False, "message": "Use Module Bus WebSocket announce instead"}


@router.get("")
async def list_intents(
    _token: str = Depends(verify_module_token),
) -> dict[str, Any]:
    """List intents — returns bus-connected module intents."""
    try:
        from core.module_bus import get_module_bus
        bus = get_module_bus()
        modules = bus.list_modules()
        return {
            "modules": [
                {
                    "module": m["module"],
                    "intents": m["capabilities"].get("intents", []),
                }
                for m in modules
            ],
            "total": len(modules),
            "source": "module_bus",
        }
    except Exception:
        return {"modules": [], "total": 0, "source": "module_bus"}


@router.delete("/{module}", status_code=204, response_class=Response)
async def unregister_intents(
    module: str,
    _token: str = Depends(verify_module_token),
) -> Response:
    """DEPRECATED: Module disconnection from bus handles cleanup automatically."""
    logger.warning("Deprecated: DELETE /intents/%s called — bus handles cleanup", module)
    return Response(status_code=204)
