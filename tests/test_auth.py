"""
tests/test_auth.py — Authentication module tests
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import patch
from pathlib import Path


class TestLoadValidTokens:
    def test_dev_token_from_env(self):
        from core.api.auth import _load_valid_tokens
        with patch.dict("os.environ", {"DEV_MODULE_TOKEN": "test-token-123", "CORE_SECURE_DIR": "/tmp/nonexistent"}):
            tokens = _load_valid_tokens()
            assert "test-token-123" in tokens

    def test_no_tokens_configured(self):
        from core.api.auth import _load_valid_tokens
        with patch.dict("os.environ", {"DEV_MODULE_TOKEN": "", "CORE_SECURE_DIR": "/tmp/nonexistent"}, clear=False):
            tokens = _load_valid_tokens()
            # Returns dict[str, str] mapping token -> module_id
            assert isinstance(tokens, dict)

    def test_tokens_from_files(self, tmp_path):
        from core.api.auth import _load_valid_tokens
        tokens_dir = tmp_path / "module_tokens"
        tokens_dir.mkdir()
        (tokens_dir / "mod1.token").write_text("token-abc\n")
        (tokens_dir / "mod2.token").write_text("token-xyz\n")

        with patch.dict("os.environ", {"CORE_SECURE_DIR": str(tmp_path), "DEV_MODULE_TOKEN": ""}):
            tokens = _load_valid_tokens()
            assert "token-abc" in tokens
            assert "token-xyz" in tokens

    def test_empty_token_file_skipped(self, tmp_path):
        from core.api.auth import _load_valid_tokens
        tokens_dir = tmp_path / "module_tokens"
        tokens_dir.mkdir()
        (tokens_dir / "empty.token").write_text("")

        with patch.dict("os.environ", {"CORE_SECURE_DIR": str(tmp_path), "DEV_MODULE_TOKEN": ""}):
            tokens = _load_valid_tokens()
            assert "" not in tokens


class TestVerifyModuleToken:
    @pytest.mark.asyncio
    async def test_missing_credentials_raises(self):
        from core.api.auth import verify_module_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await verify_module_token(None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_returns_module_id(self):
        from core.api.auth import verify_module_token
        from fastapi.security import HTTPAuthorizationCredentials
        cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-module-token-xyz")
        module_id = await verify_module_token(cred)
        # DEV_MODULE_TOKEN returns "dev-module" as module_id
        assert module_id == "dev-module"

    @pytest.mark.asyncio
    async def test_invalid_token_raises(self):
        from core.api.auth import verify_module_token
        from fastapi import HTTPException
        from fastapi.security import HTTPAuthorizationCredentials
        cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-bad-token")
        with pytest.raises(HTTPException) as exc_info:
            await verify_module_token(cred)
        assert exc_info.value.status_code == 401
