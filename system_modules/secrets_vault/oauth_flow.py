"""
system_modules/secrets_vault/oauth_flow.py — OAuth Device Authorization Grant (RFC 8628)

Supports major OAuth providers (Google, GitHub, etc.) via the device flow.
Sessions stored in memory (pending) and in the vault once completed.

Flow:
  1. POST /api/v1/secrets/oauth/start  → device_code, user_code, verification_uri, qr image
  2. Client polls  GET /api/v1/secrets/oauth/status/{session_id}
  3. On approval: tokens stored encrypted in SecretsVault
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from .vault import SecretRecord, get_vault

logger = logging.getLogger(__name__)

# Well-known OAuth device authorization endpoints
KNOWN_PROVIDERS: dict[str, dict[str, str]] = {
    "google": {
        "device_auth_url": "https://oauth2.googleapis.com/device/code",
        "token_url": "https://oauth2.googleapis.com/token",
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    },
    "github": {
        "device_auth_url": "https://github.com/login/device/code",
        "token_url": "https://github.com/login/oauth/access_token",
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    },
}


@dataclass
class OAuthSession:
    session_id: str
    service: str
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int
    started_at: float
    status: str = "pending"  # pending | approved | denied | expired


# In-memory session store (keyed by session_id)
_sessions: dict[str, OAuthSession] = {}


async def start_device_flow(
    service: str,
    client_id: str,
    scopes: list[str],
    provider_override: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Start OAuth device authorization flow.

    Returns dict with: session_id, user_code, verification_uri, qr_png_b64.
    """
    provider = provider_override or KNOWN_PROVIDERS.get(service)
    if not provider:
        raise ValueError(f"Unknown OAuth provider: {service!r}. Pass provider_override dict.")

    device_auth_url = provider["device_auth_url"]
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            device_auth_url,
            data={"client_id": client_id, "scope": " ".join(scopes)},
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    device_code = data["device_code"]
    user_code = data["user_code"]
    verification_uri = data.get("verification_uri") or data.get("verification_url", "")
    expires_in = int(data.get("expires_in", 1800))
    interval = int(data.get("interval", 5))

    session_id = str(uuid.uuid4())
    session = OAuthSession(
        session_id=session_id,
        service=service,
        device_code=device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        expires_in=expires_in,
        interval=interval,
        started_at=time.time(),
    )
    _sessions[session_id] = session

    # Start background polling
    asyncio.create_task(_poll_for_token(session, client_id, provider, scopes))

    result: dict[str, Any] = {
        "session_id": session_id,
        "user_code": user_code,
        "verification_uri": verification_uri,
        "expires_in": expires_in,
    }

    # Generate QR code if qrcode library is available
    try:
        import io
        import base64
        import qrcode  # type: ignore

        img = qrcode.make(verification_uri)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result["qr_png_b64"] = base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        result["qr_png_b64"] = None

    return result


async def _poll_for_token(
    session: OAuthSession,
    client_id: str,
    provider: dict[str, str],
    scopes: list[str],
) -> None:
    """Background task: poll token endpoint until approved, denied, or expired."""
    token_url = provider["token_url"]
    grant_type = provider["grant_type"]

    async with httpx.AsyncClient(timeout=15) as client:
        deadline = session.started_at + session.expires_in
        while time.time() < deadline:
            await asyncio.sleep(session.interval)

            try:
                resp = await client.post(
                    token_url,
                    data={
                        "client_id": client_id,
                        "device_code": session.device_code,
                        "grant_type": grant_type,
                    },
                    headers={"Accept": "application/json"},
                )
                data = resp.json()
            except Exception as exc:
                logger.warning("OAuth poll error for %s: %s", session.service, exc)
                continue

            error = data.get("error")

            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                session.interval += 5
                continue
            elif error in ("access_denied", "expired_token"):
                session.status = "denied" if "denied" in error else "expired"
                logger.info("OAuth %s for session %s", session.status, session.session_id)
                return
            elif "access_token" in data:
                # Store token in vault
                expires_in_tok = data.get("expires_in")
                expires_at = (time.time() + expires_in_tok) if expires_in_tok else None
                record = SecretRecord(
                    service=session.service,
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token"),
                    expires_at=expires_at,
                    scopes=scopes,
                    extra={"token_type": data.get("token_type", "Bearer")},
                )
                get_vault().store(record)
                session.status = "approved"
                logger.info("OAuth approved and stored for service %s", session.service)
                return

    # Timed out
    session.status = "expired"
    logger.info("OAuth session %s expired", session.session_id)


def get_session_status(session_id: str) -> dict[str, Any] | None:
    """Return session status dict or None if not found."""
    session = _sessions.get(session_id)
    if not session:
        return None
    return {
        "session_id": session.session_id,
        "service": session.service,
        "status": session.status,
        "user_code": session.user_code,
        "verification_uri": session.verification_uri,
    }
