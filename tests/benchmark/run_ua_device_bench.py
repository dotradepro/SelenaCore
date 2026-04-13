"""
tests/benchmark/run_ua_device_bench.py — Ukrainian voice command benchmark.

Tests ALL registered devices through the full production pipeline:
  Helsinki translation → tokenisation → catalog filter → embedding classify
  → param extraction → device resolution

Reports intent accuracy, param accuracy, device resolution rate, confidence
scores and per-stage latency.  Outputs a human-readable report + JSON dump
to ``_private/``.

Usage (inside the selena-core container):

    docker compose exec -T core python3 \
        /opt/selena-core/tests/benchmark/run_ua_device_bench.py \
        --corpus /opt/selena-core/tests/benchmark/ua_device_corpus.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_here = Path(__file__).resolve()
for candidate in (_here.parents[2], Path("/opt/selena-core")):
    if (candidate / "core" / "llm.py").is_file() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))


# ── Bootstrap ────────────────────────────────────────────────────────


async def _ensure_bootstrap() -> None:
    from core.module_loader.sandbox import get_sandbox
    sandbox = get_sandbox()
    if sandbox._session_factory is not None:
        return

    from sqlalchemy.ext.asyncio import (
        create_async_engine, async_sessionmaker, AsyncSession,
    )
    db_path = "/var/lib/selena/selena.db"
    if not Path(db_path).is_file():
        db_path = "/var/lib/selena/db/selena.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    sandbox._session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    from system_modules.llm_engine.intent_compiler import get_intent_compiler
    try:
        await get_intent_compiler().full_reload()
    except Exception as exc:
        print(f"  ! IntentCompiler.full_reload: {exc}")


# ── Per-case pipeline trace ──────────────────────────────────────────


async def _trace_case(case: dict[str, Any]) -> dict[str, Any]:
    from core.config_writer import get_value as _cfg_get
    from core.translation.local_translator import (
        get_input_translator, _normalize_for_mt,
    )
    from system_modules.llm_engine.intent_router import (
        get_intent_router, _tokenize, _parse_catalog_to_candidates,
        _extract_command_segment,
    )
    from system_modules.llm_engine.embedding_classifier import (
        EmbeddingIntentClassifier, extract_params,
    )

    native_text = case["text"]
    lang = case.get("lang", "uk")
    expected = case.get("expected", {})

    trace: dict[str, Any] = {
        "native_text": native_text,
        "lang": lang,
        "device_ref": case.get("device_ref", ""),
        "category": case.get("category", ""),
        "expected": expected,
        "notes": case.get("notes", ""),
    }

    # ── Stage 1: Helsinki translation ──
    translation_enabled = bool(_cfg_get("translation", "enabled", False))
    if translation_enabled and lang != "en":
        t0 = time.perf_counter()
        inp = get_input_translator()
        if inp.is_available():
            normalised = _normalize_for_mt(native_text)
            translated = inp.to_english(native_text, lang)
        else:
            normalised = native_text
            translated = native_text
        trans_ms = (time.perf_counter() - t0) * 1000
        trace["translation"] = {
            "enabled": True,
            "input": native_text,
            "normalised": normalised,
            "output_en": translated,
            "ms": round(trans_ms, 1),
        }
        user_text = translated
    else:
        trace["translation"] = {
            "enabled": False,
            "input": native_text,
            "output_en": native_text,
            "ms": 0,
        }
        user_text = native_text

    # ── Stage 2: Tokenisation ──
    tokens_en = set(_tokenize(user_text))
    tokens_native = set(_tokenize(native_text))
    union = tokens_en | tokens_native
    trace["tokens"] = {
        "from_en": sorted(tokens_en),
        "from_native": sorted(tokens_native),
        "union": sorted(union),
    }

    # ── Stage 3: Catalog build ──
    router = get_intent_router()
    t0 = time.perf_counter()
    catalog, allowed = await router._build_filtered_catalog(
        user_text, native_text=native_text,
    )
    catalog_ms = (time.perf_counter() - t0) * 1000
    trace["catalog"] = {
        "chars": len(catalog),
        "ms": round(catalog_ms, 1),
        "text": catalog,
        "allowed_intents": sorted(allowed),
    }

    # ── Stage 4: Embedding classify ──
    candidates = _parse_catalog_to_candidates(catalog)
    trace["embedding"] = {
        "candidates_count": len(candidates),
        "intent": None,
        "score": 0.0,
        "margin": 0.0,
        "runner_up": None,
        "runner_up_score": 0.0,
        "all_scores": {},
        "error": None,
    }

    emb_intent = None
    emb_params: dict[str, Any] = {}
    if candidates:
        try:
            emb = router._ensure_embedding()
            if emb:
                query = _extract_command_segment(user_text)
                t0 = time.perf_counter()
                result = emb.classify(query, candidates)
                emb_ms = (time.perf_counter() - t0) * 1000
                emb_intent = result.intent
                emb_params = result.params or {}

                trace["embedding"].update({
                    "query_text": query,
                    "intent": result.intent,
                    "score": round(result.score, 4),
                    "margin": round(result.margin, 4),
                    "runner_up": result.runner_up,
                    "runner_up_score": round(result.runner_up_score, 4),
                    "ms": round(emb_ms, 1),
                    "params": emb_params,
                })
            else:
                trace["embedding"]["error"] = "classifier not available"
        except Exception as exc:
            trace["embedding"]["error"] = str(exc)

    # ── Stage 5: Param extraction (from embedding result) ──
    trace["params"] = {
        "entity": emb_params.get("entity"),
        "location": emb_params.get("location"),
        "value": emb_params.get("value") or emb_params.get("genre"),
    }

    # ── Stage 6: Device resolution check ──
    trace["resolution"] = {
        "entity_query": emb_params.get("entity"),
        "location_query": emb_params.get("location"),
        "matched_count": 0,
        "matched_devices": [],
        "success": False,
    }

    if emb_intent and emb_intent not in ("unknown",):
        try:
            from core.module_loader.sandbox import get_sandbox
            from sqlalchemy import select, or_
            from core.registry.models import Device

            sf = get_sandbox()._session_factory
            if sf:
                async with sf() as session:
                    stmt = select(Device).where(Device.enabled == True)  # noqa: E712
                    entity = (emb_params.get("entity") or "").lower().strip()
                    location = (emb_params.get("location") or "").lower().strip()

                    if entity and location:
                        stmt = stmt.where(
                            Device.entity_type.ilike(f"%{entity}%"),
                            or_(
                                Device.location.ilike(f"%{location}%"),
                                Device.meta.ilike(f'%"location_en"%{location}%'),
                            ),
                        )
                    elif location:
                        stmt = stmt.where(
                            or_(
                                Device.location.ilike(f"%{location}%"),
                                Device.meta.ilike(f'%"location_en"%{location}%'),
                            ),
                        )
                    elif entity:
                        stmt = stmt.where(
                            Device.entity_type.ilike(f"%{entity}%"),
                        )

                    rows = list((await session.execute(stmt)).scalars().all())
                    matched = []
                    for d in rows:
                        try:
                            m = json.loads(d.meta) if d.meta else {}
                        except Exception:
                            m = {}
                        matched.append({
                            "device_id": d.device_id[:8],
                            "name": d.name,
                            "entity_type": d.entity_type,
                            "location": d.location,
                            "name_en": m.get("name_en", ""),
                            "location_en": m.get("location_en", ""),
                        })
                    trace["resolution"]["matched_count"] = len(matched)
                    trace["resolution"]["matched_devices"] = matched
                    trace["resolution"]["success"] = len(matched) >= 1
        except Exception as exc:
            trace["resolution"]["error"] = str(exc)

    # ── Stage 7: Verdict ──
    exp_intent = expected.get("intent", "")
    got_intent = emb_intent or "unknown"
    intent_pass = exp_intent == got_intent

    params_pass = True
    exp_params = expected.get("params") or {}
    for k, v in exp_params.items():
        got = str(emb_params.get(k, "")).lower()
        exp = str(v).lower()
        if got != exp:
            params_pass = False
            break

    is_known_issue = bool(case.get("notes", ""))

    trace["verdict"] = {
        "intent_pass": intent_pass,
        "params_pass": params_pass,
        "resolution_success": trace["resolution"]["success"],
        "known_issue": is_known_issue,
        "got_intent": got_intent,
        "got_params": emb_params,
    }
    return trace


# ── Aggregation ──────────────────────────────────────────────────────


def _aggregate(traces: list[dict]) -> dict[str, Any]:
    total = len(traces)
    intent_pass = sum(1 for t in traces if t["verdict"]["intent_pass"])
    params_pass = sum(1 for t in traces if t["verdict"]["params_pass"])
    resolved = sum(1 for t in traces if t["resolution"]["success"])
    known = sum(1 for t in traces if t["verdict"]["known_issue"])

    # Per-category
    categories: dict[str, dict] = {}
    for t in traces:
        cat = t.get("category", "other")
        if cat not in categories:
            categories[cat] = {"total": 0, "intent_pass": 0, "params_pass": 0}
        categories[cat]["total"] += 1
        if t["verdict"]["intent_pass"]:
            categories[cat]["intent_pass"] += 1
        if t["verdict"]["params_pass"]:
            categories[cat]["params_pass"] += 1

    # Latencies
    trans_ms = [t["translation"]["ms"] for t in traces if t["translation"]["ms"]]
    catalog_ms = [t["catalog"]["ms"] for t in traces]
    emb_ms = [t["embedding"].get("ms", 0) for t in traces if t["embedding"].get("ms")]

    # Confidence
    scores = [t["embedding"]["score"] for t in traces if t["embedding"]["score"]]
    margins = [t["embedding"]["margin"] for t in traces if t["embedding"]["margin"]]
    low_margin = sum(1 for m in margins if m < 0.05)

    def _stats(vals: list[float]) -> dict:
        if not vals:
            return {"avg": 0, "p50": 0, "p95": 0, "min": 0, "max": 0}
        return {
            "avg": round(statistics.mean(vals), 1),
            "p50": round(statistics.median(vals), 1),
            "p95": round(
                statistics.quantiles(vals, n=20)[18] if len(vals) >= 20
                else max(vals), 1,
            ),
            "min": round(min(vals), 1),
            "max": round(max(vals), 1),
        }

    # Issues detection
    issues: dict[str, list] = {
        "missing_entity_map": [],
        "missing_room_keywords": [],
        "missing_intents": [],
        "translation_artifacts": [],
        "low_confidence": [],
        "wrong_intent": [],
    }

    for t in traces:
        v = t["verdict"]
        cat = t.get("category", "")
        # Missing intents (brightness, color)
        if cat in ("brightness", "color") and not v["intent_pass"]:
            issues["missing_intents"].append({
                "text": t["native_text"],
                "expected": t["expected"].get("intent"),
                "got": v["got_intent"],
            })
        # Wrong intent (not a known missing intent)
        elif not v["intent_pass"] and cat not in ("brightness", "color"):
            issues["wrong_intent"].append({
                "text": t["native_text"],
                "expected": t["expected"].get("intent"),
                "got": v["got_intent"],
                "score": t["embedding"]["score"],
                "notes": t.get("notes", ""),
            })
        # Low confidence
        if t["embedding"]["margin"] and t["embedding"]["margin"] < 0.05:
            issues["low_confidence"].append({
                "text": t["native_text"],
                "intent": v["got_intent"],
                "score": t["embedding"]["score"],
                "margin": t["embedding"]["margin"],
                "runner_up": t["embedding"]["runner_up"],
            })
        # Translation artifacts
        en = t["translation"].get("output_en", "")
        if en and any(weird in en.lower() for weird in ("clutch", "putting")):
            issues["translation_artifacts"].append({
                "text": t["native_text"],
                "translated": en,
            })

    return {
        "total": total,
        "intent_accuracy": round(intent_pass / total * 100, 1) if total else 0,
        "params_accuracy": round(params_pass / total * 100, 1) if total else 0,
        "resolution_rate": round(resolved / total * 100, 1) if total else 0,
        "intent_pass": intent_pass,
        "params_pass": params_pass,
        "resolved": resolved,
        "known_issues": known,
        "per_category": categories,
        "latency": {
            "translation": _stats(trans_ms),
            "catalog": _stats(catalog_ms),
            "embedding": _stats(emb_ms),
        },
        "confidence": {
            "avg_score": round(statistics.mean(scores), 3) if scores else 0,
            "avg_margin": round(statistics.mean(margins), 3) if margins else 0,
            "min_score": round(min(scores), 3) if scores else 0,
            "low_margin_count": low_margin,
        },
        "issues": issues,
    }


# ── Report renderer ──────────────────────────────────────────────────


def _render_report(
    traces: list[dict],
    summary: dict,
    devices: list[dict],
) -> str:
    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines.append("SelenaCore Ukrainian Device Intent Benchmark")
    lines.append("=" * 74)
    lines.append(f"Run time:       {now}")
    lines.append(f"Classifier:     all-MiniLM-L6-v2 via ONNX (Tier 1 embedding)")
    lines.append(f"Corpus:         {summary['total']} cases (Ukrainian voice commands)")
    lines.append(f"Devices in DB:  {len(devices)}")
    lines.append("")

    # Device snapshot
    lines.append("Device Snapshot:")
    for d in devices:
        lines.append(
            f"  {d['name']:30s}  type={d['entity_type']:17s}  "
            f"loc={str(d['location'] or ''):12s}  "
            f"name_en={d['name_en']:30s}  loc_en={d['location_en']}"
        )
    lines.append("")

    # Summary
    lines.append("=" * 74)
    lines.append("SUMMARY")
    lines.append("-" * 74)
    lines.append(f"{'':25s}  {'Total':>6s}  {'Pass':>6s}  {'Fail':>6s}  {'Acc%':>6s}")
    t = summary["total"]
    lines.append(
        f"{'Intent accuracy':25s}  {t:>6d}  {summary['intent_pass']:>6d}  "
        f"{t - summary['intent_pass']:>6d}  {summary['intent_accuracy']:>5.1f}%"
    )
    lines.append(
        f"{'Params accuracy':25s}  {t:>6d}  {summary['params_pass']:>6d}  "
        f"{t - summary['params_pass']:>6d}  {summary['params_accuracy']:>5.1f}%"
    )
    lines.append(
        f"{'Device resolution':25s}  {t:>6d}  {summary['resolved']:>6d}  "
        f"{t - summary['resolved']:>6d}  {summary['resolution_rate']:>5.1f}%"
    )
    lines.append(f"\nKnown-issue cases:  {summary['known_issues']}")
    lines.append("")

    # Per-category
    lines.append("Per-category breakdown:")
    for cat, data in sorted(summary["per_category"].items()):
        ct = data["total"]
        ip = data["intent_pass"]
        pct = round(ip / ct * 100, 1) if ct else 0
        lines.append(f"  {cat:15s}  {ip}/{ct}  ({pct}%)")
    lines.append("")

    # Latency
    lines.append("Latency (ms):")
    lines.append(f"  {'Stage':15s}  {'avg':>7s}  {'p50':>7s}  {'p95':>7s}")
    for stage in ("translation", "catalog", "embedding"):
        s = summary["latency"][stage]
        lines.append(
            f"  {stage:15s}  {s['avg']:>7.1f}  {s['p50']:>7.1f}  {s['p95']:>7.1f}"
        )
    lines.append("")

    # Confidence
    c = summary["confidence"]
    lines.append("Embedding confidence:")
    lines.append(f"  Avg score:      {c['avg_score']:.3f}")
    lines.append(f"  Avg margin:     {c['avg_margin']:.3f}")
    lines.append(f"  Min score:      {c['min_score']:.3f}")
    lines.append(f"  Low-margin (<0.05): {c['low_margin_count']} cases")
    lines.append("")

    # Issues
    lines.append("=" * 74)
    lines.append("ISSUES DETECTED")
    lines.append("-" * 74)

    iss = summary["issues"]
    if iss["wrong_intent"]:
        lines.append(f"\n1. WRONG INTENT ({len(iss['wrong_intent'])} cases):")
        for i in iss["wrong_intent"]:
            lines.append(
                f"   [{i['text']}]  expected={i['expected']}  "
                f"got={i['got']}  score={i['score']}"
            )
            if i.get("notes"):
                lines.append(f"     note: {i['notes']}")

    if iss["missing_intents"]:
        lines.append(f"\n2. MISSING INTENTS ({len(iss['missing_intents'])} cases):")
        for i in iss["missing_intents"]:
            lines.append(
                f"   [{i['text']}]  expected={i['expected']}  got={i['got']}"
            )

    if iss["low_confidence"]:
        lines.append(
            f"\n3. LOW CONFIDENCE / MARGIN ({len(iss['low_confidence'])} cases):"
        )
        for i in iss["low_confidence"]:
            lines.append(
                f"   [{i['text']}]  intent={i['intent']}  "
                f"score={i['score']:.3f}  margin={i['margin']:.3f}  "
                f"runner_up={i['runner_up']}"
            )

    if iss["translation_artifacts"]:
        lines.append(
            f"\n4. TRANSLATION ARTIFACTS ({len(iss['translation_artifacts'])} cases):"
        )
        for i in iss["translation_artifacts"]:
            lines.append(f"   [{i['text']}] → [{i['translated']}]")
    lines.append("")

    # Per-case traces
    lines.append("=" * 74)
    lines.append("PER-CASE TRACES")
    lines.append("=" * 74)

    for idx, t in enumerate(traces, 1):
        v = t["verdict"]
        status = "PASS" if v["intent_pass"] and v["params_pass"] else "FAIL"
        lines.append("")
        lines.append(f"Case {idx}/{len(traces)} — [{t['lang']}] {t['native_text']!r}  [{status}]")
        lines.append(f"  Device ref: {t['device_ref']}")
        if t.get("notes"):
            lines.append(f"  Note: {t['notes']}")

        # Translation
        tr = t["translation"]
        if tr["enabled"]:
            lines.append(
                f"  TRANSLATION:  {tr['input']!r} → {tr['output_en']!r}  ({tr['ms']}ms)"
            )
        else:
            lines.append(f"  TRANSLATION:  disabled (pass-through)")

        # Tokens
        tk = t["tokens"]
        lines.append(f"  TOKENS EN:    {tk['from_en']}")
        lines.append(f"  TOKENS UK:    {tk['from_native']}")

        # Catalog
        ct = t["catalog"]
        lines.append(
            f"  CATALOG:      {ct['chars']} chars, {ct['ms']}ms, "
            f"intents={ct['allowed_intents']}"
        )

        # Embedding
        em = t["embedding"]
        if em.get("error"):
            lines.append(f"  EMBEDDING:    ERROR: {em['error']}")
        elif em["intent"]:
            lines.append(
                f"  EMBEDDING:    intent={em['intent']}  score={em['score']:.3f}  "
                f"margin={em['margin']:.3f}  runner_up={em['runner_up']}({em['runner_up_score']:.3f})"
            )
        else:
            lines.append(f"  EMBEDDING:    no result")

        # Params
        p = t["params"]
        lines.append(
            f"  PARAMS:       entity={p['entity']}  location={p['location']}  "
            f"value={p['value']}"
        )

        # Resolution
        res = t["resolution"]
        if res["matched_count"] == 0:
            lines.append(f"  RESOLUTION:   no devices matched")
        elif res["matched_count"] == 1:
            d = res["matched_devices"][0]
            lines.append(
                f"  RESOLUTION:   1 match → {d['name']} ({d['entity_type']}, "
                f"{d['location']})"
            )
        else:
            lines.append(
                f"  RESOLUTION:   {res['matched_count']} matches (AMBIGUOUS):"
            )
            for d in res["matched_devices"]:
                lines.append(
                    f"                 - {d['name']} ({d['entity_type']}, {d['location']})"
                )

        # Verdict
        lines.append(
            f"  VERDICT:      intent={'PASS' if v['intent_pass'] else 'FAIL'}  "
            f"params={'PASS' if v['params_pass'] else 'FAIL'}  "
            f"resolution={'OK' if res['success'] else 'FAIL'}"
        )
        if not v["intent_pass"]:
            lines.append(
                f"                expected={t['expected'].get('intent')}  "
                f"got={v['got_intent']}"
            )

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="tests/benchmark/ua_device_corpus.jsonl")
    ap.add_argument("--out-dir", default="_private")
    args = ap.parse_args()

    corpus_path = Path(args.corpus)
    if not corpus_path.is_absolute():
        alt = Path("/opt/selena-core") / args.corpus
        if alt.is_file():
            corpus_path = alt
    corpus = [
        json.loads(line)
        for line in corpus_path.read_text().splitlines()
        if line.strip()
    ]
    print(f"Corpus:     {len(corpus)} cases from {corpus_path}")

    await _ensure_bootstrap()
    print("Bootstrap:  OK")

    # Warm up embedding
    from system_modules.llm_engine.intent_router import get_intent_router
    router = get_intent_router()
    router.warmup_embedding()
    print("Embedding:  warmed up")

    # Device snapshot
    from core.module_loader.sandbox import get_sandbox
    from sqlalchemy import select
    from core.registry.models import Device

    devices: list[dict] = []
    async with get_sandbox()._session_factory() as session:
        rows = (await session.execute(select(Device))).scalars().all()
        for d in rows:
            try:
                meta = json.loads(d.meta) if d.meta else {}
            except Exception:
                meta = {}
            devices.append({
                "name": d.name,
                "entity_type": d.entity_type or "",
                "location": d.location or "",
                "name_en": meta.get("name_en", ""),
                "location_en": meta.get("location_en", ""),
            })
    print(f"Devices:    {len(devices)} in DB")

    # Run traces
    traces: list[dict] = []
    for i, case in enumerate(corpus, 1):
        print(f"  [{i:2d}/{len(corpus)}] {case['text'][:50]}")
        trace = await _trace_case(case)
        traces.append(trace)

    # Aggregate
    summary = _aggregate(traces)

    # Render report
    report = _render_report(traces, summary, devices)

    # Write output
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        for candidate in (
            Path("/opt/selena-core") / args.out_dir,
            Path.cwd() / args.out_dir,
        ):
            if candidate.parent.is_dir():
                out_dir = candidate
                break
    out_dir.mkdir(parents=True, exist_ok=True)

    txt_path = out_dir / f"ua_device_bench_{ts}.txt"
    json_path = out_dir / f"ua_device_bench_{ts}.json"

    txt_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "devices": devices,
        "summary": summary,
        "traces": traces,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print summary
    print()
    print(f"Intent accuracy:  {summary['intent_accuracy']}%  ({summary['intent_pass']}/{summary['total']})")
    print(f"Params accuracy:  {summary['params_accuracy']}%  ({summary['params_pass']}/{summary['total']})")
    print(f"Resolution rate:  {summary['resolution_rate']}%  ({summary['resolved']}/{summary['total']})")
    print(f"Known-issue:      {summary['known_issues']} cases")
    print(f"\nReport: {txt_path}")
    print(f"JSON:   {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
