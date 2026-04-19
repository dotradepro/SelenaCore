"""
tests/test_sdk_base_module.py — SDK base_module unit tests (WebSocket bus client)
"""
from __future__ import annotations

import json
import re
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestDecorators:
    def test_intent_decorator(self):
        from sdk.base_module import intent
        @intent(r"weather|forecast", order=60)
        async def handler(text, ctx):
            pass
        assert handler._intent_pattern == r"weather|forecast"
        assert handler._intent_order == 60

    def test_intent_default_order(self):
        from sdk.base_module import intent
        @intent(r"test")
        async def handler(text, ctx):
            pass
        assert handler._intent_order == 50

    def test_on_event_decorator(self):
        from sdk.base_module import on_event
        @on_event("device.state_changed")
        async def handler(payload):
            pass
        assert handler._event_type == "device.state_changed"

    def test_scheduled_decorator(self):
        from sdk.base_module import scheduled
        @scheduled("every:5m")
        async def handler():
            pass
        assert handler._schedule == "every:5m"


class TestSmartHomeModule:
    def test_init_subclass_sets_name(self):
        from sdk.base_module import SmartHomeModule
        class TestMod(SmartHomeModule):
            pass
        assert TestMod.name == "TestMod"

    def test_explicit_name_preserved(self):
        from sdk.base_module import SmartHomeModule
        class TestMod(SmartHomeModule):
            name = "my-custom-name"
        assert TestMod.name == "my-custom-name"

    def test_discover_handlers(self):
        from sdk.base_module import SmartHomeModule, intent, on_event

        class TestMod(SmartHomeModule):
            name = "test-mod"

            @intent(r"hello", order=30)
            async def handle_hello(self, text, ctx):
                pass

            @intent(r"bye", order=70)
            async def handle_bye(self, text, ctx):
                pass

            @on_event("device.*")
            async def handle_device(self, payload):
                pass

        mod = TestMod()
        assert len(mod._intent_handlers) == 2
        # Should be sorted by order
        assert mod._intent_handlers[0][1] == 30  # order
        assert mod._intent_handlers[1][1] == 70
        assert "device.*" in mod._event_handlers

    def test_validate_intents_raises_on_bad_regex(self):
        from sdk.base_module import SmartHomeModule, intent

        with pytest.raises(ValueError, match="Invalid intent regex"):
            class BadMod(SmartHomeModule):
                name = "bad-mod"

                @intent(r"[invalid")
                async def handle(self, text, ctx):
                    pass
            BadMod()


class TestModuleI18n:
    def test_t_returns_key_when_no_locale(self):
        from sdk.base_module import SmartHomeModule
        class TestMod(SmartHomeModule):
            name = "test-mod"
        mod = TestMod()
        assert mod.t("unknown_key") == "unknown_key"

    def test_t_with_loaded_locale(self):
        from sdk.base_module import SmartHomeModule
        class TestMod(SmartHomeModule):
            name = "test-mod"
        mod = TestMod()
        mod._locale_strings = {
            "en": {"greeting": "Hello {name}!"},
            "uk": {"greeting": "Привіт {name}!"},
        }
        assert mod.t("greeting", lang="en", name="World") == "Hello World!"
        assert mod.t("greeting", lang="uk", name="Світ") == "Привіт Світ!"

    def test_t_fallback_to_en(self):
        from sdk.base_module import SmartHomeModule
        class TestMod(SmartHomeModule):
            name = "test-mod"
        mod = TestMod()
        mod._locale_strings = {
            "en": {"msg": "English fallback"},
        }
        assert mod.t("msg", lang="fr") == "English fallback"

    def test_register_locales_tier_priority(self, tmp_path):
        """Manual > community > auto > en-fallback, all merged per lang."""
        import inspect
        from sdk.base_module import SmartHomeModule

        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text('{"shared": "en-shared", "only_en": "only-en"}')
        # Three tiers for pl — manual should win for overlapping keys.
        (locales / "pl.auto.json").write_text('{"shared": "pl-auto", "a": "from-auto"}')
        (locales / "pl.community.json").write_text('{"shared": "pl-community", "c": "from-community"}')
        (locales / "pl.json").write_text('{"shared": "pl-manual", "m": "from-manual"}')

        class TestMod(SmartHomeModule):
            name = "tier-test"

        # Stub inspect.getfile so _register_locales picks up our tmp dir.
        original_getfile = inspect.getfile
        try:
            inspect.getfile = lambda _cls: str(tmp_path / "fake_module.py")
            mod = TestMod()
        finally:
            inspect.getfile = original_getfile

        pl = mod._locale_strings.get("pl", {})
        assert pl.get("shared") == "pl-manual"   # highest tier wins
        assert pl.get("a") == "from-auto"
        assert pl.get("c") == "from-community"
        assert pl.get("m") == "from-manual"
        assert pl.get("only_en") == "only-en"  # en.json baseline merged first


