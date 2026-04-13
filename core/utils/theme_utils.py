"""
core/utils/theme_utils.py — CSS generation and validation for custom themes.

Used by:
  - core/api/routes/ui.py        (theme CRUD endpoints)
  - core/api/routes/shared_assets.py  (dynamic /api/shared/theme.css)
"""
from __future__ import annotations

import re

# CSS custom properties that themes can override.
THEME_VARS = (
    "bg", "sf", "sf2", "sf3",
    "b", "b2",
    "tx", "tx2", "tx3",
    "ac", "gr", "am", "rd", "pu", "tl",
    "r", "shadow", "shadow-lg",
)

# Accept: #rgb, #rrggbb, #rrggbbaa, rgb(...), rgba(...)
_COLOR_RE = re.compile(
    r"^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3,5})?$"
    r"|^rgba?\(.+\)$"
)

# shadow / border-radius are free-form — skip colour regex for them.
_FREEFORM_VARS = {"shadow", "shadow-lg", "r"}


def validate_theme_colors(
    dark: dict[str, str],
    light: dict[str, str],
) -> list[str]:
    """Return a list of human-readable error strings.  Empty list = valid."""
    errors: list[str] = []
    for label, mapping in (("dark", dark), ("light", light)):
        for key, value in mapping.items():
            if key not in THEME_VARS:
                errors.append(f"{label}.{key}: unknown variable")
                continue
            if key in _FREEFORM_VARS:
                continue
            if not _COLOR_RE.match(value):
                errors.append(f"{label}.{key}: invalid color '{value}'")
    return errors


def generate_override_css(theme: dict) -> str:
    """Build a CSS block that overrides :root / :root.light variables.

    Only variables present in the theme's ``dark`` / ``light`` dicts are
    emitted, so the default values from THEME_CSS remain for anything the
    custom theme does not override.

    The returned string is appended **after** the base THEME_CSS so the
    CSS cascade gives these declarations priority.
    """
    dark: dict[str, str] = theme.get("dark", {})
    light: dict[str, str] = theme.get("light", {})

    parts: list[str] = []

    if dark:
        props = "".join(f"--{k}:{v};" for k, v in dark.items() if k in THEME_VARS)
        if props:
            parts.append(f"\n:root{{{props}}}")

    if light:
        props = "".join(f"--{k}:{v};" for k, v in light.items() if k in THEME_VARS)
        if props:
            parts.append(f":root.light{{{props}}}")

    return "".join(parts)
