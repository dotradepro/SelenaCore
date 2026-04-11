"""
core/translit.py — Cyrillic → Latin transliteration for proper nouns.

Used primarily to derive ``voice.wake_word_en`` from a Cyrillic
``voice.wake_word_model`` ("Селена" → "Selena") and to fill ``name_en``
for devices created with a native-language name. Argos translation is
unreliable for proper nouns (treats them as common words), so this
deterministic lookup is preferred.

Covers Ukrainian + Russian + Belarusian alphabets. Unknown characters
are preserved as-is so pure-Latin input passes through unchanged.
"""
from __future__ import annotations

_MAP: dict[str, str] = {
    # Ukrainian / Russian shared
    "а": "a", "б": "b", "в": "v", "г": "h", "д": "d",
    "е": "e", "ж": "zh", "з": "z", "и": "y", "й": "i",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
    "щ": "shch", "ю": "iu", "я": "ia",
    # Ukrainian-specific
    "є": "ie", "і": "i", "ї": "i", "ґ": "g",
    # Russian-specific
    "ё": "e", "ъ": "", "ы": "y", "э": "e",
    # Belarusian-specific
    "ў": "u",
    # Soft/hard signs
    "ь": "",
}


def cyrillic_to_latin(text: str) -> str:
    """Transliterate Cyrillic text to a Latin form.

    Preserves casing of the first letter per word (so "Селена" → "Selena",
    not "selena" or "SELENA"). Non-Cyrillic characters pass through.
    """
    if not text:
        return ""
    out: list[str] = []
    for ch in text:
        lower = ch.lower()
        if lower in _MAP:
            mapped = _MAP[lower]
            if ch.isupper() and mapped:
                mapped = mapped[0].upper() + mapped[1:]
            out.append(mapped)
        else:
            out.append(ch)
    return "".join(out)