class TestMatchesSubscription:
    def test_exact(self):
        from sdk.base_module import _matches_subscription
        assert _matches_subscription("device.state_changed", "device.state_changed") is True

    def test_wildcard_all(self):
        from sdk.base_module import _matches_subscription
        assert _matches_subscription("anything", "*") is True

    def test_wildcard_prefix(self):
        from sdk.base_module import _matches_subscription
        assert _matches_subscription("device.offline", "device.*") is True
        assert _matches_subscription("module.started", "device.*") is False

    def test_no_match(self):
        from sdk.base_module import _matches_subscription
        assert _matches_subscription("a.b", "x.y") is False


class TestBuildCapabilities:
    def test_from_decorators(self):
        from sdk.base_module import SmartHomeModule, intent, on_event

        class TestMod(SmartHomeModule):
            name = "test-mod"

            @intent(r"test pattern", order=55)
            async def handle(self, text, ctx):
                pass

            @on_event("device.state_changed")
            async def on_device(self, payload):
                pass

        mod = TestMod()
        caps = mod._build_capabilities()
        assert "intents" in caps
        assert caps["intents"][0]["priority"] == 55
        assert "subscriptions" in caps
        assert "device.state_changed" in caps["subscriptions"]

    def test_manifest_intents_priority(self):
        from sdk.base_module import SmartHomeModule, intent

        class TestMod(SmartHomeModule):
            name = "test-mod"

            @intent(r"decorator pattern")
            async def handle(self, text, ctx):
                pass

        mod = TestMod()
        manifest_intents = [
            {"patterns": {"en": ["manifest pattern"]}, "priority": 40}
        ]
        with patch.object(mod, "_load_manifest", return_value={"intents": manifest_intents}):
            caps = mod._build_capabilities()
            # Manifest should take priority
            assert caps["intents"] == manifest_intents


class TestDispatchIntent:
    @pytest.mark.asyncio
    async def test_matching_handler(self):
        from sdk.base_module import SmartHomeModule, intent

        class TestMod(SmartHomeModule):
            name = "test-mod"

            @intent(r"hello")
            async def handle_hello(self, text, ctx):
                return {"tts_text": "Hi!"}

        mod = TestMod()
        result = await mod._dispatch_intent("hello world", {"_lang": "en"})
        assert result["handled"] is True
        assert result["tts_text"] == "Hi!"

    @pytest.mark.asyncio
    async def test_no_match(self):
        from sdk.base_module import SmartHomeModule, intent

        class TestMod(SmartHomeModule):
            name = "test-mod"

            @intent(r"hello")
            async def handle_hello(self, text, ctx):
                return {"tts_text": "Hi!"}

        mod = TestMod()
        result = await mod._dispatch_intent("goodbye", {"_lang": "en"})
        assert result["handled"] is False

    @pytest.mark.asyncio
    async def test_handler_returns_none(self):
        from sdk.base_module import SmartHomeModule, intent

        class TestMod(SmartHomeModule):
            name = "test-mod"

            @intent(r"skip")
            async def handle_skip(self, text, ctx):
                return None

        mod = TestMod()
        result = await mod._dispatch_intent("skip this", {"_lang": "en"})
        assert result["handled"] is False

    @pytest.mark.asyncio
    async def test_handler_exception(self):
        from sdk.base_module import SmartHomeModule, intent

        class TestMod(SmartHomeModule):
            name = "test-mod"

            @intent(r"crash")
            async def handle_crash(self, text, ctx):
                raise ValueError("boom")

        mod = TestMod()
        result = await mod._dispatch_intent("crash now", {"_lang": "en"})
        assert result["handled"] is False
        assert "boom" in result.get("error", "")


class TestPublishEvent:
    @pytest.mark.asyncio
    async def test_publish_when_disconnected_buffers(self):
        from sdk.base_module import SmartHomeModule

        class TestMod(SmartHomeModule):
            name = "test-mod"

        mod = TestMod()
        # Not connected — should buffer in outbox
        result = await mod.publish_event("test.event", {"key": "val"})
        assert result is True
        assert mod._outbox.qsize() == 1

    @pytest.mark.asyncio
    async def test_publish_when_connected_sends(self):
        from sdk.base_module import SmartHomeModule

        class TestMod(SmartHomeModule):
            name = "test-mod"

        mod = TestMod()
        mod._connected.set()
        mod._ws = AsyncMock()
        result = await mod.publish_event("test.event", {"key": "val"})
        assert result is True
        mod._ws.send.assert_called_once()
        sent = json.loads(mod._ws.send.call_args[0][0])
        assert sent["type"] == "event"
        assert sent["payload"]["event_type"] == "test.event"


class TestHandleApiRequest:
    @pytest.mark.asyncio
    async def test_default_returns_not_implemented(self):
        from sdk.base_module import SmartHomeModule

        class TestMod(SmartHomeModule):
            name = "test-mod"

        mod = TestMod()
        result = await mod.handle_api_request("GET", "/foo", None)
        assert "error" in result
        assert "Not implemented" in result["error"]
