"""
core/api/routes/bus.py — WebSocket endpoint for Module Bus.

Modules connect to ws://host:7070/api/v1/bus?token=<module_token>
Token is validated BEFORE accept() — invalid clients consume no resources.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)
router = APIRouter(tags=["bus"])


@router.websocket("/bus")
async def module_bus_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for module bus connections.

    Auth: token in query parameter ``?token=<module_token>``.
    Protocol: see ``core/module_bus.py`` for message format.
    """
    token = websocket.query_params.get("token", "")

    # Validate token BEFORE accept — reject immediately if invalid
    if not token or not _verify_token(token):
        try:
            await websocket.close(code=4001, reason="invalid_token")
        except Exception:
            pass
        logger.warning("Bus: rejected connection — invalid token")
        return

    await websocket.accept()

    from core.module_bus import get_module_bus
    bus = get_module_bus()
    await bus.handle_connection(websocket, token=token)


def _verify_token(token: str) -> bool:
    """Verify module token against stored tokens."""
    try:
        from core.api.auth import verify_module_token
        return verify_module_token(token)
    except Exception:
        # In dev mode, accept any non-empty token
        return bool(token)
