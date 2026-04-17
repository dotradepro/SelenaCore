"""
tests/test_wizard_llm_step.py — wizard "LLM Provider" step semantics.

Covers:
  * Ollama submission stores url + optional api_key under voice.providers.ollama.*
  * Skip removes voice.llm_provider so _get_provider() returns a no-op.
  * Cloud submission writes voice.providers.{id}.api_key and model.
  * Cloud list_models text_only filter drops image / embedding / vision SKUs.
"""
from __future__ import annotations

import pytest

from system_modules.llm_engine.cloud_providers import _parse_models, _is_text_model


# ── Cloud model filter ──────────────────────────────────────────────────

def test_text_only_filter_keeps_chat_and_drops_non_text():
    chat = ["gpt-4o", "gpt-4o-mini", "o3-mini", "claude-3-5-sonnet-20240620",
            "gemini-1.5-pro", "llama3.1-8b-instant"]
    drops = ["dall-e-3", "text-embedding-3-large", "whisper-1", "tts-1",
             "imagen-3.0-generate", "veo-2", "gemini-1.5-pro-vision",
             "babbage-002", "davinci-002", "omni-moderation-latest"]
    for m in chat:
        assert _is_text_model(m), m
    for m in drops:
        assert not _is_text_model(m), m


def test_parse_openai_applies_text_only_filter():
    data = {"data": [
        {"id": "gpt-4o"},
        {"id": "gpt-4o-mini"},
        {"id": "dall-e-3"},
        {"id": "text-embedding-3-large"},
        {"id": "whisper-1"},
        {"id": "o3-mini"},
    ]}
    models = _parse_models("openai", data, text_only=True)
    ids = [m["id"] for m in models]
    assert "gpt-4o" in ids
    assert "gpt-4o-mini" in ids
    assert "o3-mini" in ids
    assert "dall-e-3" not in ids
    assert "text-embedding-3-large" not in ids
    assert "whisper-1" not in ids


def test_parse_openai_full_catalog_keeps_everything_compatible():
    """text_only=False returns the pre-filter allowlist (gpt/o1/o3 only)."""
    data = {"data": [
        {"id": "gpt-4o"},
        {"id": "text-embedding-3-large"},
        {"id": "dall-e-3"},
        {"id": "o1"},
    ]}
    models = _parse_models("openai", data, text_only=False)
    ids = [m["id"] for m in models]
    # The per-provider allowlist already drops embeddings/image models
    # regardless of text_only — this just confirms we didn't regress that.
    assert "gpt-4o" in ids
    assert "o1" in ids
    assert "dall-e-3" not in ids


def test_parse_google_text_only_drops_imagen_and_embeddings():
    data = {"models": [
        {"name": "models/gemini-1.5-pro", "displayName": "Gemini 1.5 Pro"},
        {"name": "models/gemini-1.5-flash", "displayName": "Gemini 1.5 Flash"},
        {"name": "models/embedding-001", "displayName": "Embedding 001"},
    ]}
    ids_filtered = [m["id"] for m in _parse_models("google", data, text_only=True)]
    assert "gemini-1.5-pro" in ids_filtered
    assert "gemini-1.5-flash" in ids_filtered
    assert "embedding-001" not in ids_filtered


def test_parse_anthropic_preserves_claude_family():
    data = {"data": [
        {"id": "claude-3-5-sonnet-20240620", "display_name": "Claude 3.5 Sonnet"},
        {"id": "claude-3-opus-20240229", "display_name": "Claude 3 Opus"},
    ]}
    models = _parse_models("anthropic", data, text_only=True)
    ids = [m["id"] for m in models]
    assert "claude-3-5-sonnet-20240620" in ids
    assert "claude-3-opus-20240229" in ids


# ── Wizard step backend handler ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_wizard_llm_provider_ollama_saves_url_and_key(monkeypatch, tmp_path):
    cfg_path = tmp_path / "core.yaml"
    cfg_path.write_text("{}\n")

    import core.config_writer as cw
    monkeypatch.setenv("SELENA_CONFIG", str(cfg_path))
    # Reset the cached path so _get_config_path() re-reads SELENA_CONFIG.
    monkeypatch.setattr(cw, "_CONFIG_PATH", None, raising=False)

    from core.api.routes.ui import _apply_wizard_step
    await _apply_wizard_step("llm_provider", {
        "provider": "ollama",
        "url": "http://192.168.1.10:11434/",
        "api_key": "sk-test",
        "model": "llama3.2",
    })

    saved = cw.read_config()
    voice = saved.get("voice", {})
    assert voice.get("llm_provider") == "ollama"
    assert voice.get("llm_model") == "llama3.2"
    ollama = voice.get("providers", {}).get("ollama", {})
    # Trailing slash trimmed.
    assert ollama.get("url") == "http://192.168.1.10:11434"
    assert ollama.get("api_key") == "sk-test"
    assert ollama.get("model") == "llama3.2"


@pytest.mark.asyncio
async def test_wizard_llm_provider_skip_removes_provider_key(monkeypatch, tmp_path):
    cfg_path = tmp_path / "core.yaml"
    cfg_path.write_text(
        "voice:\n"
        "  llm_provider: ollama\n"
        "  llm_model: phi3:mini\n"
    )

    import core.config_writer as cw
    monkeypatch.setenv("SELENA_CONFIG", str(cfg_path))
    # Reset the cached path so _get_config_path() re-reads SELENA_CONFIG.
    monkeypatch.setattr(cw, "_CONFIG_PATH", None, raising=False)

    from core.api.routes.ui import _apply_wizard_step
    await _apply_wizard_step("llm_provider", {"provider": "skip"})

    saved = cw.read_config()
    voice = saved.get("voice", {})
    assert "llm_provider" not in voice
    assert "llm_model" not in voice


@pytest.mark.asyncio
async def test_wizard_llm_provider_cloud_saves_api_key_only(monkeypatch, tmp_path):
    cfg_path = tmp_path / "core.yaml"
    cfg_path.write_text("{}\n")

    import core.config_writer as cw
    monkeypatch.setenv("SELENA_CONFIG", str(cfg_path))
    # Reset the cached path so _get_config_path() re-reads SELENA_CONFIG.
    monkeypatch.setattr(cw, "_CONFIG_PATH", None, raising=False)

    from core.api.routes.ui import _apply_wizard_step
    await _apply_wizard_step("llm_provider", {
        "provider": "anthropic",
        "api_key": "sk-ant-test",
        "model": "claude-3-5-sonnet-20240620",
    })

    saved = cw.read_config()
    voice = saved.get("voice", {})
    assert voice.get("llm_provider") == "anthropic"
    p_cfg = voice.get("providers", {}).get("anthropic", {})
    assert p_cfg.get("api_key") == "sk-ant-test"
    assert p_cfg.get("model") == "claude-3-5-sonnet-20240620"
    # url is ollama-specific — must not appear on cloud providers.
    assert "url" not in p_cfg


def test_get_provider_returns_empty_when_skipped(monkeypatch):
    """core.llm._get_provider() must not crash on missing voice.llm_provider."""
    import core.config_writer as cw

    def _fake_read_config():
        return {"voice": {}}

    monkeypatch.setattr(cw, "read_config", _fake_read_config)

    from core.llm import _get_provider
    provider, cfg = _get_provider()
    assert provider == ""
    assert cfg == {}
