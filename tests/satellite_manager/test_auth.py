"""Unit tests for satellite_manager.auth."""
from __future__ import annotations

import time
from unittest.mock import patch

from system_modules.satellite_manager.auth import (
    CLOCK_SKEW_S,
    DEFAULT_TTL_S,
    issue_token,
    verify_token,
)


def test_issue_verify_roundtrip():
    secret = "super-secret-dev-key"
    token = issue_token("sat_aabbcc112233", secret)
    assert verify_token(token, "sat_aabbcc112233", secret) is True


def test_wrong_device_id_rejected():
    secret = "s"
    token = issue_token("sat_one", secret)
    assert verify_token(token, "sat_two", secret) is False


def test_wrong_secret_rejected():
    token = issue_token("sat_one", "secret_a")
    assert verify_token(token, "sat_one", "secret_b") is False


def test_tampered_payload_rejected():
    secret = "s"
    token = issue_token("sat_one", secret)
    header, payload, sig = token.split(".")
    # Flip a bit in the payload — signature should no longer match
    bad_payload = payload[:-1] + ("A" if payload[-1] != "A" else "B")
    tampered = f"{header}.{bad_payload}.{sig}"
    assert verify_token(tampered, "sat_one", secret) is False


def test_malformed_token_rejected():
    assert verify_token("not-a-token", "sat_one", "s") is False
    assert verify_token("only.two", "sat_one", "s") is False
    assert verify_token("", "sat_one", "s") is False


def test_token_is_three_segments_base64url():
    token = issue_token("sat_aabbcc112233", "k")
    parts = token.split(".")
    assert len(parts) == 3
    # base64url: only [A-Za-z0-9_-]
    import re
    for p in parts:
        assert re.fullmatch(r"[A-Za-z0-9_-]+", p), p


def test_expired_token_rejected():
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=1_000_000_000):
        token = issue_token("sat_one", "s", ttl_s=60)
    # Jump forward past expiry (1 hour later)
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=1_000_003_600):
        assert verify_token(token, "sat_one", "s") is False


def test_fresh_token_accepted_just_before_expiry():
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=1_000_000_000):
        token = issue_token("sat_one", "s", ttl_s=60)
    # 59 seconds later — still valid
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=1_000_000_059):
        assert verify_token(token, "sat_one", "s") is True


def test_token_from_distant_future_rejected():
    """Clock skew tolerance — small skew OK, large skew rejected."""
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=2_000_000_000):  # issued from the future
        token = issue_token("sat_one", "s")
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=1_000_000_000):  # verifier's clock is way behind
        assert verify_token(token, "sat_one", "s") is False


def test_token_within_clock_skew_accepted():
    skew = CLOCK_SKEW_S // 2
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=1_000_000_000 + skew):  # issued slightly in future
        token = issue_token("sat_one", "s")
    with patch("system_modules.satellite_manager.auth.time.time",
               return_value=1_000_000_000):  # verifier lagging within tolerance
        assert verify_token(token, "sat_one", "s") is True


def test_token_without_exp_rejected():
    """Pre-exp tokens (issued by older code) must no longer validate."""
    import base64
    import hashlib
    import hmac
    import json
    header = {"alg": "HS256", "typ": "SAT"}
    payload = {"device_id": "sat_one", "iat": int(time.time())}  # no 'exp'

    def _b64(d: bytes) -> str:
        return base64.urlsafe_b64encode(d).rstrip(b"=").decode("ascii")

    hb = _b64(json.dumps(header, separators=(",", ":")).encode())
    pb = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(b"s", f"{hb}.{pb}".encode("ascii"), hashlib.sha256).digest()
    legacy = f"{hb}.{pb}.{_b64(sig)}"
    assert verify_token(legacy, "sat_one", "s") is False
