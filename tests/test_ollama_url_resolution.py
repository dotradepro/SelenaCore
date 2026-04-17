"""
tests/test_ollama_url_resolution.py — config resolution for the Ollama
provider after the llm.ollama_url → voice.providers.ollama.url migration.

Covers:
  * OllamaClient reads the new canonical key.
  * The legacy llm.ollama_url still resolves as a transition safeguard.
  * voice.providers.ollama.api_key is attached as a Bearer header when set.
  * OllamaClient.probe() differentiates reachable / auth_required / offline.
"""
from __future__ import annotations

import httpx
import pytest

from system_modules.llm_engine import ollama_client as oc


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Force a fresh OllamaClient singleton per test — config reads happen
    lazily in get_ollama_client(), and tests mutate config keys."""
    oc.reset_ollama_client()
    yield
    oc.reset_ollama_client()


class _FakeConfig:
    """Stand-in for core.config_writer.get_nested / read_config.

    get_nested() resolves dotted paths from the held dict so the test can
    pretend a specific core.yaml is on disk without touching the filesystem.
    """
    def __init__(self, data: dict):
        self.data = data

    def get_nested(self, path: str, default=None):
        node = self.data
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def _install_fake_config(monkeypatch, data: dict):
    fake = _FakeConfig(data)
    # _cfg() imports get_nested lazily inside ollama_client.py, so we
    # patch the import target the module uses.
    import core.config_writer as cw
    monkeypatch.setattr(cw, "get_nested", fake.get_nested)
    # OLLAMA_URL env overrides config, so scrub it for deterministic tests.
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)


def test_new_canonical_key_wins(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://example.invalid:11434"}}},
    })
    client = oc.OllamaClient()
    assert client.base_url == "http://example.invalid:11434"
    assert client.api_key is None


def test_legacy_key_still_resolves(monkeypatch):
    """Transition safeguard — pre-migration core.yaml still boots."""
    _install_fake_config(monkeypatch, {
        "llm": {"ollama_url": "http://legacy.invalid:11434"},
    })
    client = oc.OllamaClient()
    assert client.base_url == "http://legacy.invalid:11434"


def test_canonical_overrides_legacy(monkeypatch):
    """When both keys coexist (mid-migration hand-merge) canonical wins."""
    _install_fake_config(monkeypatch, {
        "llm": {"ollama_url": "http://legacy.invalid:11434"},
        "voice": {"providers": {"ollama": {"url": "http://new.invalid:11434"}}},
    })
    client = oc.OllamaClient()
    assert client.base_url == "http://new.invalid:11434"


def test_env_var_overrides_config(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://config.invalid"}}},
    })
    monkeypatch.setenv("OLLAMA_URL", "http://env.invalid:7070")
    client = oc.OllamaClient()
    assert client.base_url == "http://env.invalid:7070"


def test_default_when_nothing_configured(monkeypatch):
    _install_fake_config(monkeypatch, {})
    client = oc.OllamaClient()
    assert client.base_url == "http://localhost:11434"


def test_api_key_attaches_bearer_header(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {
            "url": "http://remote.invalid:11434",
            "api_key": "sk-ollama-test",
        }}},
    })
    client = oc.OllamaClient()
    assert client.api_key == "sk-ollama-test"
    headers = client._headers()
    assert headers == {"Authorization": "Bearer sk-ollama-test"}


def test_no_api_key_sends_empty_headers(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://local.invalid:11434"}}},
    })
    client = oc.OllamaClient()
    assert client.api_key is None
    assert client._headers() == {}


@pytest.mark.asyncio
async def test_probe_returns_reachable_on_200(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://local.invalid"}}},
    })
    client = oc.OllamaClient()

    class _Resp:
        status_code = 200

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    info = await client.probe()
    assert info == {"reachable": True, "auth_required": False, "status": 200, "error": None}
    assert await client.is_available() is True


@pytest.mark.asyncio
async def test_probe_flags_auth_required_on_401(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://remote.invalid"}}},
    })
    client = oc.OllamaClient()

    class _Resp:
        status_code = 401

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw): return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    info = await client.probe()
    assert info["reachable"] is True
    assert info["auth_required"] is True
    assert info["status"] == 401
    # is_available() treats auth-required as unavailable — UI needs probe()
    # for the nuanced state.
    assert await client.is_available() is False


@pytest.mark.asyncio
async def test_probe_marks_unreachable_on_connect_error(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://down.invalid"}}},
    })
    client = oc.OllamaClient()

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw):
            raise httpx.ConnectError("connect failed")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    info = await client.probe()
    assert info["reachable"] is False
    assert info["auth_required"] is False
    assert info["status"] is None
    assert "connect" in (info["error"] or "").lower()


def test_reset_singleton_rereads_config(monkeypatch):
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://first.invalid"}}},
    })
    c1 = oc.get_ollama_client()
    assert c1.base_url == "http://first.invalid"

    # Simulate a /setup/llm/provider/apikey write that rewrote core.yaml.
    _install_fake_config(monkeypatch, {
        "voice": {"providers": {"ollama": {"url": "http://second.invalid"}}},
    })
    # Without reset the cached singleton keeps the old URL.
    assert oc.get_ollama_client().base_url == "http://first.invalid"
    oc.reset_ollama_client()
    # After reset, a fresh client is built with the new value.
    assert oc.get_ollama_client().base_url == "http://second.invalid"
