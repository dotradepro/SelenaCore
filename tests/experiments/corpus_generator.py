"""tests/experiments/corpus_generator.py — build bench corpus from the live registry.

For every (entity_type, location) combo present in the current registry,
generate a spread of utterances in English and Ukrainian covering:

  plain        — canonical phrasing
  variety      — synonyms / polite / short / indirect / casual
  noise        — filler / stutter / typo / context / long
  ambiguous    — same intent without a room (for needs_location path)
  distractor   — nonsense / chat-fallback phrases

Each generated case is a ``dict`` with:
    {
        "lang": "en" | "uk",
        "native": "<what the user would say>",
        "exp_intent": "device.on" | ... | "unknown",
        "exp_entity": "light" | ... | None,
        "exp_location": "bedroom" | ... | None,
        "category": "plain" | "variety" | "noise" | "ambiguous" | "distractor",
        "twist": "syn" | "polite" | "short" | "indirect" | "casual" | None,
        "noise": "filler" | "typo" | "stutter" | "context" | "long" | None,
    }

The generator is deterministic — same registry state → same corpus.
Run from inside the selena-core container:

    docker exec -t selena-core python3 -c \\
        "from tests.experiments.corpus_generator import generate; \\
         import json, sys; json.dump(generate(), sys.stdout)"
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


# ── Vocabulary ─────────────────────────────────────────────────────────

# entity_type → (EN nouns, UK nouns). First noun is the default in templates.
ENTITY_VOCAB: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "light":          (("light", "lamp"),                      ("світло", "лампа")),
    "switch":         (("switch",),                            ("перемикач",)),
    "outlet":         (("outlet", "plug", "socket"),           ("розетка", "розетку")),
    "fan":            (("fan",),                               ("вентилятор",)),
    "air_conditioner":(("air conditioner", "AC"),              ("кондиціонер",)),
    "thermostat":     (("thermostat",),                        ("термостат",)),
    "humidifier":     (("humidifier",),                        ("зволожувач",)),
    "kettle":         (("kettle",),                            ("чайник",)),
    "speaker":        (("speaker",),                           ("динамік", "колонку")),
    "tv":             (("TV", "television"),                   ("телевізор",)),
    "curtain":        (("curtains", "blinds"),                 ("штори", "жалюзі")),
    "radiator":       (("radiator", "heater"),                 ("радіатор",)),
    "vacuum":         (("vacuum", "vacuum cleaner"),           ("пилосос",)),
    "media_player":   (("media player",),                      ("медіаплеєр",)),
    "door_lock":      (("door", "lock", "door lock"),          ("двері", "замок")),
    # Skip read-only types in bench: sensor, camera.
}

ROOM_UK_TO_EN: dict[str, str] = {
    "спальня":  "bedroom",
    "кухня":    "kitchen",
    "вітальня": "living room",
    "кабінет":  "office",
    "ванна":    "bathroom",
    # fallthrough — return as-is
}

# Ukrainian locative-case forms for room names (used in prepositional
# phrase "у/в <room_locative>"). The registry stores nominative
# ("спальня"), but a natural utterance uses locative ("у спальні"). If
# this table doesn't have a form, we fall back to the nominative which
# Helsinki still translates reasonably.
ROOM_UK_LOCATIVE: dict[str, str] = {
    "спальня":   "спальні",
    "кухня":     "кухні",
    "вітальня":  "вітальні",
    "кабінет":   "кабінеті",
    "ванна":     "ванній",
}


def _room_en(uk: str | None) -> str | None:
    if not uk:
        return None
    return ROOM_UK_TO_EN.get(uk, uk)


# ── Templates ──────────────────────────────────────────────────────────

# Each template is a tuple (twist_name, phrase). {e} = entity noun,
# {l} = location (EN or UK form appropriate for the language),
# {l_with_prep} = language-specific prepositional phrase like "in the
# kitchen" / "у кухні".

EN_TEMPLATES: dict[str, list[tuple[str | None, str]]] = {
    "on": [
        (None,         "turn on the {e} {l_with_prep}"),
        ("syn",        "switch on the {e} {l_with_prep}"),
        ("polite",     "could you turn on the {e} {l_with_prep} please"),
        ("short",      "{e} on"),
        ("indirect",   "I want the {e} on {l_with_prep}"),
        ("casual",     "flip on the {e} {l_with_prep}"),
    ],
    "off": [
        (None,         "turn off the {e} {l_with_prep}"),
        ("syn",        "switch off the {e} {l_with_prep}"),
        ("polite",     "please turn off the {e} {l_with_prep}"),
        ("short",      "{e} off"),
        ("indirect",   "no need for the {e} {l_with_prep}"),
        ("casual",     "kill the {e} {l_with_prep}"),
    ],
    "lock": [
        (None,         "lock the {e} {l_with_prep}"),
        ("syn",        "secure the {e} {l_with_prep}"),
        ("polite",     "could you lock the {e} {l_with_prep}"),
    ],
    "unlock": [
        (None,         "unlock the {e} {l_with_prep}"),
        ("syn",        "open the {e} {l_with_prep}"),
    ],
    "set_temperature": [
        (None,         "set the temperature to 22 degrees {l_with_prep}"),
        ("syn",        "make it 22 degrees {l_with_prep}"),
    ],
    "set_mode": [
        (None,         "set {e} to cool mode {l_with_prep}"),
    ],
}

UK_TEMPLATES: dict[str, list[tuple[str | None, str]]] = {
    "on": [
        (None,         "увімкни {e} {l_with_prep}"),
        ("syn",        "включи {e} {l_with_prep}"),
        ("polite",     "увімкни {e} {l_with_prep} будь ласка"),
        ("short",      "{e} увімкни"),
        ("indirect",   "хочу щоб {e} працював {l_with_prep}"),
        ("casual",     "запали {e} {l_with_prep}"),
    ],
    "off": [
        (None,         "вимкни {e} {l_with_prep}"),
        ("syn",        "виключи {e} {l_with_prep}"),
        ("polite",     "вимкни {e} {l_with_prep} будь ласка"),
        ("short",      "{e} вимкни"),
        ("indirect",   "не треба {e} {l_with_prep}"),
        ("casual",     "погаси {e} {l_with_prep}"),
    ],
    "lock": [
        (None,         "замкни {e} {l_with_prep}"),
        ("syn",        "зачини {e} {l_with_prep}"),
    ],
    "unlock": [
        (None,         "відімкни {e} {l_with_prep}"),
        ("syn",        "відчини {e} {l_with_prep}"),
    ],
    "set_temperature": [
        (None,         "встанови температуру на 22 градуси {l_with_prep}"),
        ("syn",        "хочу 22 градуси {l_with_prep}"),
    ],
    "set_mode": [
        (None,         "встанови режим охолодження {l_with_prep}"),
    ],
}


# Noise wrappers — each takes a phrase and returns a noised version.
EN_NOISE: dict[str, callable] = {
    "filler":  lambda p: f"um {p} uh",
    "stutter": lambda p: p.replace("turn ", "turn turn ", 1)
                          .replace("switch ", "switch switch ", 1)
                          .replace("lock ", "lock lock ", 1)
                          if any(w in p for w in ("turn ", "switch ", "lock "))
                          else f"{p.split()[0]} {p}",
    "typo":    lambda p: p.replace("turn", "turnn").replace("the ", "teh ", 1)
                          .replace("please", "pleaze"),
    "context": lambda p: f"hey, {p}",
    "long":    lambda p: f"I just got home and {p}",
}

UK_NOISE: dict[str, callable] = {
    "filler":  lambda p: f"ну {p}",
    "stutter": lambda p: p.replace("увімкни ", "увімкни увімкни ", 1)
                          .replace("вимкни ", "вимкни вимкни ", 1)
                          .replace("замкни ", "замкни замкни ", 1)
                          if any(w in p for w in ("увімкни ", "вимкни ", "замкни "))
                          else f"слухай {p}",
    "typo":    lambda p: p.replace("увімкни", "увімкніи").replace("ласка", "лска"),
    "context": lambda p: f"слухай {p}",
    "long":    lambda p: f"я щойно прийшов додому {p}",
}


# ── Per-entity intent mapping ──────────────────────────────────────────

# What intents each entity_type naturally accepts. Order matters —
# first intent is the "representative" one driving most of its corpus.
INTENTS_PER_ENTITY: dict[str, list[str]] = {
    "light":          ["on", "off"],
    "switch":         ["on", "off"],
    "outlet":         ["on", "off"],
    "fan":            ["on", "off"],
    "air_conditioner":["on", "off", "set_temperature", "set_mode"],
    "thermostat":     ["set_temperature"],
    "humidifier":     ["on", "off"],
    "kettle":         ["on", "off"],
    "tv":             ["on", "off"],
    "curtain":        ["on", "off"],  # on=open, off=close semantically
    "radiator":       ["on", "off"],
    "vacuum":         ["on", "off"],
    "door_lock":      ["lock", "unlock"],
    # speaker + media_player are controlled via media.* namespace
    # (media.play_genre / media.pause / media.volume_*) which lives in
    # the media-player module — out of scope for this coverage bench
    # that targets device-control intents. Skip them here.
}


def _intent_id(verb: str) -> str:
    """Map short verb → canonical intent string."""
    return {
        "on":              "device.on",
        "off":             "device.off",
        "lock":            "device.lock",
        "unlock":          "device.unlock",
        "set_temperature": "device.set_temperature",
        "set_mode":        "device.set_mode",
    }[verb]


def _location_prep(lang: str, room_en: str | None, room_uk: str | None) -> str:
    """Return the prepositional-phrase part for a given language."""
    if lang == "en":
        return f"in the {room_en}" if room_en else ""
    # UK — use "у/в" based on following letter (cheap approximation)
    # AND use the locative case form if we have one. Nominative case
    # ("у вітальня") is ungrammatical and Helsinki mistranslates it —
    # locative ("у вітальні") translates to clean English.
    if not room_uk:
        return ""
    room_loc = ROOM_UK_LOCATIVE.get(room_uk, room_uk)
    first = room_loc[0].lower()
    prep = "в" if first in "аеиоуяію" else "у"
    return f"{prep} {room_loc}"


def _entity_nouns(lang: str, et: str) -> tuple[str, ...]:
    vocab = ENTITY_VOCAB.get(et)
    if vocab is None:
        return ()
    return vocab[0] if lang == "en" else vocab[1]


def _rendered(lang: str, phrase: str, et: str, room_en: str | None, room_uk: str | None) -> str:
    nouns = _entity_nouns(lang, et)
    if not nouns:
        return ""
    e = nouns[0]
    l = (room_en if lang == "en" else room_uk) or ""
    lp = _location_prep(lang, room_en, room_uk)
    return phrase.format(e=e, l=l, l_with_prep=lp).strip().replace("  ", " ").rstrip()


# ── Corpus assembly ────────────────────────────────────────────────────


def _yield_cases_for_combo(
    et: str,
    room_uk: str | None,
    langs: tuple[str, ...] = ("en", "uk"),
) -> list[dict[str, Any]]:
    """Generate variety + noise cases for one (entity_type, location)."""
    room_en = _room_en(room_uk)
    cases: list[dict[str, Any]] = []
    intents = INTENTS_PER_ENTITY.get(et, [])

    for verb in intents:
        templates_by_lang = {"en": EN_TEMPLATES, "uk": UK_TEMPLATES}
        noise_by_lang = {"en": EN_NOISE, "uk": UK_NOISE}
        for lang in langs:
            templates = templates_by_lang[lang].get(verb, [])
            if not templates:
                continue

            # Variety: emit every twist (plain = twist=None)
            for twist, phrase in templates:
                native = _rendered(lang, phrase, et, room_en, room_uk)
                if not native:
                    continue
                # If the template literally omits the location placeholder
                # (e.g. "short" twist: "{e} on"), the rendered phrase has
                # no room — so the classifier has no chance of returning
                # one. Mark exp_location=None so scoring isn't unfair.
                has_loc = ("{l}" in phrase) or ("{l_with_prep}" in phrase)
                exp_loc = (room_en if room_en and has_loc else None)
                cases.append({
                    "lang": lang, "native": native,
                    "exp_intent": _intent_id(verb),
                    "exp_entity": et,
                    "exp_location": exp_loc,
                    "category": "plain" if twist is None else "variety",
                    "twist": twist,
                    "noise": None,
                })

            # Noise: apply each noise wrapper to the plain template
            plain_template = next(
                (p for t, p in templates if t is None), None,
            )
            if plain_template:
                plain = _rendered(lang, plain_template, et, room_en, room_uk)
                for noise_name, fn in noise_by_lang[lang].items():
                    try:
                        noisy = fn(plain)
                    except Exception:
                        continue
                    if not noisy or noisy == plain:
                        continue
                    cases.append({
                        "lang": lang, "native": noisy,
                        "exp_intent": _intent_id(verb),
                        "exp_entity": et,
                        "exp_location": room_en if room_en else None,
                        "category": "noise",
                        "twist": None,
                        "noise": noise_name,
                    })

            # Ambiguous: same verb but no room — used to exercise the
            # needs_location path. Only emit once per (entity, lang).
            for twist, phrase in templates[:1]:
                native = _rendered(lang, phrase, et, None, None)
                if not native:
                    continue
                cases.append({
                    "lang": lang, "native": native,
                    "exp_intent": _intent_id(verb),
                    "exp_entity": et,
                    "exp_location": None,
                    "category": "ambiguous",
                    "twist": twist,
                    "noise": None,
                })

    return cases


# Whole-house / whole-room on-off cases. No specific entity —
# these must route to house.all_on / house.all_off (with optional
# location). Currently no intent covers this — all cases fail
# until the house.* intents are added.
ALL_OFF_CASES: list[dict[str, Any]] = [
    # EN: no location
    {"lang": "en", "native": "turn off everything",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": None,
     "category": "all_off", "twist": None, "noise": None},
    {"lang": "en", "native": "turn everything off",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": None,
     "category": "all_off", "twist": "syn", "noise": None},
    {"lang": "en", "native": "turn all the lights off",
     "exp_intent": "house.all_off", "exp_entity": "light", "exp_location": None,
     "category": "all_off", "twist": "syn", "noise": None},
    {"lang": "en", "native": "shut everything down",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": None,
     "category": "all_off", "twist": "syn", "noise": None},
    # EN: with location
    {"lang": "en", "native": "turn off everything in the kitchen",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": "kitchen",
     "category": "all_off", "twist": None, "noise": None},
    {"lang": "en", "native": "turn off all in the bedroom",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": "bedroom",
     "category": "all_off", "twist": "short", "noise": None},
    # UK: no location
    {"lang": "uk", "native": "вимкни все",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": None,
     "category": "all_off", "twist": None, "noise": None},
    {"lang": "uk", "native": "вимкни всі прилади",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": None,
     "category": "all_off", "twist": "syn", "noise": None},
    {"lang": "uk", "native": "виключи все в будинку",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": None,
     "category": "all_off", "twist": "syn", "noise": None},
    # UK: with location
    {"lang": "uk", "native": "вимкни все у кухні",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": "kitchen",
     "category": "all_off", "twist": None, "noise": None},
    {"lang": "uk", "native": "вимкни все у спальні",
     "exp_intent": "house.all_off", "exp_entity": None, "exp_location": "bedroom",
     "category": "all_off", "twist": None, "noise": None},
]

ALL_ON_CASES: list[dict[str, Any]] = [
    # Less common than all-off but reasonable
    {"lang": "en", "native": "turn on all the lights",
     "exp_intent": "house.all_on", "exp_entity": "light", "exp_location": None,
     "category": "all_on", "twist": None, "noise": None},
    {"lang": "en", "native": "turn on all the lights in the kitchen",
     "exp_intent": "house.all_on", "exp_entity": "light", "exp_location": "kitchen",
     "category": "all_on", "twist": None, "noise": None},
    {"lang": "uk", "native": "увімкни все світло у кухні",
     "exp_intent": "house.all_on", "exp_entity": "light", "exp_location": "kitchen",
     "category": "all_on", "twist": None, "noise": None},
    {"lang": "uk", "native": "включи все світло",
     "exp_intent": "house.all_on", "exp_entity": "light", "exp_location": None,
     "category": "all_on", "twist": "syn", "noise": None},
]


# Media playback control. Exercises media.play_* / pause / resume /
# stop / next / previous / volume_*. Some cases are bare verbs without
# entity — they work correctly only if media-player has a notion of
# "last active session" (currently: handler picks the only registered
# media device, which is fragile with 2+ speakers / TVs).
MEDIA_CASES: list[dict[str, Any]] = [
    # Play by genre / station / search
    {"lang": "en", "native": "play jazz",
     "exp_intent": "media.play_genre", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "en", "native": "play some classical music",
     "exp_intent": "media.play_genre", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "casual", "noise": None},
    {"lang": "en", "native": "play Radio Relax",
     "exp_intent": "media.play_radio_name", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "en", "native": "play BBC",
     "exp_intent": "media.play_radio_name", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "uk", "native": "постав джаз",
     "exp_intent": "media.play_genre", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "uk", "native": "вмикни Радіо Рокс",
     "exp_intent": "media.play_radio_name", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    # Pause / resume / stop — bare verb, no entity
    {"lang": "en", "native": "pause",
     "exp_intent": "media.pause", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "en", "native": "pause the music",
     "exp_intent": "media.pause", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "en", "native": "resume",
     "exp_intent": "media.resume", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "en", "native": "stop playing",
     "exp_intent": "media.stop", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "uk", "native": "постав на паузу",
     "exp_intent": "media.pause", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "uk", "native": "продовж",
     "exp_intent": "media.resume", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    # Next / previous — bare
    {"lang": "en", "native": "next",
     "exp_intent": "media.next", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "short", "noise": None},
    {"lang": "en", "native": "next track",
     "exp_intent": "media.next", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "en", "native": "previous song",
     "exp_intent": "media.previous", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "en", "native": "skip this",
     "exp_intent": "media.next", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "syn", "noise": None},
    {"lang": "uk", "native": "наступна",
     "exp_intent": "media.next", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "uk", "native": "попередня пісня",
     "exp_intent": "media.previous", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    # Volume — bare and with level
    {"lang": "en", "native": "louder",
     "exp_intent": "media.volume_up", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "short", "noise": None},
    {"lang": "en", "native": "turn it up",
     "exp_intent": "media.volume_up", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "casual", "noise": None},
    {"lang": "en", "native": "quieter",
     "exp_intent": "media.volume_down", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "short", "noise": None},
    {"lang": "en", "native": "volume to 50",
     "exp_intent": "media.volume_set", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "uk", "native": "голосніше",
     "exp_intent": "media.volume_up", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "short", "noise": None},
    {"lang": "uk", "native": "тихіше",
     "exp_intent": "media.volume_down", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": "short", "noise": None},
    # What's playing
    {"lang": "en", "native": "what's playing",
     "exp_intent": "media.whats_playing", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
    {"lang": "uk", "native": "що грає",
     "exp_intent": "media.whats_playing", "exp_entity": None, "exp_location": None,
     "category": "media", "twist": None, "noise": None},
]


# ── Cross-module coverage ─────────────────────────────────────────────
# The lists below exercise every currently-declared intent that has
# zero hand-written test coverage. Pure intent-match scoring — no
# entity/location expected because these intents are typically
# parameterless queries (who_home, weather.current, automation.status,
# energy.today), or take freetext args the classifier isn't expected
# to extract.

CLOCK_CASES: list[dict[str, Any]] = [
    # set_alarm
    {"lang": "en", "native": "set an alarm for 7 am",
     "exp_intent": "clock.set_alarm", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    {"lang": "uk", "native": "встанови будильник на сьому ранку",
     "exp_intent": "clock.set_alarm", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    # set_timer
    {"lang": "en", "native": "set a timer for 10 minutes",
     "exp_intent": "clock.set_timer", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    {"lang": "uk", "native": "таймер на 10 хвилин",
     "exp_intent": "clock.set_timer", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    # set_reminder
    {"lang": "en", "native": "remind me at 3 pm to call mom",
     "exp_intent": "clock.set_reminder", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    {"lang": "uk", "native": "нагадай мені о п'ятій вечора",
     "exp_intent": "clock.set_reminder", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    # list_alarms
    {"lang": "en", "native": "list my alarms",
     "exp_intent": "clock.list_alarms", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    {"lang": "uk", "native": "які будильники встановлено",
     "exp_intent": "clock.list_alarms", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    # cancel_alarm
    {"lang": "en", "native": "cancel the morning alarm",
     "exp_intent": "clock.cancel_alarm", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    {"lang": "uk", "native": "видали ранковий будильник",
     "exp_intent": "clock.cancel_alarm", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    # stop_alarm
    {"lang": "en", "native": "stop the alarm",
     "exp_intent": "clock.stop_alarm", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    {"lang": "uk", "native": "вимкни будильник",
     "exp_intent": "clock.stop_alarm", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    # cancel_timer
    {"lang": "en", "native": "cancel the timer",
     "exp_intent": "clock.cancel_timer", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
    {"lang": "uk", "native": "зупини таймер",
     "exp_intent": "clock.cancel_timer", "exp_entity": None, "exp_location": None,
     "category": "clock", "twist": None, "noise": None},
]

WEATHER_CASES: list[dict[str, Any]] = [
    {"lang": "en", "native": "what's the weather",
     "exp_intent": "weather.current", "exp_entity": None, "exp_location": None,
     "category": "weather", "twist": None, "noise": None},
    {"lang": "uk", "native": "яка сьогодні погода",
     "exp_intent": "weather.current", "exp_entity": None, "exp_location": None,
     "category": "weather", "twist": None, "noise": None},
    {"lang": "en", "native": "will it rain tomorrow",
     "exp_intent": "weather.forecast", "exp_entity": None, "exp_location": None,
     "category": "weather", "twist": None, "noise": None},
    {"lang": "uk", "native": "завтра буде дощ",
     "exp_intent": "weather.forecast", "exp_entity": None, "exp_location": None,
     "category": "weather", "twist": None, "noise": None},
    {"lang": "en", "native": "what's the temperature outside",
     "exp_intent": "weather.temperature", "exp_entity": None, "exp_location": None,
     "category": "weather", "twist": None, "noise": None},
    {"lang": "uk", "native": "яка температура надворі",
     "exp_intent": "weather.temperature", "exp_entity": None, "exp_location": None,
     "category": "weather", "twist": None, "noise": None},
]

PRESENCE_CASES: list[dict[str, Any]] = [
    {"lang": "en", "native": "who's home",
     "exp_intent": "presence.who_home", "exp_entity": None, "exp_location": None,
     "category": "presence", "twist": None, "noise": None},
    {"lang": "uk", "native": "хто в домі",
     "exp_intent": "presence.who_home", "exp_entity": None, "exp_location": None,
     "category": "presence", "twist": None, "noise": None},
    {"lang": "en", "native": "is Alice home",
     "exp_intent": "presence.check_user", "exp_entity": None, "exp_location": None,
     "category": "presence", "twist": None, "noise": None},
    {"lang": "uk", "native": "чи Петро вдома",
     "exp_intent": "presence.check_user", "exp_entity": None, "exp_location": None,
     "category": "presence", "twist": None, "noise": None},
    {"lang": "en", "native": "house status",
     "exp_intent": "presence.status", "exp_entity": None, "exp_location": None,
     "category": "presence", "twist": None, "noise": None},
    {"lang": "uk", "native": "стан будинку",
     "exp_intent": "presence.status", "exp_entity": None, "exp_location": None,
     "category": "presence", "twist": None, "noise": None},
]

AUTOMATION_CASES: list[dict[str, Any]] = [
    {"lang": "en", "native": "list automations",
     "exp_intent": "automation.list", "exp_entity": None, "exp_location": None,
     "category": "automation", "twist": None, "noise": None},
    {"lang": "uk", "native": "які правила встановлено",
     "exp_intent": "automation.list", "exp_entity": None, "exp_location": None,
     "category": "automation", "twist": None, "noise": None},
    {"lang": "en", "native": "enable the bedtime automation",
     "exp_intent": "automation.enable", "exp_entity": None, "exp_location": None,
     "category": "automation", "twist": None, "noise": None},
    {"lang": "uk", "native": "увімкни автоматизацію вечірнього режиму",
     "exp_intent": "automation.enable", "exp_entity": None, "exp_location": None,
     "category": "automation", "twist": None, "noise": None},
    {"lang": "en", "native": "disable the morning routine",
     "exp_intent": "automation.disable", "exp_entity": None, "exp_location": None,
     "category": "automation", "twist": None, "noise": None},
    {"lang": "uk", "native": "вимкни ранкове правило",
     "exp_intent": "automation.disable", "exp_entity": None, "exp_location": None,
     "category": "automation", "twist": None, "noise": None},
    {"lang": "en", "native": "automation status",
     "exp_intent": "automation.status", "exp_entity": None, "exp_location": None,
     "category": "automation", "twist": None, "noise": None},
]

SYSTEM_CASES: list[dict[str, Any]] = [
    # device-watchdog
    {"lang": "en", "native": "which devices are offline",
     "exp_intent": "watchdog.status", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "які прилади офлайн",
     "exp_intent": "watchdog.status", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "en", "native": "scan the devices",
     "exp_intent": "watchdog.scan", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    # energy-monitor
    {"lang": "en", "native": "current power usage",
     "exp_intent": "energy.current", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "скільки зараз ват",
     "exp_intent": "energy.current", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "en", "native": "energy consumed today",
     "exp_intent": "energy.today", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "скільки електроенергії сьогодні",
     "exp_intent": "energy.today", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    # voice-core privacy
    {"lang": "en", "native": "enable privacy mode",
     "exp_intent": "privacy_on", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "увімкни режим приватності",
     "exp_intent": "privacy_on", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "en", "native": "disable privacy mode",
     "exp_intent": "privacy_off", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "вимкни режим приватності",
     "exp_intent": "privacy_off", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    # device-control gaps: query_temperature (indoor, distinct from weather.temperature)
    {"lang": "en", "native": "what's the temperature in the living room",
     "exp_intent": "device.query_temperature", "exp_entity": None, "exp_location": "living room",
     "category": "system", "twist": None, "noise": None},
    {"lang": "en", "native": "how warm is it in the bedroom",
     "exp_intent": "device.query_temperature", "exp_entity": None, "exp_location": "bedroom",
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "яка температура у вітальні",
     "exp_intent": "device.query_temperature", "exp_entity": None, "exp_location": "living room",
     "category": "system", "twist": None, "noise": None},
    # device-control gaps: set_fan_speed
    {"lang": "en", "native": "set the fan speed to high",
     "exp_intent": "device.set_fan_speed", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "en", "native": "fan speed low",
     "exp_intent": "device.set_fan_speed", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "встанови швидкість вентилятора на високу",
     "exp_intent": "device.set_fan_speed", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    # media-player gaps: play_search, shuffle_toggle
    {"lang": "en", "native": "play Pink Floyd",
     "exp_intent": "media.play_search", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "uk", "native": "пустити Бітлз",
     "exp_intent": "media.play_search", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
    {"lang": "en", "native": "shuffle the tracks",
     "exp_intent": "media.shuffle_toggle", "exp_entity": None, "exp_location": None,
     "category": "system", "twist": None, "noise": None},
]


DISTRACTORS: list[dict[str, Any]] = [
    # Pure chat / nonsense — classifier must NOT produce a device intent.
    {"lang": "en", "native": "what is the meaning of life",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": None},
    {"lang": "en", "native": "tell me a joke",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": None},
    {"lang": "en", "native": "who are you",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": None},
    {"lang": "en", "native": "xyzzy plover quux",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": "garbled"},
    {"lang": "en", "native": "asdf qwerty",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": "garbled"},
    {"lang": "uk", "native": "розкажи анекдот",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": None},
    {"lang": "uk", "native": "як справи",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": None},
    {"lang": "uk", "native": "хто ти",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": None},
    {"lang": "uk", "native": "що ти вмієш",
     "exp_intent": "unknown", "exp_entity": None, "exp_location": None,
     "category": "distractor", "twist": None, "noise": None},
]


async def generate() -> list[dict[str, Any]]:
    """Build the full corpus from the current registry."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import (
        AsyncSession, async_sessionmaker, create_async_engine,
    )

    from core.registry.models import Device

    db = "/var/lib/selena/selena.db"
    if not Path(db).is_file():
        db = "/var/lib/selena/db/selena.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    combos: set[tuple[str, str | None]] = set()
    async with Session() as session:
        rows = (await session.execute(select(Device))).scalars().all()
        for d in rows:
            et = (d.entity_type or "").strip()
            if not et or et not in ENTITY_VOCAB:
                continue
            combos.add((et, d.location or None))

    cases: list[dict[str, Any]] = []
    for et, loc in sorted(combos, key=lambda c: (c[0], c[1] or "")):
        cases.extend(_yield_cases_for_combo(et, loc))
    cases.extend(ALL_OFF_CASES)
    cases.extend(ALL_ON_CASES)
    cases.extend(MEDIA_CASES)
    cases.extend(CLOCK_CASES)
    cases.extend(WEATHER_CASES)
    cases.extend(PRESENCE_CASES)
    cases.extend(AUTOMATION_CASES)
    cases.extend(SYSTEM_CASES)
    cases.extend(DISTRACTORS)
    return cases


if __name__ == "__main__":
    corpus = asyncio.run(generate())
    print(json.dumps(corpus, ensure_ascii=False, indent=2))
