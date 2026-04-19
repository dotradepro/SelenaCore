"""
system_modules/llm_engine/embedding_classifier.py

Production embedding-based intent classifier — IntentRouter Tier 1.

Pipeline position
-----------------
    Vosk STT (native)
      → Helsinki translator       (always present)
      → Token filter               (3-15 candidates)
      → EmbeddingIntentClassifier  (this file, IntentRouter Tier 1)
      → if confident → return; else fall through to Local LLM (Tier 2)

The classifier is fed Helsinki English output, NOT the user's native
text. INTENT_ANCHORS therefore mix two sources:

  * Original clean English (e.g. "turn on the light in the living room")
  * Real Helsinki outputs from previous trace bench runs, which include
    quirks like "Turn on the air conditioning in the living room." or
    "What weather outside." (Helsinki sometimes drops the verb)

Anchors trained on idealised English would be miscalibrated against
the actual production translation noise. Including both forms makes
the classifier robust to whatever Helsinki produces.

Production rationale
--------------------
Trace bench on qwen 1.5b + Helsinki: 35/40 (87.5%), p50 2548 ms.
Embedding bench (all-MiniLM-L6-v2 via ONNX Runtime) on the same
40-case corpus: 39/40 (97.5%), classify-only p50 41 ms — both more
accurate AND ~60× faster than the local LLM. The model is 22 MB on
disk and ~30 MB in RAM (ONNX Runtime), vs ~1 GB for qwen 1.5b.

After integration into IntentRouter.route() with LLM fallback for
low-margin cases: 40/40 (100%), end-to-end p50 111 ms (~23× faster
than the LLM-only path).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_DIR = "/var/lib/selena/models/embedding/all-MiniLM-L6-v2"


def _get_embedding_model_dir() -> str:
    """Read the ONNX embedding model directory from config."""
    try:
        from core.config_writer import get_nested
        return str(get_nested("intent.embedding_model_dir", _DEFAULT_MODEL_DIR))
    except Exception:
        return _DEFAULT_MODEL_DIR


# ── Intent anchors ──────────────────────────────────────────────────
#
# For each intent name we list a few representative phrases. The
# classifier averages their embeddings and uses the result as a
# centroid against which incoming queries are compared.
#
# CRITICAL: anchors must include actual Helsinki outputs, not just
# clean English. Real outputs from trace_bench:
#
#   "увімкни кондиціонер у вітальні" → "Turn on the air conditioning..."
#   "встанови режим охолодження"     → "Set the cooling mode."
#   "вмикни джазове радіо"           → "Turn on the jazz radio."
#   "яка погода надворі"             → "What weather outside."
#   "замкни вхідні двері"            → "Shut the front door."
#   "розкажи анекдот"                → "Tell me the joke."
#
# Without these the classifier will perform well in unit tests but
# fall apart on production translations.
INTENT_ANCHORS: dict[str, list[str]] = {
    "device.on": [
        # Core on commands
        "turn on the light",
        "turn on the lamp",
        "turn on the air conditioner",
        "turn on the air conditioning",
        "turn on the humidifier in the bedroom",
        "turn on the camera",
        "turn on the vacuum cleaner",
        "turn on the thermometer in the garage",
        "turn on the motion sensor",
        "turn on the speaker",
        "turn on the kitchen speaker",
        "turn on the speaker in the living room",
        "turn on the smart speaker",
        "turn on the thermostat",
        "enable the thermostat",
        "switch on the thermostat",
        "turn on the AC",
        "power on the air conditioning",
        "start the air conditioner",
        "switch on the air conditioner",
        "switch on the air conditioning",
        # Vacuum: "run" / "start"
        "run the robot vacuum cleaner",
        "start the vacuum cleaner",
        # Covers (curtains/blinds) → device.on = open
        "open the curtains in the bedroom",
        "open the curtains",
        "open the blinds",
        "raise the blinds",
        "turn the curtains on in the bedroom",
        "turn on the curtains in the bedroom",
        # Brightness + color (enriched device.on)
        "turn on the lamp with orange light",
        "set the light to blue",
        "dim the bedroom light",
        "make the light brighter",
        "make a brighter lamp in the living room",
        "put the brightness of the lamp to maximum",
        "put the tape brightness at 50",
        "change the color to red",
        "make it dark in the office",
        # Helsinki artifact: "постав теплий колір" → declarative form
        "the color of the lamp is warm",
        # Helsinki outputs:
        "turn on the air conditioning in the living room.",
        "turn on the air conditioning.",
        # NOTE: removed "put the light..." — "put" collides with
        # "put out the light" in device.off, causing "загаси" to hit on.
        "turn the light in the living room.",
        "turn on the humidifier in your bedroom.",
        "turn the tape on.",
        "turn the tape on in your office.",
        "turn the camera on at the entrance.",
        "turn the thermostat on.",
        "turn on the socket in the kitchen.",
        "run the robot vacuum cleaner.",
        "open the curtains in your bedroom.",
        # Indirect "I want X on" patterns — user expresses desire, not
        # explicit command verb. Helsinki preserves these shapes from UK
        # "хочу щоб X працював".
        "I want the air conditioner on",
        "I want the air conditioner on in the living room",
        "I want the fan on in the bedroom",
        "I want the lights on",
        "I want the light on in the living room",
        "I need the heater on",
        "I want the AC working",
        # Helsinki output for UK "хочу щоб X працював" (I want X to
        # work / be operating) — the "to work" phrasing is easy for
        # MiniLM to confuse with query intents.
        "I want the air conditioner to work in the living room",
        "I want the fan to work in the bedroom",
        "I want the heater to work",
        # Noun-first short form: "X on" (Helsinki for UK "X увімкни")
        "air conditioner on",
        "the lights on",
        "fan on",
        "lamp on",
    ],
    "device.off": [
        # Core off commands
        "turn off the light",
        "turn off the lamp",
        "turn off the light in the living room",
        "turn off the air conditioner",
        "turn off the air conditioning",
        "turn off the kettle in the kitchen",
        "turn off the thermostat in the bedroom",
        "turn off the speaker",
        "turn off the speaker in the living room",
        "turn off the kitchen speaker",
        "turn off the smart speaker",
        # Covers (curtains/blinds) → device.off = close
        "close the curtains",
        "close the curtains in the bedroom",
        "close the blinds",
        "lower the blinds",
        "shut the blinds",
        # Helsinki outputs:
        "turn off the air conditioning.",
        "turn off the air conditioner.",
        "turn off the lights in the living room.",
        "turn off the light in the living room.",
        "turn off the kettle in the kitchen.",
        # Helsinki: "загаси" → "put out"
        "put out the light.",
        "put out the light",
        "put out all the lights",
        "turn the light out",
        "extinguish the light",
        "extinguish the lights",
        # Helsinki: modal verbs (можеш вимкнути)
        "you can turn off the air conditioner.",
        "you can turn off the air conditioning.",
        "could you turn off the air conditioner.",
        # Helsinki: "закрий штори"
        "close the curtains.",
        # Indirect "no need for X" / "don't need X" (Helsinki for
        # "не треба X" — declarative, no imperative verb).
        "no need for the air conditioner",
        "no need for the fan in the bedroom",
        "no need for the lights",
        "don't need the AC anymore",
        # Noun-first short form: "X off"
        "air conditioner off",
        "the lights off",
        "fan off",
        "lamp off",
    ],
    "device.set_temperature": [
        "set the air conditioner to 22 degrees",
        "set temperature to 20",
        # Helsinki outputs:
        "set the air conditioning to 22 degrees.",
        "set twenty-two degrees.",
        "set the temperature to 22 degrees in the living room",
        "set the temperature to 22 degrees in the bedroom",
        "set the temperature to 22 degrees in the bathroom",
        "set temperature to 22 in the kitchen",
        "make it 22 degrees in the living room",
        "change the temperature to 22 degrees",
    ],
    "device.set_mode": [
        # NOTE: do NOT include "air conditioner" / "thermostat" here —
        # those device words pull the centroid toward on/off AC commands
        # and cause "turn on the AC" to classify as set_mode.
        "set cool mode",
        "set heating mode",
        "set dry mode",
        "switch to heating",
        "switch to cooling",
        "switch to cool mode",
        "switch to heat mode",
        # Helsinki outputs:
        "set the cooling mode.",
        "set the draining mode.",
        "switch to heaters.",
    ],
    "device.set_fan_speed": [
        "set the fan speed to high",
        "set fan speed to low",
        # Helsinki outputs:
        "set fan speed to high.",
    ],
    "device.query_temperature": [
        # NOTE: keep anchors question-shaped ("what is...") to avoid
        # pulling imperative commands ("turn on the thermometer") here.
        "what is the temperature in the living room",
        "what is the temperature",
        "what is the current temperature",
        "how warm is it in the bedroom",
        # Helsinki outputs:
        "what is the temperature in the living room?",
        "what is the temperature on the air conditioner?",
    ],
    "device.lock": [
        "lock the front door",
        "lock the door",
        "close the lock",
        # Helsinki outputs (Helsinki tc-big-zle-en sometimes uses "shut"):
        "shut the front door.",
    ],
    "device.unlock": [
        "unlock the front door",
        "unlock the door",
        "unlock the lock",
        "open the lock",
        "open the front door",
        "open the door",
    ],
    "clock.set_timer": [
        "set a timer for ten minutes",
        "start a timer for 5 minutes",
        # Helsinki outputs:
        "set the timer to 10 minutes.",
    ],
    "clock.set_alarm": [
        "set an alarm for 7 am",
        "set an alarm for 6 in the morning",
        "wake me at 7 am",
        "wake me up at 8",
        # Helsinki outputs for UK "встанови будильник":
        "set the alarm for 7 in the morning.",
        "set alarm at 8.",
    ],
    "clock.set_reminder": [
        "remind me at 3 pm to call mom",
        "remind me to take medicine at 8",
        "set a reminder for 5 pm",
        # Helsinki outputs for UK "нагадай мені":
        "remind me at 5 o'clock in the evening.",
        "remind me to do something.",
    ],
    "clock.list_alarms": [
        "list my alarms",
        "what alarms do I have",
        "show me all the alarms",
        # Helsinki outputs for UK:
        "which alarms have been set.",
    ],
    "clock.stop_alarm": [
        "stop the alarm",
        "cancel the alarm",
        "dismiss the alarm",
        "cancel the morning alarm",
        "delete the 7am alarm",
        # Helsinki outputs for UK "вимкни будильник" / "видали будильник":
        "turn off the alarm.",
        "delete the morning alarm.",
    ],
    "clock.cancel_timer": [
        "cancel the timer",
        "stop the timer",
        "abort timer",
        # Helsinki outputs for UK "зупини таймер":
        "stop timer.",
    ],
    "clock.cancel_alarm": [
        "cancel the alarm",
        "stop the alarm",
    ],
    "media.play_genre": [
        "play jazz radio",
        "play some rock music",
        "play classical music",
        # Helsinki outputs — critical for case 22:
        "turn on the jazz radio.",
    ],
    "media.play_radio": [
        "play the radio",
        "turn on radio",
    ],
    "media.play_radio_name": [
        "play Radio Relax",
        "put on BBC Radio",
    ],
    "media.play_search": [
        "play Pink Floyd",
        "play The Beatles",
        "play music by Queen",
        "find me songs by Metallica",
        "search for 80s rock",
    ],
    "media.pause": [
        "pause the music",
        "pause",
        "pause playback",
        # Helsinki outputs:
        "put music on pause.",
        "put the music on pause.",
        "pause it.",
    ],
    "media.resume": [
        "resume the music",
        "resume",
        "resume playback",
        "continue playing",
        "unpause",
        "keep going",
        # Helsinki artifacts for UK "продовж" / "продовжи":
        "continued.",
        "continue.",
    ],
    "media.stop": [
        "stop the music",
        "stop playing",
        "stop playback",
        "stop",
        "stop the radio",
    ],
    "media.next": [
        "next track",
        "next song",
        "next",
        "skip this",
        "skip track",
        "skip this song",
    ],
    "media.previous": [
        "previous track",
        "previous song",
        "go back",
        "previous",
        "play the previous song",
    ],
    "media.volume_up": [
        "louder",
        "turn it up",
        "make it louder",
        "increase volume",
        "volume up",
    ],
    "media.volume_down": [
        "quieter",
        "softer",
        "turn it down",
        "make it quieter",
        "decrease volume",
        "volume down",
        # Helsinki artifact for UK "тихіше":
        "be quiet.",
    ],
    "media.volume_set": [
        "set volume to 50",
        "volume to 30",
        "set the volume to 80 percent",
    ],
    "media.whats_playing": [
        "what's playing",
        "what song is this",
        "what is playing",
        "what's on the radio",
    ],
    "weather.current": [
        "what is the weather outside",
        "current weather conditions",
        # Helsinki sometimes drops "is the":
        "what weather outside.",
        # Argos artifact for robustness:
        "what a weather.",
        # Noisy bench: indirect weather questions
        "is it raining",
        "is it raining outside",
        "is it cold outside",
        "is it warm outside",
    ],
    "weather.temperature": [
        "what is the current temperature outside",
        "how hot is it outside",
    ],
    "weather.forecast": [
        "what is the weather forecast",
        "weather for tomorrow",
    ],
    "privacy_on": [
        "enable privacy mode",
        "activate privacy mode",
        # Helsinki outputs:
        "turn on the privacy mode.",
        # v0.4.0 RU bench: removed "stop listening to me" / "don't
        # listen to me" — they pulled "Turn on the kitchen speaker"
        # away from device.on because both phrases live in the
        # audio/mic semantic neighbourhood. Privacy users reliably
        # say "privacy mode" explicitly, so the shorter anchors
        # aren't worth the collateral misroutes.
    ],
    "automation.list": [
        "list automations",
        "what automations do I have",
        "show me all rules",
        "list all automation rules",
        # Helsinki outputs for UK "які правила":
        "what rules are set.",
        "which rules are installed.",
    ],
    "automation.enable": [
        "enable the bedtime automation",
        "activate the morning routine",
        "enable the rule",
        "turn on the automation",
        # Helsinki outputs:
        "turn on the automation of the evening mode.",
    ],
    "automation.disable": [
        "disable the morning routine",
        "deactivate the bedtime automation",
        "disable the rule",
        "turn off the automation",
        # Helsinki outputs:
        "turn off the morning rule.",
    ],
    "presence.who_home": [
        "who is home",
        "who's home",
        "is anyone home",
        "who's here",
    ],
    "presence.check_user": [
        "is Alice home",
        "is Bob here",
        "is Peter at home",
    ],
    "weather.current": [
        "what's the weather",
        "how is the weather outside",
        "current weather",
    ],
    "privacy_off": [
        "disable privacy mode",
        "deactivate privacy mode",
        "turn off privacy mode",
    ],
    "presence.who_home": [
        "who is at home",
        "who is home right now",
        # Deliberately NOT including "who are you" — that should fall
        # to unknown via the negative anchors below.
    ],
    "unknown": [
        # Negative anchors push the unknown centroid away from device
        # intents and toward "weird stuff" / "questions about the
        # assistant itself".
        "xyzzy plover quux",
        "tell me a joke",
        "tell me the joke.",
        "who are you",
        "who are you.",
    ],
}


# ── Param extraction ────────────────────────────────────────────────
#
# Lexicon-based, runs on Helsinki English output. Includes Helsinki
# artifacts ("air conditioning", "clutch") so cases like
# "увімкни кондиціонер" still resolve entity correctly when Helsinki
# produces a non-canonical phrase.

ENTITY_MAP: dict[str, str] = {
    # ── Lights (HA: on_off_domains + expansion_rules.light) ──
    "light": "light",
    "lights": "light",
    "lighting": "light",
    "lamp": "light",
    "lamps": "light",
    "tape": "light",          # Helsinki: стрічка → tape
    "led tape": "light",      # Helsinki: світлодіодна стрічка → LED tape
    "strip": "light",
    "led strip": "light",
    # ── Climate ──
    "air conditioner": "air_conditioner",
    "air conditioning": "air_conditioner",
    "conditioner": "air_conditioner",
    "heaters": "air_conditioner",  # Helsinki: обігрів (AC heat mode) → heaters
    "heater": "radiator",          # Helsinki: обігрівач (a heating device) → heater
    "thermostat": "thermostat",
    "radiator": "radiator",
    # ── Fans (HA: on_off_domains) ──
    "fan": "fan",
    "fans": "fan",
    # ── Switches / outlets ──
    "switch": "switch",
    "switches": "switch",
    "outlet": "outlet",
    "socket": "outlet",
    "plug": "outlet",
    # ── Locks (HA: expansion_rules.lockable) ──
    "lock": "door_lock",      # canonical entity_type in registry
    "door lock": "door_lock",
    "door": "door_lock",      # Helsinki: двері → door (lock/unlock context)
    "castle": "door_lock",    # Helsinki quirk: замок → "Castle"
    "gate": "gate",
    "shutter": "shutter",
    # ── Covers (HA: cover_classes) ──
    "curtains": "curtain",
    "curtain": "curtain",
    "blinds": "curtain",
    "blind": "curtain",
    "awning": "curtain",
    "shade": "curtain",
    "shades": "curtain",
    "window": "window",
    # ── Sensors ──
    "thermometer": "sensor",
    "sensor": "sensor",
    "motion sensor": "sensor",
    "door sensor": "sensor",
    # ── Vacuum (HA: HassVacuumStart) ──
    "vacuum cleaner": "vacuum",
    "vacuum": "vacuum",
    "robot vacuum": "vacuum",
    # ── Camera ──
    "camera": "camera",
    # ── Speaker / media ──
    "speaker": "speaker",
    "column": "speaker",       # Helsinki: колонка → column
    # ── TV (distinct from media_player; controlled via device.on/off) ──
    "tv": "tv",
    "television": "tv",
    "telly": "tv",
    "tv set": "tv",
    # ── Misc ──
    "humidifier": "humidifier",
    "moisturizer": "humidifier",   # Helsinki quirk for зволожувач
    "kettle": "kettle",
    "teapot": "kettle",
    "clutch": "humidifier",   # Argos quirk for зволожувач
}

ROOM_KEYWORDS: list[str] = [
    # Standard rooms
    "living room", "bedroom", "kitchen", "bathroom",
    "hallway", "office", "garage", "balcony",
    "entrance", "corridor", "cabinet", "nightstand",
    # HA expansion_rules.home synonyms
    "nursery", "dining room", "laundry", "attic", "basement",
    "pantry", "porch", "terrace", "lobby", "closet",
]

VALUE_KEYWORDS: list[str] = [
    # Fan / mode keywords
    "high", "low", "medium", "auto",
    # Climate modes
    "cool", "heat", "dry", "eco", "turbo",
    # Helsinki translation artifacts
    "cooling", "heating", "draining",
    # HA brightness_level names
    "maximum", "minimum",
]

# Helsinki translation artifacts for mode values.
_VALUE_NORMALIZE: dict[str, str] = {
    "cooling": "cool",
    "heating": "heat",
    "draining": "dry",
    "heaters": "heat",
    "maximum": "max",
    "minimum": "min",
}

# ── Colors (from HA color list) ──
COLOR_KEYWORDS: list[str] = [
    "white", "black", "red", "orange", "yellow",
    "green", "blue", "purple", "brown", "pink", "turquoise",
]

# Color name → (hue_0-65535, saturation_0-254) for Hue/Z2M API.
# Used by device-control _enrich_state_from_params() to translate
# voice color params into driver-ready hue+saturation values.
COLOR_TO_HS: dict[str, tuple[int, int]] = {
    "red":       (0,     254),
    "orange":    (7281,  254),
    "yellow":    (10922, 254),
    "green":     (21845, 254),
    "blue":      (43690, 254),
    "purple":    (49151, 254),
    "pink":      (56173, 200),
    "turquoise": (32768, 254),
    "white":     (0,     0),     # saturation=0 → white
    "black":     (0,     0),     # effectively off
    "brown":     (5461,  200),
}

# ── Color temperature names (from HA color_temperature_names) ──
COLOR_TEMP_MAP: dict[str, int] = {
    "candle light": 1900,
    "warm white": 2700,
    "warm": 2700,
    "cold white": 4000,
    "cool white": 4000,
    "daylight": 6500,
}

GENRE_KEYWORDS: list[str] = [
    "jazz", "rock", "classical", "pop", "blues",
    "electronic", "country", "folk", "metal",
]

# Word-to-number mapping for STT output where Vosk or Helsinki
# produces spelled-out numbers ("twenty two") instead of digits.
# Sorted longest-first at lookup time so "twenty two" matches
# before "twenty".
WORD_NUMBERS: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty one": 21, "twenty two": 22, "twenty three": 23,
    "twenty four": 24, "twenty five": 25, "twenty six": 26,
    "twenty seven": 27, "twenty eight": 28, "twenty nine": 29,
    "thirty": 30,
}

_WORD_NUMBERS_SORTED = sorted(
    WORD_NUMBERS.items(), key=lambda x: -len(x[0]),
)


def _extract_numeric_value(text: str) -> str | None:
    """Extract the first numeric value from text — digit or word form.

    Digit regex runs FIRST because it's unambiguous ("22" is always
    a number). Word-number lookup runs second with word-boundary
    guards so "one" inside "air conditioner" doesn't match.

    Helsinki sometimes produces hyphenated forms ("twenty-two") so
    we normalise hyphens to spaces before the word-number scan.
    """
    import re

    q = text.lower()
    # Prefer digit form — always unambiguous.
    nums = re.findall(r"\b(\d+)\b", q)
    if nums:
        return nums[0]
    # Normalise hyphens so "twenty-two" matches "twenty two".
    q_norm = q.replace("-", " ")
    # Fallback to word-number with word boundaries.
    for phrase, num in _WORD_NUMBERS_SORTED:
        pattern = r"\b" + re.escape(phrase) + r"\b"
        if re.search(pattern, q_norm):
            return str(num)
    return None


# Skip words from HA + Helsinki modal verbs.
# Stripped before param extraction so "can you turn off" → "turn off".
_SKIP_PHRASES: tuple[str, ...] = (
    "please", "can you", "could you", "would you",
    "for me", "i'd like to", "i'd like", "i want to", "i want",
    "you can",  # Helsinki: "можеш" → "you can"
)


def _strip_skip_phrases(text: str) -> str:
    s = text
    for phrase in _SKIP_PHRASES:
        s = s.replace(phrase, " ")
    return " ".join(s.split())


def extract_params(query_en: str, intent: str) -> dict[str, Any]:
    """Lexicon-based param extraction over Helsinki English output."""
    import re

    q = _strip_skip_phrases(query_en.lower())
    params: dict[str, Any] = {}

    # Strip leading verb phrases that collide with ENTITY_MAP keys.
    # "switch on the fan" — without this the longest-match scanner
    # picks "switch" as the entity because it's 6 chars vs "fan" 3.
    # We re-lowercased already, so a leading prefix check is safe.
    q_for_entity = q
    for verb_prefix in (
        "switch on ", "switch off ", "turn on ", "turn off ",
    ):
        if q_for_entity.startswith(verb_prefix):
            q_for_entity = q_for_entity[len(verb_prefix):]
            break

    # Entity — longest substring match wins so "air conditioning"
    # beats "conditioner" beats nothing.
    matched, matched_len = None, 0
    for kw, entity_type in ENTITY_MAP.items():
        if kw in q_for_entity and len(kw) > matched_len:
            matched, matched_len = entity_type, len(kw)
    if matched:
        params["entity"] = matched

    # Location — longest match wins so "living room" beats "room"
    best_room, best_room_len = None, 0
    for room in ROOM_KEYWORDS:
        if room in q and len(room) > best_room_len:
            best_room, best_room_len = room, len(room)
    if best_room:
        params["location"] = best_room

    # Value — only relevant for set_* intents. Handles both digit
    # ("22") and word-number ("twenty two") forms from Vosk/Helsinki.
    if intent in (
        "device.set_mode",
        "device.set_fan_speed",
        "device.set_temperature",
    ):
        num_val = _extract_numeric_value(q)
        if num_val:
            params["value"] = num_val
        else:
            for v in VALUE_KEYWORDS:
                if v in q:
                    # Normalize Helsinki artifacts: "draining"→"dry", etc.
                    params["value"] = _VALUE_NORMALIZE.get(v, v)
                    break

    # Color — extracted for any device intent, applied by enrichment
    # if the resolved device supports hue/saturation.
    for c in COLOR_KEYWORDS:
        if c in q:
            params["color"] = c
            break

    # Color temperature — "warm white", "daylight", etc.
    for ct_name, ct_val in COLOR_TEMP_MAP.items():
        if ct_name in q:
            params["color_temp"] = ct_val
            break

    # Brightness — extracted for any device intent, applied by
    # enrichment if the resolved device has brightness in state.
    # "set brightness to 50", "make brighter", "dim the light"
    if any(kw in q for kw in ("bright", "dim", "brightness")):
        bri_val = _extract_numeric_value(q)
        if bri_val:
            params["brightness"] = bri_val
        elif any(kw in q for kw in ("maximum", "max", "brightest", "full")):
            params["brightness"] = "100"
        elif any(kw in q for kw in ("minimum", "min", "dimmest")):
            params["brightness"] = "1"
        elif "dim" in q:
            params["brightness"] = "25"
        elif "bright" in q:
            params["brightness"] = "75"
        elif "half" in q:
            params["brightness"] = "50"

    # Genre — only for media.play_genre
    if intent == "media.play_genre":
        for g in GENRE_KEYWORDS:
            if g in q:
                params["genre"] = g
                break

    return params


# ── Classifier ──────────────────────────────────────────────────────


@dataclass
class EmbeddingResult:
    intent: str
    score: float            # cosine similarity of the winner
    params: dict[str, Any] = field(default_factory=dict)
    runner_up: str = "unknown"
    runner_up_score: float = 0.0
    margin: float = 0.0     # winner.score - runner_up.score


class EmbeddingIntentClassifier:
    """Cosine-similarity classifier over pre-computed anchor centroids.

    Performance design
    ------------------
    The naive approach (re-encode every anchor on every classify call)
    is ~10× slower than necessary. Instead we pre-compute a single
    centroid per intent at warmup time, cache it, and on each classify
    call only embed the live query + the live description string. The
    description embedding is also cached because the catalog descriptions
    rarely change between requests.
    """

    # Display name only — the actual weights are loaded from the ONNX
    # export configured via intent.embedding_model_dir in core.yaml.
    MODEL_NAME = "all-MiniLM-L6-v2"
    UNKNOWN_THRESHOLD = 0.25   # max cosine below this → force unknown
    MARGIN_THRESHOLD = 0.003   # winner − runner_up below this → log low confidence

    def __init__(self) -> None:
        self._model = None
        # Pre-computed mean anchor embedding per intent name.
        self._anchor_cache: dict[str, np.ndarray] = {}
        # Lazy cache for description strings (filled on demand).
        self._desc_cache: dict[str, np.ndarray] = {}

    def warmup(self) -> None:
        """Load model and pre-compute anchor centroids."""
        if self._model is not None:
            return

        from system_modules.llm_engine.onnx_embedder import OnnxMiniLMEmbedder

        self._model = OnnxMiniLMEmbedder(_get_embedding_model_dir())

        # Pre-compute mean anchor embedding for every known intent.
        for intent_name, anchors in INTENT_ANCHORS.items():
            if not anchors:
                continue
            embs = self._model.encode(anchors, normalize_embeddings=True)
            mean = embs.mean(axis=0)
            mean = mean / np.linalg.norm(mean)
            self._anchor_cache[intent_name] = mean

        logger.info(
            "EmbeddingIntentClassifier: warmed up, %d anchor centroids",
            len(self._anchor_cache),
        )

    def _desc_embedding(self, description: str) -> np.ndarray:
        """Cache-or-encode a description string."""
        cached = self._desc_cache.get(description)
        if cached is not None:
            return cached
        emb = self._model.encode(description, normalize_embeddings=True)
        # Already normalized by sentence-transformers, but be defensive.
        emb = emb / np.linalg.norm(emb)
        self._desc_cache[description] = emb
        return emb

    def _intent_centroid(self, name: str, description: str) -> np.ndarray:
        """Combine pre-cached anchor centroid with live description embedding.

        If the intent has no anchors (one of the rare ones added by a
        module after this file was last updated), fall back to using
        the description alone.
        """
        anchor = self._anchor_cache.get(name)
        desc = self._desc_embedding(description)
        if anchor is None:
            return desc
        combined = anchor + desc
        return combined / np.linalg.norm(combined)

    def classify(
        self,
        query_en: str,
        candidates: list[dict[str, str]],
    ) -> EmbeddingResult:
        """Classify a single Helsinki-English query against the filtered catalog.

        ``candidates`` is the per-utterance shortlist already produced by
        ``IntentRouter._build_filtered_catalog``: a list of
        ``{"name": str, "description": str}`` dicts.
        """
        self.warmup()

        if not candidates:
            return EmbeddingResult(intent="unknown", score=0.0)

        query_emb = self._model.encode(query_en, normalize_embeddings=True)

        names: list[str] = []
        scores: list[float] = []
        for c in candidates:
            name = c["name"]
            desc = c.get("description", "")
            centroid = self._intent_centroid(name, desc)
            names.append(name)
            scores.append(float(np.dot(query_emb, centroid)))

        # Sort by score descending so we can read winner + runner-up
        order = sorted(range(len(scores)), key=lambda i: -scores[i])
        best_idx = order[0]
        best_intent = names[best_idx]
        best_score = scores[best_idx]

        if len(order) > 1:
            ru_idx = order[1]
            runner_up = names[ru_idx]
            runner_up_score = scores[ru_idx]
        else:
            runner_up = "unknown"
            runner_up_score = 0.0
        margin = best_score - runner_up_score

        # Hard threshold: if even the best match is weak, fall back.
        if best_score < self.UNKNOWN_THRESHOLD:
            best_intent = "unknown"

        params = extract_params(query_en, best_intent)

        return EmbeddingResult(
            intent=best_intent,
            score=best_score,
            params=params,
            runner_up=runner_up,
            runner_up_score=runner_up_score,
            margin=margin,
        )
