"""Unit tests for the i18n auto-translation generator pipeline.

Uses the StubBackend so these tests don't need argostranslate installed —
the stub emits "[<lang>]<text>" markers which lets us assert the
surrounding structural guarantees (glossary preservation, placeholder
preservation, plural expansion) without depending on real MT quality.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from i18n_backends import StubBackend, pick_backend  # noqa: E402
from i18n_plurals import (  # noqa: E402
    expand_plural_forms,
    has_count_placeholder,
    plural_categories,
    pluralize_key,
)
from generate_auto_locales import (  # noqa: E402
    apply_glossary_shield,
    load_glossary,
    restore_glossary,
    source_hash,
    translate_tree,
)


# ─── Backend ──────────────────────────────────────────────────────────────

def test_stub_backend_deterministic():
    b = StubBackend()
    assert b.translate("Hello", "en", "pl") == "[pl]Hello"
    assert b.translate("", "en", "pl") == ""          # empty passes through
    assert b.translate("  ", "en", "pl") == "  "      # whitespace-only passes through


def test_pick_backend_stub_explicit():
    assert pick_backend(prefer="stub").name == "stub"


# ─── Glossary shielding ───────────────────────────────────────────────────

def test_glossary_shield_replaces_terms_with_tokens():
    terms = ["SHA256", "Ollama"]
    text = "SHA256 · {{checks}} via Ollama"
    shielded, tokens = apply_glossary_shield(text, terms)
    # Tokens opaque — no glossary term remains in shielded text
    assert "SHA256" not in shielded
    assert "Ollama" not in shielded
    assert len(tokens) == 2


def test_glossary_restore_round_trip():
    terms = ["SHA256", "Ollama"]
    text = "SHA256 via Ollama"
    shielded, tokens = apply_glossary_shield(text, terms)
    assert restore_glossary(shielded, tokens, overrides=None) == text


def test_glossary_per_language_override():
    terms = ["Provider"]
    text = "Provider management"
    shielded, tokens = apply_glossary_shield(text, terms)
    restored = restore_glossary(shielded, tokens, overrides={"Provider": "Anbieter"})
    assert restored == "Anbieter management"


# ─── Translate tree with stub backend ─────────────────────────────────────

def test_translate_tree_preserves_placeholder():
    tree = {"greeting": "Hello {{name}}"}
    glossary = {"keep_original": [], "per_language_overrides": {}}
    out = translate_tree(tree, "pl", glossary, StubBackend())
    # Placeholder intact, surrounding text stub-prefixed
    assert "{{name}}" in out["greeting"]
    assert "[pl]" in out["greeting"]


def test_translate_tree_preserves_glossary_terms():
    tree = {"meta": "SHA256 check every {{count}}s"}
    glossary = {"keep_original": ["SHA256"], "per_language_overrides": {}}
    out = translate_tree(tree, "pl", glossary, StubBackend())
    assert "SHA256" in out["meta"]          # kept verbatim
    assert "{{count}}" in out["meta"]       # placeholder kept verbatim


def test_translate_tree_nested():
    tree = {"a": {"b": {"c": "deep"}}}
    glossary = {"keep_original": [], "per_language_overrides": {}}
    out = translate_tree(tree, "pl", glossary, StubBackend())
    assert out["a"]["b"]["c"] == "[pl]deep"


def test_translate_tree_lists_pass_through():
    tree = {"days": ["Mon", "Tue"]}
    glossary = {"keep_original": [], "per_language_overrides": {}}
    out = translate_tree(tree, "pl", glossary, StubBackend())
    assert out["days"] == ["[pl]Mon", "[pl]Tue"]


# ─── Plural expansion ─────────────────────────────────────────────────────

def test_has_count_placeholder():
    assert has_count_placeholder("{{count}} files")
    assert not has_count_placeholder("{{name}} files")
    assert not has_count_placeholder("just text")


try:
    import babel  # noqa: F401
    _HAS_BABEL = True
except ImportError:
    _HAS_BABEL = False

_needs_babel = pytest.mark.skipif(not _HAS_BABEL, reason="babel not installed")


@_needs_babel
def test_plural_categories_russian_has_four():
    cats = plural_categories("ru")
    assert set(cats) == {"one", "few", "many", "other"}
    # 'other' must be last for i18next resolution semantics
    assert cats[-1] == "other"


@_needs_babel
def test_plural_categories_japanese_is_single():
    assert plural_categories("ja") == ["other"]


@_needs_babel
def test_plural_categories_english_is_one_other():
    assert set(plural_categories("en")) == {"one", "other"}


@_needs_babel
def test_expand_plural_forms_restores_count_token():
    # Stub translator uppercases — easy to detect in asserts
    forms = expand_plural_forms("{{count}} files", "ru", translate=lambda s: s.upper())
    assert set(forms.keys()) == {"one", "few", "many", "other"}
    # Every form has {{count}} restored
    for form in forms.values():
        assert "{{count}}" in form


def test_pluralize_key_single_category_collapses():
    """Japanese-style single 'other' form should NOT emit the suffix —
    i18next resolves `foo` directly when no plural suffixes exist."""
    out = pluralize_key("files.count", {"other": "X files"})
    assert out == {"files.count": "X files"}


def test_pluralize_key_multi_category_emits_suffixes():
    forms = {"one": "1 file", "few": "few", "many": "many", "other": "other"}
    out = pluralize_key("files.count", forms)
    assert set(out.keys()) == {
        "files.count_one", "files.count_few",
        "files.count_many", "files.count_other",
    }


@_needs_babel
def test_translate_tree_expands_plural_eligible_keys():
    """Keys with {{count}} should be replaced by suffixed variants
    (at least for languages with >1 plural category)."""
    tree = {"devices_count": "{{count}} devices"}
    glossary = {"keep_original": [], "per_language_overrides": {}}
    out = translate_tree(tree, "ru", glossary, StubBackend())
    assert "devices_count" not in out  # base key replaced
    assert "devices_count_one" in out
    assert "devices_count_few" in out
    assert "devices_count_many" in out
    assert "devices_count_other" in out


# ─── Source hash idempotency ──────────────────────────────────────────────

def test_source_hash_deterministic():
    data = {"a": 1, "b": 2}
    data_reordered = {"b": 2, "a": 1}
    assert source_hash(data) == source_hash(data_reordered)


def test_source_hash_changes_on_content_change():
    assert source_hash({"a": 1}) != source_hash({"a": 2})


# ─── Glossary loader ──────────────────────────────────────────────────────

def test_load_glossary_sorts_by_length_desc(tmp_path):
    """Ensures multi-word terms take precedence in replace-pass order."""
    gpath = tmp_path / "glossary.json"
    gpath.write_text(json.dumps({
        "keep_original": ["Wi", "Wi-Fi", "A"],
        "per_language_overrides": {},
    }))
    g = load_glossary(gpath)
    # "Wi-Fi" (5 chars) comes before "Wi" (2 chars) — otherwise "Wi" would
    # win and we'd produce "Wi-__TERM_1__" instead of "__TERM_0__".
    assert g["keep_original"][0] == "Wi-Fi"
