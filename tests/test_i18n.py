"""tests/test_i18n.py — Unit tests for core/i18n.py."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core.i18n import (
    _flatten,
    get_system_lang,
    register_module_locales,
    reload_locales,
    t,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear i18n cache before each test."""
    reload_locales()
    yield
    reload_locales()


# ── t() basic ────────────────────────────────────────────────────────────────


def test_t_existing_key_en():
    assert t("media.paused", lang="en") == "Paused"


def test_t_existing_key_uk():
    assert t("media.paused", lang="uk") == "Пауза"


def test_t_fallback_to_en():
    """If a key exists in en but not in uk, fallback to en."""
    # Use a key we know is in en.json
    en_val = t("media.paused", lang="en")
    assert en_val  # exists

    # Force a missing key in some other lang → fallback to en
    result = t("media.paused", lang="xx")
    assert result == en_val


def test_t_fallback_to_raw_key():
    """If key is missing from all locales, return the key itself."""
    result = t("nonexistent.key.here", lang="en")
    assert result == "nonexistent.key.here"


def test_t_interpolation():
    result = t("media.playing_radio", lang="en", station="Jazz FM")
    assert "Jazz FM" in result
    assert "{station}" not in result


def test_t_interpolation_missing_var():
    """Missing interpolation variables should not crash — produce empty string."""
    result = t("media.playing_radio", lang="en")
    # {station} should become empty string via defaultdict
    assert "{station}" not in result


def test_t_interpolation_uk():
    result = t("media.volume_set", lang="uk", level=50)
    assert "50" in result


# ── get_system_lang() ────────────────────────────────────────────────────────


def test_get_system_lang_default():
    with patch("core.i18n.get_system_lang", return_value="en"):
        assert t("media.paused") == "Paused"


# ── register_module_locales() ────────────────────────────────────────────────


def test_register_module_locales(tmp_path: Path):
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    (locales_dir / "en.json").write_text(
        json.dumps({"greeting": "Hello from module"}), encoding="utf-8",
    )
    (locales_dir / "uk.json").write_text(
        json.dumps({"greeting": "Привіт з модуля"}), encoding="utf-8",
    )

    register_module_locales("test-module", locales_dir)

    assert t("test-module.greeting", lang="en") == "Hello from module"
    assert t("test-module.greeting", lang="uk") == "Привіт з модуля"


def test_register_module_locales_nonexistent_dir(tmp_path: Path):
    """Should not crash on missing directory."""
    register_module_locales("ghost", tmp_path / "nope")


# ── reload_locales() ────────────────────────────────────────────────────────


def test_reload_clears_cache():
    # Warm the cache
    val1 = t("media.paused", lang="en")
    assert val1 == "Paused"

    reload_locales()

    # After reload, cache is empty but reloads on next call
    val2 = t("media.paused", lang="en")
    assert val2 == "Paused"


# ── _flatten() ───────────────────────────────────────────────────────────────


def test_flatten_nested():
    data = {"media": {"pause": "Paused", "stop": "Stopped"}}
    result = _flatten(data)
    assert result == {"media.pause": "Paused", "media.stop": "Stopped"}


def test_flatten_already_flat():
    data = {"media.pause": "Paused", "media.stop": "Stopped"}
    result = _flatten(data)
    assert result == data


def test_flatten_deep_nesting():
    data = {"a": {"b": {"c": "deep"}}}
    result = _flatten(data)
    assert result == {"a.b.c": "deep"}


# ── Thread safety ────────────────────────────────────────────────────────────


def test_concurrent_access():
    """Multiple threads calling t() should not crash."""
    results: list[str] = []
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(50):
                results.append(t("media.paused", lang="en"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    assert all(r == "Paused" for r in results)
