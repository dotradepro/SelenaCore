"""
core/utils/fuzzy.py — Shared fuzzy string matching utility.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any


def fuzzy_find(
    name: str,
    items: list[dict[str, Any]],
    *,
    key: str = "name",
    threshold: float = 0.5,
) -> dict[str, Any] | None:
    """Return the best-matching item by *key* (ratio >= *threshold*), or ``None``."""
    best: dict[str, Any] | None = None
    best_ratio = 0.0
    name_lower = name.lower().strip()
    for item in items:
        ratio = SequenceMatcher(None, name_lower, item[key].lower()).ratio()
        if ratio > best_ratio:
            best, best_ratio = item, ratio
    return best if best_ratio >= threshold else None
