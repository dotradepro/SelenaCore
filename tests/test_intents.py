"""
tests/test_intents.py — Intent registry API + internal matching tests
"""
from __future__ import annotations

import pytest
from unittest.mock import patch


class TestIntentRegistry:
    @pytest.mark.asyncio
    async def test_register_and_list(self, client, auth_headers):
        # Register intents
        resp = await client.post(
            "/api/v1/intents/register",
            json={
                "module": "weather-module",
                "port": 8101,
                "intents": [
                    {
                        "patterns": {
                            "en": ["weather", "forecast"],
                            "ru": ["погода", "прогноз"],
                        },
                        "description": "Weather queries",
                        "endpoint": "/api/intent",
                    }
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["registered"] is True
        assert data["intent_count"] == 1

        # List intents
        resp = await client.get("/api/v1/intents", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    @pytest.mark.asyncio
    async def test_unregister(self, client, auth_headers):
        # Register first
        await client.post(
            "/api/v1/intents/register",
            json={
                "module": "test-mod",
                "port": 8102,
                "intents": [
                    {
                        "patterns": {"en": ["test"]},
                        "endpoint": "/api/intent",
                    }
                ],
            },
            headers=auth_headers,
        )
        # Unregister
        resp = await client.delete("/api/v1/intents/test-mod", headers=auth_headers)
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_unregister_nonexistent(self, client, auth_headers):
        resp = await client.delete("/api/v1/intents/nonexistent", headers=auth_headers)
        assert resp.status_code == 404


class TestIntentMatching:
    def test_find_module_for_text(self):
        from core.api.routes.intents import (
            _intent_registry, _ModuleIntentRecord, IntentEntry, IntentPatterns,
            find_module_for_text,
        )
        # Clear any leftover from API tests
        saved = dict(_intent_registry)
        _intent_registry.clear()

        _intent_registry["weather"] = _ModuleIntentRecord(
            module="weather",
            port=8101,
            intents=[
                IntentEntry(
                    patterns=IntentPatterns(en=["weather", "forecast"], ru=["погода"]),
                    endpoint="/api/weather",
                )
            ],
        )

        result = find_module_for_text("what's the weather like", "en")
        assert result is not None
        module, port, endpoint = result
        assert module == "weather"
        assert port == 8101

        # Russian
        result = find_module_for_text("какая погода", "ru")
        assert result is not None

        # No match
        result = find_module_for_text("play music", "en")
        assert result is None

        # Cleanup
        _intent_registry.clear()
        _intent_registry.update(saved)

    def test_language_fallback_to_en(self):
        from core.api.routes.intents import (
            _intent_registry, _ModuleIntentRecord, IntentEntry, IntentPatterns,
            find_module_for_text,
        )
        _intent_registry["lights"] = _ModuleIntentRecord(
            module="lights",
            port=8103,
            intents=[
                IntentEntry(
                    patterns=IntentPatterns(en=["light"]),
                    endpoint="/api/lights",
                )
            ],
        )

        # Ukrainian not defined — should fallback to English
        result = find_module_for_text("turn on light", "uk")
        assert result is not None

        del _intent_registry["lights"]
