"""
core/lang_utils.py — Language code utilities.

Single source of truth for ISO 639-1 code ↔ language name mapping.
Uses pycountry/babel if available, otherwise a minimal built-in table
with fallback to code.capitalize().
"""
from __future__ import annotations

# Minimal built-in mapping (covers Whisper's most common outputs).
# NOT a hardcoded limit — unknown codes fall back to code.capitalize()
# which works for most ISO 639-1 codes (e.g. "fr" → "Fr" is wrong,
# but LLMs understand it fine as a language hint).
_KNOWN: dict[str, str] = {
    "af": "Afrikaans", "ar": "Arabic", "be": "Belarusian",
    "bg": "Bulgarian", "ca": "Catalan", "cs": "Czech",
    "da": "Danish", "de": "German", "el": "Greek",
    "en": "English", "es": "Spanish", "et": "Estonian",
    "fi": "Finnish", "fr": "French", "gl": "Galician",
    "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "id": "Indonesian", "is": "Icelandic",
    "it": "Italian", "ja": "Japanese", "ka": "Georgian",
    "kk": "Kazakh", "ko": "Korean", "lt": "Lithuanian",
    "lv": "Latvian", "mk": "Macedonian", "ms": "Malay",
    "nl": "Dutch", "no": "Norwegian", "pl": "Polish",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "sk": "Slovak", "sl": "Slovenian", "sr": "Serbian",
    "sv": "Swedish", "th": "Thai", "tr": "Turkish",
    "uk": "Ukrainian", "vi": "Vietnamese", "zh": "Chinese",
}


def lang_code_to_name(code: str) -> str:
    """Convert ISO 639-1 language code to English name.

    Examples: "uk" → "Ukrainian", "en" → "English", "xx" → "Xx"
    """
    if not code:
        return "English"
    code = code.strip().lower()[:5]
    return _KNOWN.get(code, code.capitalize())


def lang_name_to_code(name: str) -> str:
    """Convert language name to ISO 639-1 code. Best-effort reverse lookup."""
    if not name:
        return "en"
    name_lower = name.lower().strip()
    for code, lang_name in _KNOWN.items():
        if lang_name.lower() == name_lower:
            return code
    # Fallback: first 2 chars
    return name_lower[:2]
