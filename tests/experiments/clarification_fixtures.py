"""Two-turn fixtures for the clarification bench.

Each entry models a single user interaction where the classifier had to
ask a follow-up question on turn 1, then resolve the intent from the
user's reply on turn 2. Fixtures are intentionally hand-curated (not
auto-generated from the registry) because they need an expected 2-turn
flow — can't be derived from a single-entity catalog.

Schema (one dict per scenario):

    {
        "name":                 "short descriptive id",
        "lang":                 "en" | "uk",
        "turn_1_text":          "first utterance (the ambiguous / missing-param command)",
        "turn_2_text":          "reply to the clarification question",
        "expected_reason":      "ambiguous_device" | "missing_param" | "low_margin",
        "expected_final_intent":"what should fire after clarification resolves",
        "expected_final_params":{"partial slot dict we must see in the merged params"},
        "allow_cancelled":      True,   # if True, a clarify.cancelled
                                        # outcome also counts as pass
                                        # (for fuzzy-fail scenarios)
    }

The bench harness ``run_clarification_bench.py`` consumes this list.
"""
from __future__ import annotations

from typing import Any


FIXTURES: list[dict[str, Any]] = [
    # ── 1. ambiguous_device → resolved by room ──
    {
        "name": "ambiguous.resolve_by_room.en",
        "lang": "en",
        "turn_1_text": "turn off the light",
        "turn_2_text": "bedroom",
        "expected_reason": "ambiguous_device",
        "expected_final_intent": "device.off",
        "expected_final_params": {"entity": "light"},
    },
    {
        "name": "ambiguous.resolve_by_room.uk",
        "lang": "uk",
        "turn_1_text": "вимкни світло",
        "turn_2_text": "у спальні",
        "expected_reason": "ambiguous_device",
        "expected_final_intent": "device.off",
        "expected_final_params": {"entity": "light"},
    },

    # ── 2. ambiguous_device → resolved by positional reference ──
    {
        "name": "ambiguous.resolve_by_position.en",
        "lang": "en",
        "turn_1_text": "turn on the thermostat",
        "turn_2_text": "the first one",
        "expected_reason": "ambiguous_device",
        "expected_final_intent": "device.on",
        "expected_final_params": {},
    },
    {
        "name": "ambiguous.resolve_by_position.uk",
        "lang": "uk",
        "turn_1_text": "увімкни термостат",
        "turn_2_text": "перший",
        "expected_reason": "ambiguous_device",
        "expected_final_intent": "device.on",
        "expected_final_params": {},
    },

    # ── 3. ambiguous_device → resolved by direct device-name mention ──
    {
        "name": "ambiguous.resolve_by_name.en",
        "lang": "en",
        "turn_1_text": "turn off the switch",
        "turn_2_text": "the one in the kitchen",
        "expected_reason": "ambiguous_device",
        "expected_final_intent": "device.off",
        "expected_final_params": {"entity": "switch"},
    },

    # ── 4. missing_param (set_temperature) → resolved by numeric digit ──
    {
        "name": "missing_temp.numeric.en",
        "lang": "en",
        "turn_1_text": "set the temperature",
        "turn_2_text": "22",
        "expected_reason": "missing_param",
        "expected_final_intent": "device.set_temperature",
        "expected_final_params": {"value": "22"},
    },
    {
        "name": "missing_temp.numeric.uk",
        "lang": "uk",
        "turn_1_text": "встанови температуру",
        "turn_2_text": "22",
        "expected_reason": "missing_param",
        "expected_final_intent": "device.set_temperature",
        "expected_final_params": {"value": "22"},
    },

    # ── 5. missing_param → resolved by word-form number ──
    {
        "name": "missing_temp.word_number.en",
        "lang": "en",
        "turn_1_text": "set the temperature",
        "turn_2_text": "twenty-two degrees",
        "expected_reason": "missing_param",
        "expected_final_intent": "device.set_temperature",
        "expected_final_params": {"value": "22"},
    },

    # ── 6. missing_param (set_mode) → resolved by allowed-value ──
    {
        "name": "missing_mode.allowed_value.en",
        "lang": "en",
        "turn_1_text": "set the mode",
        "turn_2_text": "cool",
        "expected_reason": "missing_param",
        "expected_final_intent": "device.set_mode",
        "expected_final_params": {"value": "cool"},
    },

    # ── 7. low_margin → resolved by direct pick ──
    # (low_margin cases are hard to synthesize deterministically
    #  — skipped in this fixture set; unit tests in the router
    #  exercise the matcher directly with a fabricated pending ctx.)

    # ── 8. fuzzy-fail → cancel ──
    {
        "name": "fuzzy_fail.ambiguous.nonsense",
        "lang": "en",
        "turn_1_text": "turn off the light",
        "turn_2_text": "asdfjkl qwerty zxcv",
        "expected_reason": "ambiguous_device",
        # Reply should not match — expect cancel, not resolution.
        "expected_final_intent": "unknown",
        "expected_final_params": {},
        "allow_cancelled": True,
    },
    {
        "name": "fuzzy_fail.missing_param.nonsense",
        "lang": "en",
        "turn_1_text": "set the temperature",
        "turn_2_text": "blah blah",
        "expected_reason": "missing_param",
        "expected_final_intent": "unknown",
        "expected_final_params": {},
        "allow_cancelled": True,
    },

    # ── 9. ambiguous → resolved by room (UK), entity spoken in EN ──
    #      (tests language-mix path from plan §R3)
    {
        "name": "ambiguous.cross_lang.uk_room",
        "lang": "uk",
        "turn_1_text": "вимкни кондиціонер",
        "turn_2_text": "у вітальні",
        "expected_reason": "ambiguous_device",
        "expected_final_intent": "device.off",
        "expected_final_params": {"entity": "air_conditioner"},
    },

    # ── 10. ambiguous → yes/affirmation for single-device confirmation ──
    #       (not exercised in current router — ambiguous only fires with
    #        2+ matches, not 1. Left out; could be added with a dedicated
    #        "did you mean the bedroom light?" flow in a later round.)

    # ── 11. ambiguous room → user says "second" ──
    {
        "name": "ambiguous.positional_second",
        "lang": "en",
        "turn_1_text": "turn on the speaker",
        "turn_2_text": "the second",
        "expected_reason": "ambiguous_device",
        "expected_final_intent": "device.on",
        "expected_final_params": {},
    },
]
