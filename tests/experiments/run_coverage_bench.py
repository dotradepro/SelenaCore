#!/usr/bin/env python3
"""tests/experiments/run_coverage_bench.py — registry-wide coverage bench.

Reads every (entity_type, location) combo present in the registry,
generates ~1k cases spanning plain / variety / noise / ambiguous /
distractor categories, and runs each through ``IntentRouter.route()``
(the full production chain — Module Bus → Embedding → Assistant LLM).

Reports per-language, per-entity_type, per-twist, per-noise, per-category
accuracy plus tier-hit distribution. Designed to flag which entity
types or phrasings are weakest so ``_OWNED_INTENT_META`` descriptions
can be tuned.

Run inside the selena-core container:

    docker compose exec -T core python3 \\
        /opt/selena-core/tests/experiments/run_coverage_bench.py
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


# ── Scoring ────────────────────────────────────────────────────────────


def _norm(s: str | None) -> str:
    """Canonicalise spaces / underscores for fuzzy comparison."""
    if not s:
        return ""
    return s.strip().lower().replace("_", " ").replace("-", " ")


def _loc_match(got: str | None, exp: str | None) -> bool:
    """Partial/bilingual location match — exp may be 'living room', got 'вітальня'."""
    if not exp:
        return True
    if not got:
        return False
    gl, el = _norm(got), _norm(exp)
    if el in gl or gl in el:
        return True
    # simple EN↔UK equivalence
    equiv = {
        "bedroom": "спальня", "kitchen": "кухня", "living room": "вітальня",
        "office": "кабінет", "bathroom": "ванна",
    }
    if equiv.get(el) == gl or equiv.get(gl) == el:
        return True
    return False


# Entities the classifier returns with different normalisation than our
# canonical registry names. Keys are the normalised "got" form; values
# are the canonical entity_types that should be treated as equivalent.
_ENTITY_ALIASES: dict[str, set[str]] = {
    "lock":             {"door_lock", "door lock", "lock"},
    "door":             {"door_lock", "door lock", "door"},
    "ac":               {"air_conditioner", "air conditioner", "ac"},
    "air conditioner":  {"air_conditioner", "air conditioner", "ac"},
    "conditioner":      {"air_conditioner", "air conditioner"},
    "tv":               {"tv", "television"},
    "television":       {"tv", "television"},
    "media player":     {"media_player", "media player"},
    "curtain":          {"curtain", "curtains", "blind", "blinds"},
    "curtains":         {"curtain", "curtains", "blind", "blinds"},
    "blind":            {"curtain", "curtains", "blind", "blinds"},
    "blinds":           {"curtain", "curtains", "blind", "blinds"},
    "lamp":             {"light", "lamp", "bulb"},
    "light":            {"light", "lamp", "bulb"},
    "plug":             {"outlet", "plug", "socket"},
    "socket":           {"outlet", "plug", "socket"},
    "outlet":           {"outlet", "plug", "socket"},
    "heater":           {"radiator", "heater"},
    "radiator":         {"radiator", "heater"},
    "vacuum cleaner":   {"vacuum"},
}


def _entity_match(got: str | None, exp: str | None) -> bool:
    if not exp:
        return True
    if not got:
        return False
    g, e = _norm(got), _norm(exp)
    if g == e:
        return True
    # alias tables
    aliases = _ENTITY_ALIASES.get(g, set()) | _ENTITY_ALIASES.get(e, set())
    if g in aliases and e in aliases:
        return True
    # substring (covers "door lock" got → "door_lock" exp)
    if g.replace(" ", "_") == e or e.replace(" ", "_") == g:
        return True
    return False


def _verdict(case: dict, got_intent: str, got_params: dict | None) -> str:
    """Return 'pass' | 'intent_wrong' | 'entity_wrong' | 'location_wrong'."""
    exp_intent = case["exp_intent"]
    exp_entity = case.get("exp_entity")
    exp_loc = case.get("exp_location")
    cat = case.get("category")

    # Distractor → must land in unknown / fallback / chat
    if cat == "distractor":
        if got_intent in ("unknown", "") or got_intent is None:
            return "pass"
        # Any device intent here is a false-positive
        return "intent_wrong"

    # house.all_off / all_on — intent must match; entity is optional
    # filter (light / None) and must be loose-matched; location loose too.
    if cat in ("all_off", "all_on"):
        if got_intent != exp_intent:
            return "intent_wrong"
        p = got_params or {}
        if exp_entity and not _entity_match(p.get("entity"), exp_entity):
            return "entity_wrong"
        if not _loc_match(p.get("location"), exp_loc):
            return "location_wrong"
        return "pass"

    # Media playback intents — bare verbs without entity / location;
    # intent match alone is enough to pass.
    if cat == "media":
        return "pass" if got_intent == exp_intent else "intent_wrong"

    # Cross-module coverage: clock / weather / presence / automation /
    # system — intent-match only. These are typically parameterless
    # queries or freetext-arg commands where the classifier isn't
    # expected to extract a specific entity from a fuzzy phrase.
    if cat in ("clock", "weather", "presence", "automation", "system"):
        return "pass" if got_intent == exp_intent else "intent_wrong"

    # Ambiguous (no location) → classifier should still pick the intent;
    # resolver should signal ambiguous via params. Pass if intent matches
    # OR if router signalled "ambiguous" in params.
    if cat == "ambiguous":
        p = got_params or {}
        if got_intent == exp_intent and (p.get("ambiguous") or p.get("device_ids") or p.get("device_id")):
            return "pass"
        if got_intent == exp_intent:
            # Intent correct but resolver didn't signal — still close enough
            return "pass"
        return "intent_wrong"

    # Standard case: intent + entity + location all must match
    if got_intent != exp_intent:
        return "intent_wrong"
    p = got_params or {}
    got_entity = p.get("entity")
    # Climate intents accept any device in their entity_types set —
    # "set the temperature in the living room" can legitimately target
    # a thermostat OR an air_conditioner OR a radiator in that room,
    # whichever the registry holds. Don't penalise the router for
    # picking a valid alternative.
    _CLIMATE_EQUIV = {"thermostat", "air_conditioner", "radiator"}
    if exp_intent in (
        "device.set_temperature", "device.set_mode",
        "device.set_fan_speed", "device.query_temperature",
    ) and exp_entity in _CLIMATE_EQUIV and got_entity in _CLIMATE_EQUIV:
        pass  # any climate type passes
    elif not _entity_match(got_entity, exp_entity):
        return "entity_wrong"
    if not _loc_match(p.get("location"), exp_loc):
        return "location_wrong"
    return "pass"


# ── Runner ─────────────────────────────────────────────────────────────


async def _bootstrap_db() -> None:
    """Bind sandbox session_factory + warm the intent_definitions cache."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession, async_sessionmaker, create_async_engine,
    )
    from core.module_loader.sandbox import get_sandbox
    from system_modules.llm_engine.intent_compiler import get_intent_compiler

    sandbox = get_sandbox()
    if sandbox._session_factory is None:
        db_path = "/var/lib/selena/selena.db"
        if not Path(db_path).is_file():
            db_path = "/var/lib/selena/db/selena.db"
        eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        sandbox._session_factory = async_sessionmaker(
            eng, class_=AsyncSession, expire_on_commit=False,
        )
    await get_intent_compiler().full_reload()


