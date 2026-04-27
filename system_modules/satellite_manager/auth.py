"""HS256 token issue + verify for satellite authentication.

Tokens are only consumed by the same hub that issued them, so we do not need
PyJWT's full framing — a minimal JWT-shaped HMAC-SHA256 suffices and keeps
the dependency surface small.

Token layout:
    base64url(header) . base64url(payload) . base64url(hmac_sha256(...))
where header = {"alg": "HS256", "typ": "SAT"}
      payload = {"device_id": "sat_xxx", "iat": unix_ts}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_SECTION = "satellite_manager"
CONFIG_SECRET_KEY = "hub_secret"
HEADER = {"alg": "HS256", "typ": "SAT"}

# Tokens are long-lived but not forever. ESP32 re-provisions via BLE when the
# token expires. 1 year is a sensible default — long enough to avoid user
# friction, short enough that a leaked token doesn't grant permanent access.
DEFAULT_TTL_S = 365 * 24 * 3600
# Tolerance for clocks that ran backwards (RTC reset after power loss): accept
# tokens whose iat is up to 5 minutes in the future.
CLOCK_SKEW_S = 300


def get_or_create_secret() -> str:
    """Return the module's HMAC secret, creating it on first call."""
    from core.config_writer import read_config, update_config

    cfg = read_config()
    secret = (cfg.get(CONFIG_SECTION, {}) or {}).get(CONFIG_SECRET_KEY)
    if secret:
        return secret
    secret = secrets.token_urlsafe(48)
    update_config(CONFIG_SECTION, CONFIG_SECRET_KEY, secret)
    logger.info("satellite-manager: generated new hub_secret (%d chars)", len(secret))
    return secret


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_token(device_id: str, secret: str, ttl_s: int = DEFAULT_TTL_S) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {"device_id": device_id, "iat": now, "exp": now + ttl_s}
    header_b = _b64url_encode(json.dumps(HEADER, separators=(",", ":")).encode())
    payload_b = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b}.{payload_b}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b}.{payload_b}.{_b64url_encode(sig)}"


def verify_token(token: str, expected_device_id: str, secret: str) -> bool:
    """Constant-time verify of signature + device_id + exp/iat window.

    Tokens issued before `exp` was added (iat present, exp missing) are
    rejected so old provisionings can't linger.
    """
    try:
        header_b, payload_b, sig_b = token.split(".")
    except ValueError:
        return False

    signing_input = f"{header_b}.{payload_b}".encode("ascii")
    expected_sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()

    try:
        actual_sig = _b64url_decode(sig_b)
    except Exception:
        return False

    if not hmac.compare_digest(expected_sig, actual_sig):
        return False

    try:
        payload = json.loads(_b64url_decode(payload_b).decode("utf-8"))
    except Exception:
        return False

    if payload.get("device_id") != expected_device_id:
        return False

    now = int(time.time())
    exp = payload.get("exp")
    iat = payload.get("iat")
    if not isinstance(exp, int) or not isinstance(iat, int):
        return False
    if now > exp:
        logger.info("Satellite token expired (exp=%d, now=%d)", exp, now)
        return False
    if iat - now > CLOCK_SKEW_S:
        logger.info("Satellite token issued too far in future (iat=%d, now=%d)", iat, now)
        return False
    return True
