# Intent Classifier Coverage Bench

The coverage bench is the regression gate for SelenaCore's voice intent classifier. It generates test cases from the **live device registry** and runs each through the full production pipeline (Helsinki translation → embedding classifier → post-processing cascade → intent resolution). The result is a per-category accuracy breakdown that catches classifier regressions before they reach production.

Current production baseline: **96.6%** on 1114 cases, p50 15 ms, p95 17 ms, with zero false-positive distractors.

For the classifier architecture, see [intent-routing.md](intent-routing.md). For rules on authoring intents that score well, see [intent-authoring.md](intent-authoring.md).

---

## What it tests

The bench corpus is assembled from two sources:

### Registry-generated cases (~1000)

For every `(entity_type, location)` combination present in the [device registry](api-reference.md#device-registry), [corpus_generator.py](../tests/experiments/corpus_generator.py) emits cases across:

- **plain** — canonical phrasing (`"turn on the light in the bedroom"`)
- **variety** — 5 paraphrase twists: `syn`, `polite`, `short`, `indirect`, `casual`
- **noise** — 5 real-world STT degradations: `filler`, `typo`, `stutter`, `context`, `long`
- **ambiguous** — same intent without a room, to exercise the `needs_location` path

All generated in EN and UK (corpus is bilingual).

### Curated categories (~60)

Hand-written cases for intents that can't be auto-generated from the registry:

| Category | Size | Intents covered |
|---|---|---|
| `media` | 26 | `media.play_*`, `pause`/`resume`/`stop`, `next`/`previous`, `volume_*`, `whats_playing` |
| `all_off` / `all_on` | 15 | `house.all_off` / `house.all_on` with optional entity + location filters |
| `clock` | 14 | `clock.set_alarm`, `set_timer`, `set_reminder`, `list_alarms`, `stop_alarm`, `cancel_timer` |
| `weather` | 6 | `weather.current`, `forecast`, `temperature` |
| `presence` | 6 | `presence.who_home`, `check_user`, `status` |
| `automation` | 7 | `automation.list`, `enable`, `disable` |
| `system` | 19 | `watchdog.*`, `energy.*`, `privacy_*`, `device.query_temperature`, `set_fan_speed`, `media.play_search` |
| `distractor` | 9 | chat / nonsense — **must NOT** produce a device intent |

Every intent owned by every module is represented at least once. Declared-but-never-tested intents are flagged by the audit at [docs/system-module-development.md](system-module-development.md).

---

## Running the bench

### Prerequisites

- Core container up and healthy (`sudo docker ps | grep selena-core`)
- Helsinki translator active (`translation.engine = helsinki` in `config/core.yaml`)
- Embedding model present at `intent.embedding_model_dir` (default: `/var/lib/selena/models/embedding/paraphrase-multilingual-MiniLM-L12-v2/`)
- Registry populated with representative devices — run [scripts/seed_missing_types.py](../scripts/seed_missing_types.py) if certain entity types are absent

### Execute

```bash
sudo docker exec -t selena-core python3 /opt/selena-core/tests/experiments/run_coverage_bench.py
```

Takes ~20 seconds on Jetson Orin / Pi 5. Output:

```
Accuracy: 1076/1114 (96.6%)

By category:
  plain         94/94  ████████████████████ 100.0%
  variety      358/383 ██████████████████    93.5%
  noise        437/441 ███████████████████   99.1%
  ...
```

A JSON summary lands at `_private/coverage_bench_results.json` (container-side path: `/opt/selena-core/_private/coverage_bench_results.json`). Copy to host:

```bash
sudo docker cp selena-core:/opt/selena-core/_private/coverage_bench_results.json \
               _private/coverage_bench_results.json
sudo chown $USER _private/coverage_bench_results.json
```

### Re-run in a loop while iterating

Each round of description / anchor / threshold tuning is cheap (~20 s bench + ~30 s core restart). Typical tuning loop:

```bash
# 1. edit embedding_classifier.py anchors OR intent description
# 2. restart core to pick up changes
sudo docker restart selena-core
until sudo docker ps --format '{{.Names}}: {{.Status}}' | grep -q 'selena-core.*healthy'; do
    sleep 3
done

# 3. run bench, tee log, copy JSON, diff against previous
TS=$(date +%H%M); LOG=_private/bench_runs/round_${TS}.log
sudo docker exec -t selena-core python3 /opt/selena-core/tests/experiments/run_coverage_bench.py 2>&1 | tee "$LOG"
sudo docker cp selena-core:/opt/selena-core/_private/coverage_bench_results.json _private/coverage_bench_results.json
sudo chown $USER _private/coverage_bench_results.json
python3 _private/compare_rounds.py     # prev vs current diff
```

---

## Visualising results

[scripts/render_bench_svg.py](../scripts/render_bench_svg.py) produces a scalable SVG summary from the latest JSON.

```bash
python3 scripts/render_bench_svg.py
# → _private/bench_viz/intent-bench.svg
```

The output lives under `_private/` (gitignored) because bench results are machine-local and change per registry. See below for publishing.

### Convert to PNG

No matplotlib / rsvg-convert dependency. Use headless Chrome:

```bash
cat > /tmp/wrap.html <<'HTML'
<!DOCTYPE html>
<html><head><style>*{margin:0;padding:0}html,body{background:#0d1117}</style></head>
<body><img src="/home/YOU/SelenaCore/_private/bench_viz/intent-bench.svg"/></body></html>
HTML

google-chrome --headless --no-sandbox --disable-gpu --hide-scrollbars \
    --device-scale-factor=2 --window-size=960,560 \
    --screenshot=/home/YOU/SelenaCore/_private/bench_viz/intent-bench.png \
    file:///tmp/wrap.html
rm /tmp/wrap.html
```

### Publishing the result

When you want to publish the current score (release notes, README badge, website):

1. Regenerate the SVG from the latest bench
2. Upload manually from `_private/bench_viz/` to your publishing target — **don't commit the binary into the repo**, since it goes stale as soon as the registry or classifier changes
3. For docs that reference a specific number, quote it as "as of v0.3.X" with a commit SHA — not a hardcoded value

---

## Interpreting the output

### Categories

| Category | What passing means |
|---|---|
| **plain** | Canonical phrasing is classified correctly. Any failure here is a critical description/anchor bug — should be 100%. |
| **variety** | Paraphrased phrasings (syn / polite / short / indirect / casual) classify correctly. Expect ~93–97%. |
| **noise** | Real-world STT noise (filler words, typos, stutter, context sentences, long lead-ins) doesn't throw the classifier. Expect ~99%. |
| **ambiguous** | User didn't say a room and ≥ 2 matching devices exist — the router injects `ambiguous=True` so the module can ask "which room?". Should be 100%. |
| **all_off** / **all_on** | Whole-house commands route to `house.all_*`, not individual `device.off`. Should be 100%. |
| **media** | Bare-verb playback commands (`pause`, `next`, `louder`) + named-station playback. ~85–95% — UK bare-verb forms suffer from Helsinki translation quirks. |
| **clock** / **weather** / **presence** / **automation** / **system** | Cross-module coverage. Clock / automation are the hardest because their intents share verbs; expect 85–95%. Weather is usually 100%. |
| **distractor** | Chat / nonsense — **must NOT** produce a device intent. **100% is a hard floor** — anything below means the classifier learned false-positive anchors. |

### Rolling accuracy floor

- **Overall ≥ 97.0%** — our current configuration; any drop below blocks a PR merge in principle (CI wiring is a TODO).
- **Per-new-intent ≥ 80%** on its own cases (≥ 60% when it shares verbs heavily with an existing intent).
- **Distractors = 100%** — no exceptions.

---

## When the bench fails

### Diagnosis flow

1. Read the per-category breakdown. If one category collapsed, it's a scoped regression — trace back to the last code change affecting that namespace.
2. Read the full failure list (up to 200 saved in the JSON). Patterns to look for:
   - **Same intent wins everywhere** → description or anchors for that intent got too broad, stealing matches.
   - **`unknown` everywhere in one category** → thresholds too strict OR Helsinki is mangling the UK source (run phrases through `get_input_translator().to_english()` manually to confirm).
   - **Entity extraction returns `None`** → keyword is missing from `ENTITY_MAP` in [embedding_classifier.py](../system_modules/llm_engine/embedding_classifier.py).
3. For UK failures specifically — compare the Helsinki output against your anchors. Mismatch there is the single most common root cause.

### Common fixes (ordered by likelihood)

1. Add missing anchor sentences (including Helsinki artifacts for UK)
2. Sharpen a description's IS / IS-NOT contrast
3. Add a post-processing override for systematic misroutes (use sparingly — see [intent-authoring.md](intent-authoring.md#7-post-processing-overrides))
4. Adjust a threshold (only if the cosine distribution shifted, e.g. after a model swap)
5. Merge two intents that share verbs (see [intent-authoring.md](intent-authoring.md#6-when-to-split-vs-merge-intents))

Each fix should be one change, followed by a re-run. If accuracy didn't improve by ≥ 0.5 pp, revert and try a different hypothesis — that discipline is what delivered the 57.5 → 96.6% climb across 20 rounds.

---

## Clarification bench (2-turn flow)

A companion runner lives at [tests/experiments/run_clarification_bench.py](../tests/experiments/run_clarification_bench.py). It tests the multi-turn clarification path independently of the main corpus:

```
turn 1   router.route(utterance_1)
         → IntentResult.clarification set (ambiguous / missing_param / low_margin)
turn 2   router.route_clarification(utterance_2, pending)
         → merged intent re-fires OR canned cancel
```

The two runners are separate on purpose (plan §R7): the main bench is stateless single-turn and would get contaminated by two-turn logic. Clarification fixtures live in [clarification_fixtures.py](../tests/experiments/clarification_fixtures.py) — 13 curated scenarios covering:

- Resolve by room (EN + UK, with morphology tolerance for UK noun cases)
- Resolve by positional reference (`"the first"` / `"перший"`)
- Resolve by device name (fuzzy)
- Resolve by numeric / word-form value (`"22"` / `"twenty-two"`)
- Allowed-value matching for set_mode / set_fan_speed
- Cross-language reply (EN question, UK answer)
- Fuzzy-fail → cancel (user says nonsense, assistant cancels gracefully)

### Running it

```bash
sudo docker exec -t selena-core python3 \
    /opt/selena-core/tests/experiments/run_clarification_bench.py
```

~5 seconds. JSON lands at `_private/clarification_bench_results.json`.

### Expected thresholds

- **≥ 80% overall** on the fixture list (currently 92.3%).
- Each new clarification feature should add ≥ 2 fixtures that exercise it. If adding a new `route_clarification` matcher and no new fixtures appear in the same PR, CI should reject it.

### Synthetic-pending fixtures

`missing_param` is emitted by module handlers (e.g. device-control's `_intent_to_state` ValueError trap), not by the router itself — so `route()` can't produce them in isolation. Fixtures for these scenarios carry a `synthetic_pending` dict that gets passed to `route_clarification()` directly, bypassing turn 1. This keeps the matcher logic (numeric extraction, allowed-value fuzzy match) covered without needing to stand up the full audio loop.

### What's NOT covered here

- Wake-word during `AWAITING_CLARIFICATION` cancels the pending context — this is audio-loop state-machine behaviour, requires mic-input simulation. Integration testing only.
- Real-time silence → `clarify.timed_out` — also audio-loop timing, not benchable.

Both covered by the **manual acceptance scenarios** in the plan; run them on live hardware before a release that touches this subsystem.
