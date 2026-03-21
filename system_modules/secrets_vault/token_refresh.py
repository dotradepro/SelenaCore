"""
system_modules/secrets_vault/token_refresh.py — Automatic token refresh

Background task that monitors stored tokens and refreshes them 5 minutes
before expiry using the service's refresh_token (RFC 6749 §6).
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .vault import SecretRecord, get_vault

logger = logging.getLogger(__name__)

REFRESH_ENDPOINTS: dict[str, str] = {
    "google": "https://oauth2.googleapis.com/token",
    "github": "https://github.com/login/oauth/access_token",
}

# Registry for client_ids needed for refresh (populated at oauth flow start)
_client_registry: dict[str, str] = {}  # service → client_id


def register_client(service: str, client_id: str) -> None:
    """Register OAuth client_id for a service so token refresh works."""
    _client_registry[service] = client_id


async def _refresh_token(service: str) -> bool:
    """Attempt to refresh the token for a service. Returns True on success."""
    vault = get_vault()
    record = vault.load(service)
    if not record or not record.refresh_token:
        return False

    client_id = _client_registry.get(service)
    token_url = REFRESH_ENDPOINTS.get(service)
    if not client_id or not token_url:
        logger.warning("No refresh config for service %s", service)
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": record.refresh_token,
                    "client_id": client_id,
                },
                headers={"Accept": "application/json"},
            )
            data = resp.json()

        if "access_token" not in data:
            logger.error("Token refresh failed for %s: %s", service, data.get("error"))
            return False

        expires_in = data.get("expires_in")
        record.access_token = data["access_token"]
        record.refresh_token = data.get("refresh_token", record.refresh_token)
        record.expires_at = (time.time() + expires_in) if expires_in else None
        vault.store(record)
        logger.info("Token refreshed for service %s", service)
        return True

    except Exception as exc:
        logger.error("Error refreshing token for %s: %s", service, exc)
        return False


async def token_refresh_loop() -> None:
    """Background loop: check all vault tokens every 60 seconds."""
    logger.info("Token refresh loop started")
    while True:
        await asyncio.sleep(60)
        try:
            vault = get_vault()
            for service in vault.list_services():
                if vault.is_expired(service):
                    logger.info("Token near expiry, refreshing: %s", service)
                    await _refresh_token(service)
        except Exception as exc:
            logger.error("Token refresh loop error: %s", exc)
