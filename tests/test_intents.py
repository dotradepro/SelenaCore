"""
tests/test_intents.py — Deprecated intent registry API tests + Module Bus intent routing
"""
from __future__ import annotations

import pytest


class TestDeprecatedIntentRegistry:
    """Tests for the deprecated HTTP intent registry (backward compat stubs)."""

    @pytest.mark.asyncio
    async def test_register_returns_deprecated(self, client, auth_headers):
        resp = await client.post(
            "/api/v1/intents/register",
            json={},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["registered"] is False
        assert "Module Bus" in data["message"]

    @pytest.mark.asyncio
    async def test_list_returns_bus_data(self, client, auth_headers):
        resp = await client.get("/api/v1/intents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "modules" in data
        assert "total" in data
        assert data["source"] == "module_bus"

    @pytest.mark.asyncio
    async def test_unregister_returns_204(self, client, auth_headers):
        resp = await client.delete("/api/v1/intents/any-module", headers=auth_headers)
        assert resp.status_code == 204
