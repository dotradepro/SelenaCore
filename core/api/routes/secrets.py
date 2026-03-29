"""
core/api/routes/secrets.py — Secrets Vault API endpoints (OAuth + proxy)

Implements AGENTS.md section 4.6:
  POST /api/v1/secrets/oauth/start   — start device authorization flow
  GET  /api/v1/secrets/oauth/status/{session_id} — poll session status
  POST /api/v1/secrets/proxy         — proxy API request with injected token
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.api.auth import verify_module_token

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/secrets", tags=["secrets"])


# ---- Request / Response models ----

class OAuthStartRequest(BaseModel):
    module: str = Field(..., min_length=1, max_length=255)
    provider: str = Field(..., min_length=1, max_length=64)
    scopes: list[str] = Field(default_factory=list)


class OAuthStartResponse(BaseModel):
    session_id: str
    user_code: str
    verification_uri: str
    expires_in: int
    qr_png_b64: str | None = None


class OAuthStatusResponse(BaseModel):
    session_id: str
    service: str
    status: str
    user_code: str | None = None
    verification_uri: str | None = None


class ProxyRequest(BaseModel):
    module: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1)
    method: str = Field(default="GET", pattern=r"^(GET|POST|PUT|PATCH|DELETE)$")
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any | None = None


class ProxyResponse(BaseModel):
    status_code: int
    headers: dict[str, str]
    body: Any


# ---- Endpoints ----

@router.post("/oauth/start", response_model=OAuthStartResponse, status_code=201)
async def oauth_start(
    body: OAuthStartRequest,
    _module_id: str = Depends(verify_module_token),
) -> OAuthStartResponse:
    """Start OAuth Device Authorization Grant flow (RFC 8628)."""
    import os
    from system_modules.secrets_vault.oauth_flow import start_device_flow

    # Determine client_id from env based on provider
    client_id_key = f"{body.provider.upper()}_CLIENT_ID"
    client_id = os.environ.get(client_id_key, "")
    if not client_id:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth provider {body.provider!r} not configured ({client_id_key} not set)",
        )

    try:
        result = await start_device_flow(
            service=body.module,
            client_id=client_id,
            scopes=body.scopes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("OAuth start failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to start OAuth flow")

    return OAuthStartResponse(
        session_id=result["session_id"],
        user_code=result["user_code"],
        verification_uri=result["verification_uri"],
        expires_in=result["expires_in"],
        qr_png_b64=result.get("qr_png_b64"),
    )


@router.get("/oauth/status/{session_id}", response_model=OAuthStatusResponse)
async def oauth_status(
    session_id: str,
    _module_id: str = Depends(verify_module_token),
) -> OAuthStatusResponse:
    """Poll OAuth session status."""
    from system_modules.secrets_vault.oauth_flow import get_session_status

    status = get_session_status(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="OAuth session not found")

    return OAuthStatusResponse(
        session_id=status["session_id"],
        service=status["service"],
        status=status["status"],
        user_code=status.get("user_code"),
        verification_uri=status.get("verification_uri"),
    )


@router.post("/proxy", response_model=ProxyResponse)
async def secrets_proxy(
    body: ProxyRequest,
    _module_id: str = Depends(verify_module_token),
) -> ProxyResponse:
    """Proxy an API request with token injection. Module never sees the token."""
    from system_modules.secrets_vault.proxy import proxy_request

    try:
        result = await proxy_request(
            service=body.module,
            method=body.method,
            url=body.url,
            extra_headers=body.headers if body.headers else None,
            json_body=body.body,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("Proxy request failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail="Proxy request failed")

    return ProxyResponse(
        status_code=result["status"],
        headers=result["headers"],
        body=result["body"],
    )
