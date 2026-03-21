"""
system_modules/secrets_vault/proxy.py — API proxy that injects stored tokens

Modules request external APIs via this proxy; they never see raw tokens.
Route: POST /api/v1/secrets/proxy
Body: { "service": "google", "method": "GET", "url": "https://...", "headers": {}, "json": {} }

The proxy loads the service token from vault, injects Authorization header,
forwards the request, and returns the response — tokens are never exposed.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from .vault import get_vault

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"https"}
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB


async def proxy_request(
    service: str,
    method: str,
    url: str,
    extra_headers: dict[str, str] | None = None,
    json_body: Any | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute an HTTP request with the stored token injected.

    Returns dict: { "status": 200, "headers": {}, "body": ... }
    Raises ValueError for invalid inputs, RuntimeError if token not found.
    """
    # Security: only HTTPS allowed (SSRF mitigation)
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"Only HTTPS URLs are permitted, got scheme: {parsed.scheme!r}")

    # Block private/loopback ranges (SSRF mitigation)
    _block_private_hosts(parsed.hostname or "")

    vault = get_vault()
    record = vault.load(service)
    if not record:
        raise RuntimeError(f"No credentials stored for service: {service!r}")

    token_type = (record.extra or {}).get("token_type", "Bearer")
    headers = {"Authorization": f"{token_type} {record.access_token}"}
    if extra_headers:
        # Do not allow overriding Authorization
        extra_headers.pop("Authorization", None)
        extra_headers.pop("authorization", None)
        headers.update(extra_headers)

    allowed_methods = {"GET", "POST", "PUT", "PATCH", "DELETE"}
    if method.upper() not in allowed_methods:
        raise ValueError(f"HTTP method {method!r} not allowed")

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        request = client.build_request(
            method.upper(),
            url,
            headers=headers,
            json=json_body,
            params=params,
        )
        response = await client.send(request)

    # Read response body (capped)
    body_bytes = response.content[:MAX_RESPONSE_BYTES]
    try:
        body = response.json()
    except Exception:
        body = body_bytes.decode(errors="replace")

    return {
        "status": response.status_code,
        "headers": dict(response.headers),
        "body": body,
    }


def _block_private_hosts(hostname: str) -> None:
    """Raise ValueError if hostname resolves to a private/loopback address (SSRF)."""
    import ipaddress
    import socket

    blocked_networks = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
    ]

    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return  # Let httpx handle DNS errors naturally

    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in blocked_networks:
                if ip in net:
                    raise ValueError(
                        f"SSRF protection: host {hostname!r} resolves to private IP {ip_str}"
                    )
        except ValueError:
            raise
