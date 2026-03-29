"""tests/test_cloud_sync.py — pytest tests for CloudSync"""
from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestHmacSignature:
    """Test HMAC-SHA256 signing for platform requests."""

    def test_hmac_produces_sha256_prefix(self) -> None:
        from core.cloud_sync.sync import _hmac_signature

        sig = _hmac_signature(b'{"test": true}', "secret-key")
        assert sig.startswith("sha256=")

    def test_hmac_deterministic(self) -> None:
        from core.cloud_sync.sync import _hmac_signature

        body = b'{"data": "value"}'
        sig1 = _hmac_signature(body, "key")
        sig2 = _hmac_signature(body, "key")
        assert sig1 == sig2

    def test_hmac_different_keys(self) -> None:
        from core.cloud_sync.sync import _hmac_signature

        body = b"test"
        sig1 = _hmac_signature(body, "key1")
        sig2 = _hmac_signature(body, "key2")
        assert sig1 != sig2

    def test_hmac_matches_stdlib(self) -> None:
        from core.cloud_sync.sync import _hmac_signature

        body = b'{"ok": true}'
        secret = "my-secret"
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        assert _hmac_signature(body, secret) == expected


class TestCloudSyncLifecycle:
    """Test CloudSync start/stop lifecycle."""

    @pytest.mark.asyncio
    async def test_get_cloud_sync_singleton(self) -> None:
        from core.cloud_sync.sync import get_cloud_sync

        cs1 = get_cloud_sync()
        cs2 = get_cloud_sync()
        assert cs1 is cs2

    @pytest.mark.asyncio
    async def test_start_mock_mode_no_tasks(self) -> None:
        from core.cloud_sync.sync import CloudSync

        with patch("core.cloud_sync.sync.get_settings") as mock_settings:
            settings = MagicMock()
            settings.platform_api_url = ""
            settings.platform_device_hash = ""
            settings.mock_platform = True
            mock_settings.return_value = settings

            cs = CloudSync()
            await cs.start()
            # Mock mode should not start background tasks
            assert cs._tasks == []

    @pytest.mark.asyncio
    async def test_stop_cancels_tasks(self) -> None:
        from core.cloud_sync.sync import CloudSync

        with patch("core.cloud_sync.sync.get_settings") as mock_settings:
            settings = MagicMock()
            settings.platform_api_url = ""
            settings.platform_device_hash = ""
            settings.mock_platform = True
            mock_settings.return_value = settings

            cs = CloudSync()
            await cs.stop()
            assert cs._tasks == []


class TestCloudSyncCollectState:
    """Test system state collection for heartbeat."""

    def test_collect_state_returns_dict(self) -> None:
        from core.cloud_sync.sync import _collect_system_state

        state = _collect_system_state()
        assert isinstance(state, dict)
