"""Plural-form expansion for auto-generated locales.

Given a source English key like:
    devices.registryInfo: "Device Registry — {{count}} devices registered."

and a target language like Russian, produce the CLDR-correct plural forms:
    devices.registryInfo_one:  "Реестр устройств — {{count}} устройство зарегистрировано."
    devices.registryInfo_few:  "Реестр устройств — {{count}} устройства зарегистрировано."
    devices.registryInfo_many: "Реестр устройств — {{count}} устройств зарегистрировано."
    devices.registryInfo_other:"Реестр устройств — {{count}} устройств зарегистрировано."

Argos doesn't understand plural context, so the trick is: substitute a real
representative number for each CLDR category, translate the concrete string,
then swap the number back with `{{count}}`. Not perfect — results often need
a community-PR polish pass — but gets within ~90% of correct across the 14
auto-languages we ship.

i18next picks the `_<form>` suffix at render time based on the passed `count`.
Source English stays flat (`total: '{{count}} total'`); only auto-generated
target bundles get expanded.
"""

from __future__ import annotations

import re
from typing import Callable

# babel is a dev-time dep (see requirements-dev.txt). Import lazily so the
# generator still loads on environments without it — the only cost is
# losing plural expansion (every plural-eligible key collapses to 'other').
try:
    from babel import Locale
    _BABEL_AVAILABLE = True
except ImportError:
    Locale = None  # type: ignore[assignment,misc]
    _BABEL_AVAILABLE = False

# One real number per CLDR category. Chosen to trigger each category across
# the major plural systems (one/two/few/many/other/zero). When a language
# uses fewer categories, the unused entries are simply not emitted.
_SAMPLE_NUMBERS: dict[str, float] = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "few": 3,
    "many": 5,
    "other": 42,
}

_COUNT_PLACEHOLDER = re.compile(r"\{\{\s*count\s*\}\}")


def has_count_placeholder(text: str) -> bool:
    """Return True if the string looks like it needs pluralization."""
    return bool(_COUNT_PLACEHOLDER.search(text))


def plural_categories(lang: str) -> list[str]:
    """Return the CLDR plural categories applicable to `lang`, ordered
    so i18next resolves them correctly ('other' always last as fallback).

    Falls back to ['other'] when babel is not installed — plural
    expansion becomes a no-op in that environment rather than crashing."""
    if not _BABEL_AVAILABLE:
        return ["other"]
    try:
        loc = Locale(lang)
    except Exception:
        return ["other"]
    found: set[str] = set()
    for n in _SAMPLE_NUMBERS.values():
        try:
            cat = loc.plural_form(n)
        except Exception:
            continue
        if isinstance(cat, str):
            found.add(cat)
    found.add("other")
    ordered = [c for c in ("zero", "one", "two", "few", "many", "other") if c in found]
    return ordered


def sample_number_for_category(category: str) -> float:
    return _SAMPLE_NUMBERS.get(category, _SAMPLE_NUMBERS["other"])


def expand_plural_forms(
    source: str,
    target_lang: str,
    translate: Callable[[str], str],
) -> dict[str, str]:
    """Produce `{category: translated_string}` map for a plural-eligible key.

    The `translate` callback is called per category with the number already
    substituted in, so Argos/etc sees a concrete phrase. The returned
    strings have `{{count}}` restored in place of that number.
    """
    if not has_count_placeholder(source):
        return {"other": source}

    forms: dict[str, str] = {}
    for category in plural_categories(target_lang):
        sample = sample_number_for_category(category)
        # Use int rendering when the sample is a whole number — Argos
        # prefers "5 files" over "5.0 files".
        sample_str = str(int(sample)) if sample == int(sample) else str(sample)
        concrete = _COUNT_PLACEHOLDER.sub(sample_str, source)
        translated = translate(concrete)
        # Swap the sample number back with {{count}}. Use regex to match
        # the whole token (not a substring) to avoid replacing stray digits.
        restored = re.sub(
            rf"(?<!\d){re.escape(sample_str)}(?!\d)",
            "{{count}}",
            translated,
            count=1,
        )
        forms[category] = restored
    return forms


def pluralize_key(base_key: str, forms: dict[str, str]) -> dict[str, str]:
    """Map `{category: text}` into i18next-style suffixed keys
    (`base_key_one`, `base_key_other`, etc.). Single-category results
    (`{'other': text}`) collapse to just `{base_key: text}`."""
    if set(forms.keys()) == {"other"}:
        return {base_key: forms["other"]}
    return {f"{base_key}_{cat}": text for cat, text in forms.items()}
