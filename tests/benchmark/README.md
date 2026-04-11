# Intent Classification Benchmark

Validates the unified `system` prompt on different Ollama models and reports
accuracy, latency and JSON-output validity.

## What it measures

| Metric | Meaning |
|---|---|
| `intent_acc` | % of cases where the model returned the expected intent string |
| `params_acc` | % of cases where expected params (entity, location, value, ...) matched |
| `json_valid` | % of raw outputs that parsed as valid JSON (pre-parser) |
| `p50 ms` / `p95 ms` | Round-trip latency through `core.llm.llm_call()` |

## Running

```bash
docker compose exec core python tests/benchmark/run_intent_bench.py \
    --corpus tests/benchmark/intent_corpus.jsonl \
    --models qwen2.5:0.5b,qwen2.5:1.5b,qwen2.5:3b,phi3-mini:3.8b,gemma3:1b \
    --runs 1 \
    --out /tmp/intent_bench.json
```

Models must already be pulled into the Ollama volume (`ollama pull qwen2.5:1.5b`
on the host). The benchmark reuses `core.llm.llm_call` so the prompt, catalog
injection and JSON mode match production exactly.

## Expected results (reference targets)

| Model | Target `intent_acc` | Target `json_valid` | Target p95 |
|---|---|---|---|
| `qwen2.5:1.5b` (default) | ≥ 85% | ≥ 95% | ≤ 800 ms |
| `qwen2.5:3b` | ≥ 92% | 100% | ≤ 1200 ms |
| `qwen2.5:0.5b` (low-RAM) | ≥ 70% | ≥ 90% | ≤ 400 ms |
| `phi3-mini:3.8b` | comparative — keep only if it beats `1.5b` |
| `gemma3:1b` | flagged "not recommended" if `json_valid < 80%` |

Ship-gate rule: the production default (`voice.llm_model` in `core.yaml`) must
meet the target row for its size class.

## Corpus

`intent_corpus.jsonl` — 32 cases covering every namespace:

- `device.on` / `device.off` / `device.set_level` / `device.set_temperature`
- `device.query_temperature` / `device.query_state`
- `device.lock` / `device.unlock`
- `media.play_genre` / `media.pause` / `media.stop` / `media.resume` / `media.next` / `media.volume_up` / `media.whats_playing`
- `weather.current` / `weather.forecast`
- `clock.set_alarm` / `clock.set_timer` / `clock.stop_alarm`
- `privacy_on` / `privacy_off`
- `automation.run`
- `presence.who_home`
- `chat` (freeform questions)

All cases are in English — this benchmark exercises the post-translator path.
Non-English handling is validated separately via Argos round-trip tests.

## Adding cases

One JSON object per line:

```json
{"text":"turn on the light","expected":{"intent":"device.on","params":{"entity":"light"}}}
```

Only the intent string must be exact; params are checked case-insensitively
key-by-key, and keys not listed in `expected.params` are ignored.
