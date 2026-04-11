"""
tests/benchmark/run_trace_bench.py — single-model forensic trace benchmark.

Runs one model through the full corpus and captures a complete
pipeline trace for every case: raw STT input, Argos normalisation,
tokenisation, dynamic filtered catalog, system prompt, the exact
payload sent to Ollama, the raw LLM response, parsed intent/params,
sanitizer output, and the pass/fail verdict.

Output: ``_private/benchmark_trace_<model>_<timestamp>.txt`` — one
section per case, readable top to bottom like a console log. Plus a
JSON dump for further analysis.

Usage (inside the selena-core container):
    docker compose exec -T core python3 \\
        /opt/selena-core/tests/benchmark/run_trace_bench.py \\
        --model qwen2.5:1.5b
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


OLLAMA_URL = "http://localhost:11434"


# ── Ollama helpers ────────────────────────────────────────────────────


async def _ollama_list_loaded(client) -> list[str]:
    try:
        r = await client.get(f"{OLLAMA_URL}/api/ps", timeout=10.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


async def _ollama_unload(client, model: str) -> None:
    try:
        await client.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0},
            timeout=30.0,
        )
    except Exception:
        pass


async def _unload_all(client) -> None:
    loaded = await _ollama_list_loaded(client)
    for m in loaded:
        await _ollama_unload(client, m)
    if loaded:
        await asyncio.sleep(1.0)


async def _ollama_generate(
    client, *, model: str, system: str, prompt: str,
) -> dict[str, Any]:
    resp = await client.post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": model,
            "system": system,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            "keep_alive": "5m",
            "options": {
                "temperature": 0.1,
                "num_ctx": 4096,
                "num_predict": 256,
            },
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "response": data.get("response", ""),
        "total_ns": int(data.get("total_duration", 0)),
        "load_ns": int(data.get("load_duration", 0)),
        "prompt_eval_count": int(data.get("prompt_eval_count", 0)),
        "prompt_eval_ns": int(data.get("prompt_eval_duration", 0)),
        "eval_count": int(data.get("eval_count", 0)),
        "eval_ns": int(data.get("eval_duration", 0)),
    }


async def _cloud_generate(
    *, provider: str, api_key: str, model: str, system: str, prompt: str,
) -> dict[str, Any]:
    """Cloud LLM call that mimics ``_ollama_generate``'s return shape.

    Cloud providers don't expose Ollama-style timing breakdowns, so we
    measure wall-clock total only and leave prompt_eval / eval fields at
    zero. ``json_mode=True`` so the response comes back as a JSON object,
    same as the Ollama path.
    """
    from system_modules.llm_engine.cloud_providers import generate as _gen
    t0 = time.perf_counter()
    try:
        text = await _gen(
            provider=provider,
            api_key=api_key,
            model=model,
            prompt=prompt,
            system=system,
            temperature=0.1,
            max_tokens=256,
            json_mode=True,
        )
    except Exception:
        raise
    total_ns = int((time.perf_counter() - t0) * 1e9)
    return {
        "response": text or "",
        "total_ns": total_ns,
        "load_ns": 0,
        "prompt_eval_count": 0,
        "prompt_eval_ns": 0,
        "eval_count": 0,
        "eval_ns": 0,
    }


# ── Pipeline trace for one case ───────────────────────────────────────


async def _trace_one_case(
    case: dict,
    model: str,
    system_prompt: str,
    client,
    llm_fn=None,
) -> dict[str, Any]:
    """Run one corpus case through every pipeline stage and capture
    everything we could want in a post-mortem.
    """
    from core.config_writer import get_value as _cfg_get
    from core.translation.local_translator import (
        get_input_translator, _normalize_for_mt,
    )
    from system_modules.llm_engine.intent_router import get_intent_router, _tokenize

    native_text = case["text"]
    lang = case.get("lang", "en")
    expected = case["expected"]

    trace: dict[str, Any] = {
        "native_text": native_text,
        "lang": lang,
        "expected": expected,
    }

    # ── Step 1: STT (simulated) ──
    trace["step1_stt"] = {"text": native_text, "lang": lang}

    # ── Step 2: Argos input translation ──
    translation_enabled = bool(_cfg_get("translation", "enabled", False))
    if translation_enabled and lang != "en":
        t_trans0 = time.perf_counter()
        inp = get_input_translator()
        if inp.is_available():
            normalised = _normalize_for_mt(native_text)
            translated = inp.to_english(native_text, lang)
        else:
            normalised = native_text
            translated = native_text
        trans_ms = (time.perf_counter() - t_trans0) * 1000
        trace["step2_translation"] = {
            "enabled": True,
            "input": native_text,
            "normalised_for_mt": normalised,
            "output_en": translated,
            "ms": round(trans_ms, 1),
        }
        user_text = translated
    else:
        trace["step2_translation"] = {
            "enabled": False, "input": native_text,
            "output_en": native_text,
        }
        user_text = native_text

    # ── Step 3: Tokenisation (union of user + native) ──
    tokens_user = set(_tokenize(user_text))
    tokens_native = set(_tokenize(native_text))
    union_tokens = tokens_user | tokens_native
    trace["step3_tokens"] = {
        "from_translated": sorted(tokens_user),
        "from_native": sorted(tokens_native),
        "union": sorted(union_tokens),
    }

    # ── Step 4: Dynamic filtered catalog ──
    router = get_intent_router()
    t_cat0 = time.perf_counter()
    catalog, allowed = await router._build_filtered_catalog(
        user_text, native_text=native_text,
    )
    catalog_ms = (time.perf_counter() - t_cat0) * 1000
    trace["step4_catalog"] = {
        "chars": len(catalog),
        "ms": round(catalog_ms, 1),
        "text": catalog,
        "allowed": sorted(allowed),
    }

    # ── Step 5: Full LLM payload ──
    full_system = system_prompt + "\n" + catalog if catalog else system_prompt
    trace["step5_llm_payload"] = {
        "model": model,
        "system_chars": len(full_system),
        "user_prompt": user_text,
        "system_prompt": full_system,
    }

    # ── Step 6: LLM call ──
    try:
        if llm_fn is not None:
            tel = await llm_fn(system=full_system, prompt=user_text)
        else:
            tel = await _ollama_generate(
                client, model=model, system=full_system, prompt=user_text,
            )
        llm_error = None
    except Exception as exc:
        tel = {
            "response": "", "total_ns": 0, "load_ns": 0,
            "prompt_eval_count": 0, "prompt_eval_ns": 0,
            "eval_count": 0, "eval_ns": 0,
        }
        llm_error = str(exc)

    tok_per_s = 0.0
    if tel["eval_count"] and tel["eval_ns"]:
        tok_per_s = tel["eval_count"] * 1e9 / tel["eval_ns"]

    trace["step6_llm_response"] = {
        "raw": tel["response"],
        "total_ms": tel["total_ns"] // 1_000_000,
        "load_ms": tel["load_ns"] // 1_000_000,
        "prompt_tokens": tel["prompt_eval_count"],
        "prompt_eval_ms": tel["prompt_eval_ns"] // 1_000_000,
        "output_tokens": tel["eval_count"],
        "eval_ms": tel["eval_ns"] // 1_000_000,
        "tok_per_s": round(tok_per_s, 1),
        "error": llm_error,
    }

    # ── Step 7: Parser (with built-in sanitizer) ──
    sanity_text = (
        f"{user_text}\n{native_text}" if user_text != native_text else user_text
    )
    parsed = router._parse_llm_response(
        tel["response"], source="llm", utter_text=sanity_text,
        allowed_intents=allowed,
    )
    got_intent = parsed.intent if parsed else None
    got_params = parsed.params if parsed else None
    trace["step7_parsed"] = {
        "intent": got_intent,
        "params": got_params,
    }

    # ── Step 8: Verdict ──
    exp_intent = expected.get("intent", "")
    intent_ok = exp_intent == got_intent
    exp_params = expected.get("params") or {}
    params_ok = True
    for k, v in exp_params.items():
        if str((got_params or {}).get(k, "")).lower() != str(v).lower():
            params_ok = False
            break
    trace["step8_verdict"] = {
        "expected_intent": exp_intent,
        "got_intent": got_intent,
        "expected_params": exp_params,
        "got_params": got_params,
        "intent_ok": intent_ok,
        "params_ok": params_ok,
        "pass": intent_ok and params_ok,
    }
    return trace


# ── Report renderer ───────────────────────────────────────────────────


def _render_trace_section(index: int, total: int, trace: dict) -> str:
    lines: list[str] = []
    sep = "=" * 74
    lines.append("")
    lines.append(sep)
    lines.append(
        f"Case {index}/{total} — [{trace['lang']}] {trace['native_text']!r}"
    )
    lines.append(sep)
    lines.append("")

    # Step 1
    lines.append("STEP 1. STT input (simulating Vosk output)")
    lines.append(f"  lang:        {trace['step1_stt']['lang']}")
    lines.append(f"  text:        {trace['step1_stt']['text']!r}")
    lines.append("")

    # Step 2
    t2 = trace["step2_translation"]
    lines.append("STEP 2. InputTranslator (Argos)")
    if t2["enabled"]:
        lines.append(f"  enabled:     yes  ({trace['lang']} → en)")
        lines.append(f"  raw input:   {t2['input']!r}")
        lines.append(f"  normalised:  {t2['normalised_for_mt']!r}  (capitalise + period)")
        lines.append(f"  output EN:   {t2['output_en']!r}")
        lines.append(f"  duration:    {t2.get('ms', '?')} ms")
    else:
        lines.append("  enabled:     no  (pass-through)")
        lines.append(f"  output:      {t2['output_en']!r}")
    lines.append("")

    # Step 3
    t3 = trace["step3_tokens"]
    lines.append("STEP 3. Tokenisation (union of translated + native)")
    lines.append(f"  from EN:     {t3['from_translated']}")
    lines.append(f"  from native: {t3['from_native']}")
    lines.append(f"  union:       {t3['union']}")
    lines.append("")

    # Step 4
    t4 = trace["step4_catalog"]
    lines.append(
        f"STEP 4. Dynamic filtered catalog  ({t4['chars']} chars, {t4['ms']} ms)"
    )
    for catalog_line in t4["text"].splitlines():
        lines.append(f"  │ {catalog_line}")
    lines.append("")

    # Step 5
    t5 = trace["step5_llm_payload"]
    lines.append(f"STEP 5. LLM payload ({t5['model']})")
    lines.append(f"  system total: {t5['system_chars']} chars "
                 "(identity + schema + catalog)")
    lines.append(f"  user prompt:  {t5['user_prompt']!r}")
    lines.append("")

    # Step 6
    t6 = trace["step6_llm_response"]
    lines.append("STEP 6. LLM response")
    if t6.get("error"):
        lines.append(f"  ERROR:       {t6['error']}")
    lines.append(
        f"  timing:      total={t6['total_ms']} ms  "
        f"load={t6['load_ms']} ms  "
        f"prompt_eval={t6['prompt_eval_ms']} ms  "
        f"gen={t6['eval_ms']} ms"
    )
    lines.append(
        f"  tokens:      prompt={t6['prompt_tokens']}  "
        f"output={t6['output_tokens']}  "
        f"throughput={t6['tok_per_s']} tok/s"
    )
    raw = (t6["raw"] or "").strip()
    if raw:
        lines.append("  raw:")
        for raw_line in raw.splitlines():
            lines.append(f"    > {raw_line}")
    else:
        lines.append("  raw:         <empty>")
    lines.append("")

    # Step 7
    t7 = trace["step7_parsed"]
    lines.append("STEP 7. Parser + sanitizer → IntentResult")
    lines.append(f"  intent:      {t7['intent']}")
    lines.append(f"  params:      {t7['params']}")
    lines.append("")

    # Step 8
    t8 = trace["step8_verdict"]
    mark = "PASS" if t8["pass"] else "FAIL"
    lines.append(f"STEP 8. Verdict: {mark}")
    lines.append(
        f"  expected: intent={t8['expected_intent']}  "
        f"params={t8['expected_params']}"
    )
    lines.append(
        f"  got:      intent={t8['got_intent']}  "
        f"params={t8['got_params']}"
    )
    if not t8["intent_ok"]:
        lines.append("  ✗ intent mismatch")
    if not t8["params_ok"]:
        lines.append("  ✗ params mismatch")
    lines.append("")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="tests/benchmark/full_corpus.jsonl")
    ap.add_argument("--model", default="qwen2.5:1.5b")
    ap.add_argument("--out-dir", default="_private")
    ap.add_argument(
        "--cloud", default="",
        help="Cloud provider id (e.g. 'google'). When set, bench against the "
             "configured voice.providers.<id> instead of Ollama. Reads "
             "api_key + model from core.yaml. --model is ignored.",
    )
    args = ap.parse_args()

    import httpx

    async with httpx.AsyncClient() as client:
        # Load corpus
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

        # Cloud mode: read api_key + model from voice.providers.<id>
        cloud_api_key = ""
        cloud_model = ""
        if args.cloud:
            from core.config_writer import read_config as _rc
            _cfg = _rc()
            _p_cfg = (
                _cfg.get("voice", {})
                    .get("providers", {})
                    .get(args.cloud, {})
            )
            cloud_api_key = _p_cfg.get("api_key", "") or ""
            cloud_model = _p_cfg.get("model", "") or ""
            if not cloud_api_key or not cloud_model:
                print(
                    f"  ERROR: voice.providers.{args.cloud} missing api_key "
                    f"or model in core.yaml"
                )
                return
            args.model = cloud_model
            print(f"Cloud:  {args.cloud} ({cloud_model})")
        else:
            print(f"Model:  {args.model}")
        print(f"Corpus: {len(corpus)} cases")

        # Bootstrap sandbox + IntentCompiler
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

        # System prompt from DB
        from core.llm import _resolve_system_prompt
        system_prompt = await _resolve_system_prompt("intent", "ollama")
        print(f"System prompt: {len(system_prompt)} chars")

        # Isolate the model: unload others, single warm-up
        original_model = ""
        if not args.cloud:
            print("  unloading any loaded models …")
            await _unload_all(client)

            # Set the config so that any indirect llm_call also uses this model
            from core.config_writer import read_config, write_config
            cfg = read_config()
            original_model = cfg.get("voice", {}).get("llm_model", "")
            cfg.setdefault("voice", {})["llm_model"] = args.model
            write_config(cfg)

            print(f"  warming up {args.model} …")
            try:
                await _ollama_generate(
                    client, model=args.model, system=system_prompt, prompt="hello",
                )
            except Exception as exc:
                print(f"  warmup error: {exc}")

        # Build the LLM caller used per-case. Cloud → cloud_providers.generate,
        # local → _ollama_generate (default in _trace_one_case when llm_fn is None).
        llm_fn = None
        if args.cloud:
            async def llm_fn(*, system: str, prompt: str):  # noqa: E306
                return await _cloud_generate(
                    provider=args.cloud,
                    api_key=cloud_api_key,
                    model=cloud_model,
                    system=system,
                    prompt=prompt,
                )

        # Device snapshot for header
        from sqlalchemy import select
        from core.registry.models import Device
        devices: list[dict] = []
        async with sandbox._session_factory() as session:
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
                    "name_en": meta.get("name_en") or "",
                    "location_en": meta.get("location_en") or "",
                })

        # Run the full corpus with traces
        traces: list[dict] = []
        pass_count = 0
        for i, case in enumerate(corpus, 1):
            print(f"  [{i}/{len(corpus)}] {case['text'][:55]}")
            trace = await _trace_one_case(
                case, args.model, system_prompt, client, llm_fn=llm_fn,
            )
            traces.append(trace)
            if trace["step8_verdict"]["pass"]:
                pass_count += 1

        # Restore original model and unload (Ollama path only).
        if not args.cloud:
            try:
                from core.config_writer import read_config, write_config
                cfg2 = read_config()
                if original_model:
                    cfg2.setdefault("voice", {})["llm_model"] = original_model
                    write_config(cfg2)
                    print(f"  restored voice.llm_model → {original_model}")
                await _ollama_unload(client, args.model)
            except Exception as exc:
                print(f"  cleanup warning: {exc}")

    # ── Render the report ──
    # Snapshot the active translation engine so the report and the
    # output filename make it obvious which engine produced these
    # numbers. Without this label two runs against different engines
    # are indistinguishable on disk.
    try:
        from core.config_writer import get_value as _get_value
        active_engine = _get_value("translation", "engine", "argos") or "argos"
        active_lang = _get_value("translation", "active_lang", "") or ""
        translation_enabled = bool(_get_value("translation", "enabled", False))
    except Exception:
        active_engine = "unknown"
        active_lang = ""
        translation_enabled = False
    engine_label = active_engine if translation_enabled else f"{active_engine}-disabled"

    lines: list[str] = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    latencies = [
        t["step6_llm_response"]["total_ms"]
        for t in traces
        if t["step6_llm_response"]["total_ms"]
    ]
    tok_rates = [
        t["step6_llm_response"]["tok_per_s"]
        for t in traces
        if t["step6_llm_response"]["tok_per_s"]
    ]
    avg_ms = int(statistics.mean(latencies)) if latencies else 0
    p50_ms = int(statistics.median(latencies)) if latencies else 0
    p95_ms = (
        int(statistics.quantiles(latencies, n=20)[18])
        if len(latencies) >= 20 else (max(latencies) if latencies else 0)
    )
    avg_tok_s = round(statistics.mean(tok_rates), 1) if tok_rates else 0

    lines.append(
        f"SelenaCore voice pipeline — trace benchmark "
        f"(LLM {args.model} · translator {engine_label})"
    )
    lines.append("=" * 74)
    lines.append(f"Run time:       {now}")
    lines.append(f"LLM model:      {args.model}")
    lines.append(
        f"Translation:    engine={active_engine}  "
        f"lang={active_lang or '(none)'}  enabled={translation_enabled}"
    )
    lines.append(f"Corpus:         {len(corpus)} cases")
    lines.append(f"System prompt:  {len(system_prompt)} chars (from DB key 'system')")
    lines.append(f"Accuracy:       {pass_count}/{len(corpus)}  "
                 f"({pass_count/len(corpus)*100:.1f}%)")
    lines.append(f"Latency:        avg={avg_ms} ms  p50={p50_ms} ms  p95={p95_ms} ms")
    lines.append(f"Throughput:     avg={avg_tok_s} tok/s")
    lines.append(f"Devices in DB:  {len(devices)}")
    for d in devices:
        lines.append(
            f"  - {d['name']!r:20s} type={d['entity_type']:20s} "
            f"name_en={d['name_en']:20s} loc_en={d['location_en']}"
        )
    lines.append("")

    # Pass/fail summary
    lines.append("Per-case verdicts")
    lines.append("-" * 74)
    for i, t in enumerate(traces, 1):
        v = t["step8_verdict"]
        mark = "PASS" if v["pass"] else "FAIL"
        lines.append(
            f"  {i:3d}. [{t['lang']}] {mark}  "
            f"{t['native_text'][:40]!r:42s}  "
            f"got={v['got_intent']}  exp={v['expected_intent']}"
        )
    lines.append("")

    # Per-case traces
    for i, t in enumerate(traces, 1):
        lines.append(_render_trace_section(i, len(corpus), t))

    report = "\n".join(lines)

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

    safe_model = args.model.replace("/", "_").replace(":", "_")
    safe_engine = engine_label.replace("/", "_")
    txt_path = out_dir / f"benchmark_trace_{safe_model}_{safe_engine}_{ts}.txt"
    json_path = out_dir / f"benchmark_trace_{safe_model}_{safe_engine}_{ts}.json"
    txt_path.write_text(report, encoding="utf-8")
    json_path.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "model": args.model,
        "translation_engine": active_engine,
        "translation_lang": active_lang,
        "translation_enabled": translation_enabled,
        "devices": devices,
        "traces": traces,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"Pass: {pass_count}/{len(corpus)}  ({pass_count/len(corpus)*100:.1f}%)")
    print(f"Report: {txt_path}")
    print(f"JSON:   {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
