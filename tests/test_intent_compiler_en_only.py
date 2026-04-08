"""IntentCompiler English-only-patterns regression tests.

Verifies the v0.4 contract: any IntentPattern with ``lang != 'en'`` is
silently dropped at load time. Non-English speech is expected to fall
through to the LLM tier instead of matching localised patterns.
"""
from __future__ import annotations

import pytest

from system_modules.llm_engine.intent_compiler import (
    IntentCompiler,
    SystemIntentEntry,
)


class _StubPattern:
    """Stand-in for ``core.registry.models.IntentPattern`` rows."""

    def __init__(self, intent_id: int, lang: str, pattern: str) -> None:
        self.intent_id = intent_id
        self.lang = lang
        self.pattern = pattern
        self.entity_ref = None


class _StubDefinition:
    def __init__(self, id: int, intent: str) -> None:
        self.id = id
        self.intent = intent
        self.module = "test-mod"
        self.noun_class = "DEVICE"
        self.verb = "on"
        self.priority = 50
        self.description = ""
        self.enabled = True

    def get_params_schema(self) -> dict:
        return {}


@pytest.mark.asyncio
async def test_async_load_skips_non_en_patterns(monkeypatch):
    """A mixed en/uk dataset must yield exactly one pattern after load."""
    compiler = IntentCompiler(session_factory=lambda: None)

    # Patch the SQLAlchemy query path so the test doesn't need a DB.
    definitions = [_StubDefinition(1, "device.on")]
    patterns = [
        _StubPattern(1, "en", r"turn on the lamp"),
        _StubPattern(1, "uk", r"увімкни лампу"),
        _StubPattern(1, "fr", r"allume la lampe"),
    ]

    class _FakeResult:
        def __init__(self, items): self._items = items
        def scalars(self): return self
        def all(self): return self._items

    class _FakeSession:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def execute(self, stmt):
            # First call → definitions, second → patterns. Order matches
            # IntentCompiler._async_load.
            if not hasattr(self, "_n"):
                self._n = 0
            self._n += 1
            return _FakeResult(definitions if self._n == 1 else patterns)

    compiler._sf = lambda: _FakeSession()  # type: ignore[assignment]
    await compiler._async_load()

    # Exactly one CompiledIntent should be present, with only the en pattern.
    assert len(compiler._compiled) == 1
    entry = compiler._compiled[0]
    assert set(entry.patterns.keys()) == {"en"}
    assert len(entry.patterns["en"]) == 1


def test_system_intent_entry_en_patterns_helper(caplog):
    """en_patterns() returns the en list and warns about extras."""
    entry = SystemIntentEntry(
        module="m",
        intent="i",
        patterns={"en": ["alpha", "beta"], "uk": ["альфа"]},
    )
    import logging
    with caplog.at_level(logging.WARNING):
        en = entry.en_patterns()
    assert en == ["alpha", "beta"]
    assert any("non-en pattern keys" in r.message for r in caplog.records)


def test_system_intent_entry_en_patterns_no_warning_when_clean(caplog):
    entry = SystemIntentEntry(
        module="m",
        intent="i",
        patterns={"en": ["only english"]},
    )
    import logging
    with caplog.at_level(logging.WARNING):
        assert entry.en_patterns() == ["only english"]
    assert not any("non-en pattern keys" in r.message for r in caplog.records)
