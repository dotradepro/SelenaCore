"""
core/api/auth.py — проверка module_token через Bearer авторизацию
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


def _load_valid_tokens() -> set[str]:
    """Load valid tokens from /secure/module_tokens/ directory."""
    tokens: set[str] = set()
    secure_dir = os.environ.get("CORE_SECURE_DIR", "/secure")
    tokens_dir = Path(secure_dir) / "module_tokens"
    if tokens_dir.exists():
        for token_file in tokens_dir.glob("*.token"):
            token = token_file.read_text().strip()
            if token:
                tokens.add(token)
    # Fallback for development
    dev_token = os.environ.get("DEV_MODULE_TOKEN", "")
    if dev_token:
        tokens.add(dev_token)
    return tokens


async def verify_module_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> str:
    """FastAPI dependency — проверяет Bearer токен модуля."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    valid_tokens = _load_valid_tokens()
    if not valid_tokens:
        # Strict mode: if no tokens configured, deny all
        raise HTTPException(status_code=401, detail="No module tokens configured")
    if token not in valid_tokens:
        logger.warning("Invalid module token attempt: %s...", token[:8])
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token
