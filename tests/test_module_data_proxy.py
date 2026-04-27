"""
tests/test_module_data_proxy.py — Phase 2 widget data/action proxy.

The proxy reads the named module's manifest from the sandbox to find the
upstream path, forwards via in-process httpx ASGITransport, caches per
(module, key), and falls back to a stale value when the upstream fails.
"""
from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest

import core.api.routes.module_data as md


class _StubInfo:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest


class _StubSandbox:
    def __init__(self, modules: dict[str, dict[str, Any]]):
        self._mods = {name: _StubInfo(m) for name, m in modules.items()}

    def get_module(self, name: str):
        return self._mods.get(name)


@pytest.fixture(autouse=True)
def _clear_cache():
    md._cache.clear()
    yield
    md._cache.clear()


def _template_manifest(*, with_action: bool = False) -> dict[str, Any]:
    widget: dict[str, Any] = {
        "kind": "template",
        "template": "metric",
        "data_endpoints": {
            "state": {"path": "/widget/data/state", "cache_ttl_s": 5}
        },
    }
    if with_action:
        widget["actions"] = {"toggle": {"path": "/widget/action/toggle"}}
    return {
        "name": "widget-test",
        "version": "1.0.0",
        "type": "SYSTEM",
        "api_version": "1.0",
        "permissions": [],
        "room": "system",
        "ui": {"widget": widget},
    }


class TestEndpointResolution:
    def test_unknown_module_404(self):
        with patch.object(md, "get_sandbox", return_value=_StubSandbox({})):
            with pytest.raises(Exception) as exc:
                md._widget_endpoint("nope", "state", "data")
            assert "404" in str(exc.value) or "not found" in str(exc.value).lower()

    def test_custom_kind_409(self):
        manifest = _template_manifest()
        manifest["ui"]["widget"]["kind"] = "custom"
        with patch.object(md, "get_sandbox", return_value=_StubSandbox({"x": manifest})):
            with pytest.raises(Exception) as exc:
                md._widget_endpoint("x", "state", "data")
            assert "409" in str(exc.value) or "template" in str(exc.value).lower()

    def test_unknown_data_key_404(self):
        with patch.object(md, "get_sandbox", return_value=_StubSandbox({"x": _template_manifest()})):
            with pytest.raises(Exception) as exc:
                md._widget_endpoint("x", "missing", "data")
            assert "404" in str(exc.value)

    def test_data_endpoint_resolved(self):
        with patch.object(md, "get_sandbox", return_value=_StubSandbox({"x": _template_manifest()})):
            path, ttl = md._widget_endpoint("x", "state", "data")
        assert path == "/widget/data/state"
        assert ttl == 5.0

    def test_action_endpoint_resolved(self):
        manifest = _template_manifest(with_action=True)
        with patch.object(md, "get_sandbox", return_value=_StubSandbox({"x": manifest})):
            path, ttl = md._widget_endpoint("x", "toggle", "action")
        assert path == "/widget/action/toggle"
        assert ttl == 0.0


class TestCacheBehaviour:
    def test_cache_put_then_fresh(self):
        md._cache_put("x", "state", {"v": 1}, ttl=10.0)
        e = md._cache_get("x", "state")
        assert e is not None
        assert e.fresh(time.monotonic())
        assert e.data == {"v": 1}

    def test_cache_stale_within_grace(self):
        md._cache_put("x", "state", {"v": 1}, ttl=0.001)
        time.sleep(0.01)
        e = md._cache_get("x", "state")
        assert e is not None
        now = time.monotonic()
        assert not e.fresh(now)
        assert e.stale_alive(now)

    def test_cache_invalidate_by_module(self):
        md._cache_put("x", "state", {"v": 1}, ttl=10.0)
        md._cache_put("x", "summary", {"v": 2}, ttl=10.0)
        md._cache_put("y", "state", {"v": 3}, ttl=10.0)
        md._cache_invalidate("x")
        assert md._cache_get("x", "state") is None
        assert md._cache_get("x", "summary") is None
        assert md._cache_get("y", "state") is not None