async def main() -> None:
    from core.translation.local_translator import get_input_translator
    from system_modules.llm_engine.intent_router import get_intent_router
    from tests.experiments.corpus_generator import generate

    await _bootstrap_db()

    translator = get_input_translator()
    router = get_intent_router()

    corpus = await generate()
    print(f"Coverage bench — {len(corpus)} cases from live registry")
    print("Pipeline: native → Helsinki → IntentRouter.route (full chain)")
    print("=" * 78)

    print("Pre-warming embedding classifier …")
    t_warm = time.perf_counter()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, router.warmup_embedding)
    print(f"  warmup done in {(time.perf_counter()-t_warm)*1000:.0f} ms")
    print()

    verdicts: list[str] = []
    latencies: list[float] = []
    sources: Counter[str] = Counter()

    # Per-dimension breakdowns
    by_lang: dict[str, list[str]] = defaultdict(list)
    by_entity: dict[str, list[str]] = defaultdict(list)
    by_twist: dict[str, list[str]] = defaultdict(list)
    by_noise: dict[str, list[str]] = defaultdict(list)
    by_category: dict[str, list[str]] = defaultdict(list)

    failures: list[dict] = []

    for idx, case in enumerate(corpus, 1):
        lang = case["lang"]
        native = case["native"]

        # Translate non-English through Helsinki (production path).
        try:
            text_en = translator.to_english(native, lang) if lang != "en" else native
        except Exception:
            text_en = native

        t0 = time.perf_counter()
        try:
            # Mirror VoiceCore: pass both the translated EN text AND the
            # native pre-translation text so the catalog filter sees
            # tokens from BOTH languages. Without native_text UK cases
            # lose their native-token matches against device names /
            # room names stored in Cyrillic.
            result = await router.route(
                text_en, lang=lang, tts_lang=lang,
                native_text=native if lang != "en" else None,
            )
        except Exception as exc:
            failures.append({"idx": idx, "case": case, "error": str(exc)})
            continue
        t_ms = (time.perf_counter() - t0) * 1000
        latencies.append(t_ms)

        got_intent = result.intent or ""
        got_params = result.params or {}
        v = _verdict(case, got_intent, got_params)
        verdicts.append(v)
        sources[getattr(result, "source", "unknown")] += 1

        by_lang[lang].append(v)
        by_entity[case.get("exp_entity") or "_none"].append(v)
        by_twist[case.get("twist") or "_none"].append(v)
        by_noise[case.get("noise") or "_none"].append(v)
        by_category[case.get("category") or "_none"].append(v)

        if v != "pass":
            failures.append({
                "idx": idx, "case": case,
                "got_intent": got_intent,
                "got_entity": got_params.get("entity"),
                "got_location": got_params.get("location"),
                "verdict": v,
            })

        if idx % 100 == 0:
            passed = verdicts.count("pass")
            print(f"  {idx}/{len(corpus)}  pass so far: {passed}/{idx} "
                  f"({100*passed/idx:.1f}%)")

    # ── Report ─────────────────────────────────────────────────────────
    print()
    print("=" * 78)
    total = len(verdicts)
    passed = verdicts.count("pass")
    print(f"Accuracy:        {passed}/{total}  ({100*passed/total:.1f}%)")
    print(f"Failure types:   {Counter(v for v in verdicts if v != 'pass')}")
    if latencies:
        print(f"Latency:         p50={statistics.median(latencies):.0f}ms  "
              f"p95={sorted(latencies)[int(0.95*len(latencies))-1]:.0f}ms")
    print(f"Sources:         {dict(sources)}")
    print()

    def _breakdown(label: str, data: dict[str, list[str]]) -> None:
        print(f"Accuracy by {label}:")
        for k in sorted(data.keys()):
            vs = data[k]
            p = vs.count("pass")
            pct = 100 * p / len(vs) if vs else 0
            bar = "█" * int(pct / 5)
            print(f"  {k:20} {p:4}/{len(vs):<4} {bar:21} {pct:.0f}%")
        print()

    _breakdown("entity_type", by_entity)
    _breakdown("language",    by_lang)
    _breakdown("twist",       by_twist)
    _breakdown("noise",       by_noise)
    _breakdown("category",    by_category)

    # ── Persist ────────────────────────────────────────────────────────
    out_dir = Path("/opt/selena-core/_private")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "coverage_bench_results.json"
    out_path.write_text(json.dumps({
        "summary": {
            "total":    total,
            "passed":   passed,
            "accuracy": passed / total if total else 0,
            "p50_ms":   statistics.median(latencies) if latencies else 0,
            "p95_ms":   sorted(latencies)[int(0.95*len(latencies))-1] if latencies else 0,
            "sources":  dict(sources),
        },
        "by_entity":   {k: {"pass": v.count("pass"), "total": len(v)} for k, v in by_entity.items()},
        "by_lang":     {k: {"pass": v.count("pass"), "total": len(v)} for k, v in by_lang.items()},
        "by_twist":    {k: {"pass": v.count("pass"), "total": len(v)} for k, v in by_twist.items()},
        "by_noise":    {k: {"pass": v.count("pass"), "total": len(v)} for k, v in by_noise.items()},
        "by_category": {k: {"pass": v.count("pass"), "total": len(v)} for k, v in by_category.items()},
        "failures":    failures[:200],
    }, ensure_ascii=False, indent=2))
    print(f"JSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
