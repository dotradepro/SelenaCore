# Intent Routing — Architecture Deep Dive

> Companion to [voice-settings.md](voice-settings.md), [architecture.md](architecture.md)
> and [system-module-development.md](system-module-development.md). This file is the
> single source of truth for HOW SelenaCore turns a voice utterance into a module
> action — the other docs link here instead of duplicating it.
>
> Українська версія: [docs/uk/intent-routing.md](uk/intent-routing.md)

## 1. Pipeline at a glance

```
  audio (arecord)
       │
       ▼
  Vosk STT  ────►  text + stt_lang
       │
       ▼
  ┌─────────────────────────────────────────────────────────────┐
  │  IntentRouter.route(text, lang)                              │
  │                                                              │
  │  Tier 1   FastMatcher (DB regex, English-only)      ~0 ms   │
  │  Tier 2   Module Bus (user modules, WebSocket)      ~ms     │
  │  Cache    IntentCache (SQLite, prev LLM hits)       ~10 ms  │
  │  Tier 3   Local LLM (Ollama, single call)           300-800 │
  │  Tier 4   Cloud LLM (OpenAI-compatible, optional)   1-3 sec │
  │  Fallback "not understood"                                  │
  └─────────────────────────────────────────────────────────────┘
       │
       ▼  EventBus: voice.intent { intent, params, source }
  Module owning the intent executes
       │
       ▼
  Dual Piper TTS  →  speaker
```

**Key invariants**

- The **whole pipeline operates on English** internally. Since v0.4 the
  translation is done by [Argos Translate](translation.md) at the edges
  of the pipeline (after Vosk STT, before Piper TTS), not by the LLM.
- IntentRouter receives **already-English text** and emits an English
  `intent` + English `params.location` / `params.entity` + an English
  `response`. There are no Ukrainian / Russian / German FastMatcher
  patterns and there never will be (`IntentCompiler.match()` only walks
  `patterns["en"]` by design).
- The TTS *response* language is handled by `OutputTranslator`
  (en→target_lang) right before `preprocess_for_tts` and Piper.
- All routing tiers go through `IntentRouter` and emit one `voice.intent`
  event with a uniform payload shape.

**Translation can be disabled.** When `translation.enabled=false` or
the user runs an English-only setup (Vosk EN + Piper EN), both
translators short-circuit (~0 ms passthrough). The system then expects
text in English directly from Vosk.

## 2. Where intents come from

There are exactly two kinds of intents:

| Kind | Owner | Lifecycle | Example |
|------|-------|-----------|---------|
| **Hard intents** | A module declares them at startup | Re-asserted on every `module.start()` | `device.on`, `device.set_temperature`, `media.pause`, `clock.set_alarm` |
| **Dynamic intents** | `PatternGenerator` builds them from registry rows | Rebuilt on entity CRUD | `media.play_radio_name` for "Hit FM", `device.on` composite for the live device list |

There is **no central seed file** for hard intents. The `scripts/seed_intents_to_db.py` script seeds a few legacy weather / privacy rules and is being phased out — modules are the source of truth for what they can do.

### 2.1 Hard intents — how a module declares them

Each system module exposes a `_OWNED_INTENT_META` dict and a `_claim_intent_ownership()` method. On `start()` the module:

1. Updates `intent_definitions.module = <self.name>` for every name in `OWNED_INTENTS` (claiming any rows that already exist)
2. **Inserts missing rows** with the metadata from `_OWNED_INTENT_META` (description, noun_class, verb, priority)

This makes a module fully self-sufficient — uninstalling and reinstalling restores its catalog. See [system_modules/device_control/module.py](../system_modules/device_control/module.py) for the canonical implementation.

```python
# system_modules/device_control/module.py (excerpt)
_OWNED_INTENT_META: dict[str, dict] = {
    INTENT_QUERY_TEMPERATURE: dict(
        noun_class="CLIMATE", verb="query", priority=100,
        description=(
            "Read the CURRENT temperature reported by an indoor climate "
            "device (air conditioner / thermostat) in a specific room. "
            "Returns the live sensor value, NOT the outdoor weather forecast."
        ),
    ),
    ...
}
```

A hard intent **does not need any FastMatcher pattern**. It's enough to land in `intent_definitions` — the LLM tier (Tier 3) will see it in the dynamic catalog (`IntentCompiler.get_all_intents()` returns rows with zero compiled patterns) and pick it for natural-language utterances. This is exactly how `device.query_temperature` works today.

### 2.2 Dynamic intents — composite device patterns

For the device registry, `PatternGenerator.rebuild_composite_device_patterns()` produces **at most 5 rows** for the entire registry, regardless of how many devices are registered:

| Row | Verbs covered | Devices |
|-----|---------------|---------|
| `device.on` composite | turn on, switch on, enable | every device with `meta.name_en` |
| `device.off` composite | turn off, switch off, disable | every device with `meta.name_en` |
| `device.set_temperature` composite | set X to N | climate (`thermostat` / `air_conditioner`) only |
| `device.lock` composite | lock, secure, shut | locks (`lock` / `door_lock`) only |
| `device.unlock` composite | unlock, open | locks only |

Each composite pattern uses a **named-group alternation** of all known device names, sorted longest-first so multi-word names beat their prefixes:

```regex
^(?:turn\s+on|switch\s+on|enable)
 \s+(?:the\s+)?
 (?P<name>air\ conditioner|kitchen\ light|bedroom\ lamp|...)
 (?:\s+(?:in|on)\s+(?:the\s+)?(?P<location>living\ room|kitchen|...))?
 \s*\??$
```

Old per-device rows are wiped on every rebuild, so adding or removing a device is a single SQL transaction. Radio stations and scenes still use per-entity patterns — their text varies more and they don't suffer from the same row explosion.

### 2.3 Resolving the matched device

When FastMatcher hits a composite row, the captured `(?P<name>...)` group is mapped to a concrete `device_id` in O(1) via an in-memory index built during the rebuild:

```python
gen = get_pattern_generator()
device_id = gen.get_device_id_by_name("air conditioner")  # → uuid or None
```

Two devices sharing the same `meta.name_en` (e.g. two `lamp`s in different rooms) **collide**. The collision is detected at rebuild time:

- `_device_name_index` only contains **unique** names → `get_device_id_by_name()` returns `None`
- `_ambiguous_names` (set) holds the colliding names
- `is_ambiguous_name(name)` reports whether disambiguation is needed

`device-control._on_voice_intent` checks both. For unique names it injects `params["device_id"]` and uses the `_resolve_device` fast path. For ambiguous names it injects `params["name_en"]` instead, and `_resolve_device`'s **tier-0** path searches the registry for `meta.name_en == name AND (location matches user-language OR meta.location_en)`. If still ambiguous, the resolver returns `None` and the user hears "I can't find a climate device in the bedroom."

## 3. FastMatcher (Tier 1) — `IntentCompiler`

Source: [system_modules/llm_engine/intent_compiler.py](../system_modules/llm_engine/intent_compiler.py)

`IntentCompiler` reads `intent_definitions` + `intent_patterns` from SQLite, compiles each pattern into a `re.Pattern`, and exposes `match(text, lang)` for the router.

### 3.1 Pattern ordering — `(priority DESC, specificity DESC)`

Patterns at the same `priority` were previously matched in undefined order. The compiler now scores each pattern with `_pattern_specificity()`:

| Feature | Score |
|---------|-------|
| Raw length | +1 per character |
| Named group `(?P<...>...)` | +50 each |
| End anchor `$` / `\Z` | +30 |
| Start anchor `^` / `\A` | +30 |
| Word boundary `\b` | +20 |
| Non-capturing group `(?:...)` | +10 |
| Greedy wildcard `.*` / `.+` | -5 |

All English patterns are flattened into `_flat_en` sorted by `(-priority, -specificity)`. This means a parameterised pattern like `set\s+...\s+(?P<level>\d+)$` always beats a loose `set\s+...` even when both share priority 100.

### 3.2 Verb-bucket pre-filter

A typical voice command starts with one of ~20 verbs (`turn`, `set`, `play`, `what`, `how`, `lock`, …). `_VERB_BUCKETS` maps each verb to its candidate intents:

```python
_VERB_BUCKETS = {
    "turn":   ("device.on", "device.off"),
    "switch": ("device.on", "device.off", "device.set_mode"),
    "set":    ("device.set_temperature", "device.set_fan_speed",
               "device.set_mode", "clock.set_alarm", "clock.set_timer"),
    "what":   ("weather.current", "weather.temperature",
               "device.query_temperature", "media.whats_playing"),
    ...
}
```

`_async_load()` builds `_buckets_en[verb] → list[(prio, spec, intent, entry)]` once. `match()` reads the input's first word, tries the matching bucket first (typical size: 3-15 patterns) and only falls back to the full `_flat_en` walk (107+ patterns) if the bucket misses.

Real measurements on a 46-intent / 107-pattern registry:

| First word | Bucket size | Old scan |
|---|---|---|
| `turn` | 4 | 107 |
| `set` | 11 | 107 |
| `play` | 14 | 107 |
| `what` | 14 | 107 |

Words missing from `_VERB_BUCKETS` fall through to the full scan, so omissions only cost performance, never correctness.

### 3.3 Pattern-less intents are still in the catalog

Hard intents like `device.query_temperature` may have zero compiled patterns. `IntentCompiler` keeps them in `_compiled` (and therefore in `get_all_intents()`) so the LLM tier sees them in its dynamic catalog. They are simply absent from `_flat_en` and `_buckets_en` — the FastMatcher never tries them, but the LLM does.

## 3.5. Embedding Classifier (Tier 1.5) — `EmbeddingIntentClassifier`

Source: [system_modules/llm_engine/embedding_classifier.py](../system_modules/llm_engine/embedding_classifier.py)

Added 2026-04-12. Runs **after** the token-filtered catalog is built
but **before** the LLM tier. Uses `sentence-transformers/all-MiniLM-L6-v2`
(22 MB, encoder-only) to compare the query against pre-computed anchor
centroids for each candidate intent. Confident hits short-circuit the
LLM call entirely.

### 3.5.1 Pipeline position

```
Vosk STT (any language)
  → Helsinki translator → English text
  → Token filter         → 3-15 candidate intents
  → Embedding classifier → cosine over anchor centroids     ← THIS TIER
      ↓ confident hit (score ≥ 0.30, margin ≥ 0.02)
      → return IntentResult(source="embedding")             ~50-200 ms
      ↓ low score / low margin / returns None
  → Local LLM qwen        (Tier 2 fallback)                ~2-5 s
```

### 3.5.2 How it works

1. **Warmup** (once at boot, ~15 s on Jetson, ~30 s on Pi 5):
   pre-compute a mean embedding centroid for every intent in
   `INTENT_ANCHORS` + cache it.

2. **Per-request** (~40-150 ms):
   - Encode the query with MiniLM
   - For each candidate intent: combine its cached anchor centroid
     with its live description embedding
   - Pick the highest cosine-similarity intent
   - If `score >= 0.30` and `margin >= 0.02` → return
   - Otherwise → fall through to LLM

3. **Command segment extraction**: for long phrases (>8 words),
   `_extract_command_segment()` splits on conjunctions and picks the
   clause containing a command verb. This prevents context noise
   ("I just got home and it is cold") from diluting the intent signal.

4. **Params**: lexicon-based `extract_params()` with entity map,
   room keywords, value keywords, word-to-number conversion, and
   genre list. Handles Helsinki translation artifacts
   (`"air conditioning"` → `air_conditioner`).

### 3.5.3 Anchor strategy

`INTENT_ANCHORS` is the single source of truth for what the embedding
model considers representative of each intent. Two rules:

1. **Include real Helsinki outputs**, not just clean English. The
   classifier sees production translation noise, not idealised text.
   Example: `"вмикни джазове радіо"` → Helsinki → `"Turn on the jazz
   radio."` — this exact string must be an anchor for `media.play_genre`.

2. **Include negative anchors for `unknown`**. Without them the
   classifier has no concept of "weird input". Anchors like
   `"xyzzy plover quux"`, `"tell me a joke"`, `"who are you"` push
   the unknown centroid into a distinct region of the embedding space.

When a module owner adds a new intent, they should add ≥3 anchors
including at least one expected Helsinki output.

### 3.5.4 Config

```yaml
intent:
  embedding_enabled: true           # master toggle
  embedding_score_threshold: 0.30   # below → fall through to LLM
  embedding_margin_threshold: 0.02  # winner - runner_up below → fall through
```

### 3.5.5 Benchmark results

Tested on 40-case canonical corpus + 40-case noisy corpus (filler
words, STT stutter, long contextual sentences, typos):

| Platform | Canonical | Noisy | Embedding % | LLM % |
|---|---|---|---|---|
| Jetson Orin (8 GB) | **40/40 (100%)** p50=111ms | **35/40 (87.5%)** | 95% | 2.5% |
| Raspberry Pi 5 (16 GB) | **38/40 (95%)** p50=78ms | **33/40 (82.5%)** | 82% | 1.25% |

LLM is called for <3% of queries. The remaining ~15-20% that
embedding doesn't handle are `unknown` cases that resolve correctly
via the deterministic fallback path (no LLM needed).

### 3.5.6 Full model comparison (40-case canonical, Jetson Orin)

| Config | Accuracy | p50 | Model size |
|---|---|---|---|
| tinyllama 1.1b | 50.0% | 1103 ms | 600 MB |
| qwen 0.5b | 50.0% | 2131 ms | 400 MB |
| qwen 1.5b + prompt opt | 87.5% | 2548 ms | 1 GB |
| qwen 3b | 90.0% | 2854 ms | 2 GB |
| gemini-2.5-flash-lite (cloud) | 92.5% | 856 ms | — |
| **MiniLM-L6-v2 embedding + LLM fallback** | **100%** | **111 ms** | **22 MB** |

## 4. IntentCache (between Tier 2 and Tier 3)

Source: [system_modules/llm_engine/intent_cache.py](../system_modules/llm_engine/intent_cache.py)

Every successful LLM classification is stored in a SQLite table keyed by `(text, lang)` with a `hit_count`. On the next identical utterance the cache returns the cached `intent` + `params` directly, skipping the LLM round-trip entirely (~10 ms vs 300-800 ms).

### 4.1 Hot-phrase promotion

Once an entry has been hit `>= 5` times, the **promotion loop** turns it into a real FastMatcher row:

- `IntentCache.promote_frequent_to_patterns()` runs from `core/main.py` once per hour
- Each promoted row uses `source='auto_learned'` and `entity_ref='cache:promoted'`, namespaced separately from `auto_entity` so PatternGenerator's composite rebuilds don't touch them
- The pattern is an anchored literal: `^<re.escape(text)>\??$`
- After promotion, `IntentCompiler.full_reload()` is called and the next utterance hits Tier 1 in ~0 ms

**English-only by design.** The cache still records non-ASCII utterances for cache hits, but the promotion step skips them with a log message — the FastMatcher would never read them anyway. Ukrainian / Russian / German queries keep paying the LLM cost on first encounter, then the IntentCache cost (~10 ms) on subsequent ones.

## 5. LLM tier (Tier 3) — compact English prompt

Source: [system_modules/llm_engine/intent_router.py](../system_modules/llm_engine/intent_router.py) — `_load_db_catalog_compact()` and `_build_intent_catalog()`.

Since v0.4 the local LLM prompt is **compact and English-only**. The
base prompt is ~200 tokens (loaded from `intent_system` in the prompt
store) plus a tiny dynamic catalog appended at runtime. Total prompt
size is typically **300-500 tokens** — fits comfortably in 2 K context
windows of qwen2.5:0.5b/1.5b/3b.

### 5.1 What the catalog contains

1. **Intents grouped by namespace** — pipe-separated actions:
   ```
   Intents: device.on|off|set_temperature|set_mode|lock|unlock,
            media.play|stop|pause|volume_set, weather.query,
            clock.set_alarm|set_timer, presence.query
   ```
2. **Module-extra intents** — module intents not already in the
   IntentCompiler list (same compact namespace format).
3. **Rooms with device types**:
   ```
   Rooms: living room (air_conditioner, light), bedroom (light)
   ```
4. **Radio stations** — flat list of `name_en` for media-player.

No verbose descriptions, no language directives, no per-language
examples. The whole catalog is typically **100-300 tokens**.

### 5.2 Why compact?

- Local 1-3B models on Pi 5 / Jetson Nano have effective context
  windows of 2 K-4 K tokens
- Verbose intent descriptions don't help small models — the **name**
  carries enough signal for classification
- Translation handled at the edges means no language-mixing in the
  prompt
- The base intent_system prompt holds 5 short English example
  classifications which give the model the JSON shape to follow

### 5.3 Size guards

Two module-level constants cap catalog growth:

```python
_DEVICES_PER_ROOM_LIMIT = 10
_ROOMS_LIMIT = 30
```

A 60-device / 35-room house keeps the prompt under ~1 KB extra.

### 5.4 Test results on Raspberry Pi 5 (qwen2.5:3b)

| Command | Intent (correct?) | Latency (warm) |
|---------|-------------------|----------------|
| turn on the light in the office | device.on, office ✓ | 9 s |
| turn off the air conditioner in living room | device.off, living room ✓ | 6 s |
| what is the temperature in living room | device.query_temperature, living room ✓ | 7 s |
| what is the weather | weather.query ✓ | 5 s |
| set timer for 5 minutes | clock.set_timer ✓ | 7 s |
| who is home | presence.query ✓ | 8 s |
| play music | media.play ✓ | 5 s |
| tell me a joke | unknown ✓ | 6 s |

**8/9 correct** with ~5-9 s warm response. Cold start (first call after
restart) is ~30-45 s due to model load into RAM.

### 5.2 Why this is enough — no hardcoded room mappings

In an earlier iteration there was an explicit `_OUTDOOR_TO_INDOOR_INTENT = {"weather.temperature": "device.query_temperature"}` map plus a Ukrainian morphology heuristic. Both were removed: with the registry-aware prompt the LLM picks the right intent on its own.

Verified end-to-end with Ollama on the test rig:

| Input | Result |
|-------|--------|
| "Яка температура у вітальні?" (uk) | `device.query_temperature` + `location=living room` |
| "Яка температура надворі?" (uk) | `weather.temperature` |
| "увімкни кондиціонер у вітальні" (uk) | `device.on` + `location=living room` + `entity=air_conditioner` |
| "what is the temperature in the bedroom" (en) | `device.query_temperature` + `location=bedroom` |
| "what is the temperature outside" (en) | `weather.temperature` |

No hardcoded mapping. The LLM reads the registry, sees that `living room` has an `air_conditioner`, and routes accordingly.

## 6. Voice → action lifecycle

```
1. Vosk:           audio → "Яка температура у вітальні?"
2. IntentRouter.route(text, lang="uk")
   ├─ Tier 1 FastMatcher       → miss (no Ukrainian patterns)
   ├─ Tier 2 Module Bus        → miss
   ├─ IntentCache              → miss (first time)
   ├─ Tier 3 Local LLM         → {"intent":"device.query_temperature",
   │                              "entity":"air_conditioner",
   │                              "location":"living room"}
   └─ IntentCache.put()        → cached for next time
3. EventBus publish "voice.intent" with the IntentResult payload
4. device-control._on_voice_intent
   ├─ intent = "device.query_temperature" → branch to _handle_query_temperature
   ├─ _resolve_device(entity_filter=("air_conditioner","thermostat"),
   │                  params={location:"living room"})
   ├─ device.state["current_temp"] = 22
   └─ speak_action("device.query_temperature",
                   {result:"ok", temperature:22, location:"living room"})
5. VoiceCore rephrase LLM      → "У вітальні зараз 22 градуси"
6. Piper TTS                   → audio
```

The same utterance on the second call goes Tier 1 → IntentCache hit (~10 ms) → device-control. After 5 hits and an hour of uptime, a corresponding `auto_learned` row appears and Tier 1 FastMatcher answers the third encounter at ~0 ms (English only).

## 7. Scaling envelope

| Metric | Comfortable | Practical limit |
|--------|-------------|-----------------|
| Hard intents in catalog | 100 | 150 (LLM context budget) |
| Devices in registry | 150 | 300-500 with `gemma2:9b` |
| Rooms in house | 30 | 50 with raised limits |
| Devices per room | 10 | 15 |
| Distinct unique `name_en` | 50 | 200 (regex compile time) |
| FastMatcher patterns total | 200 | 1000 |
| Latency on FastMatcher hit | ~0 ms | ~5 ms |
| Latency on cache hit | ~10 ms | ~30 ms |
| Latency on LLM hit | ~500 ms | ~1500 ms (cloud) |

Beyond ~500 devices the architecture needs hierarchical routing (per-floor sharding) — that's an enterprise / building-automation scope, not a single-house smart-home one.

## 8. Adding a voice command to your module

1. Pick a unique intent name in your module's namespace: `mymodule.do_thing`.
2. Add it to `OWNED_INTENTS` and `_OWNED_INTENT_META` in your module class.
3. Subscribe to `voice.intent` in `start()` and dispatch your intent name in the handler.
4. Use `self.speak_action(intent, context)` to let VoiceCore's rephrase LLM produce the natural-language reply in the user's TTS language.
5. **Do NOT** add patterns to the seed script. **Do NOT** create files under `config/intents/` (that path is dead). Hard intents live in the module that owns them.

If you also want a 0 ms FastMatcher shortcut for English commands, write a regex into `intent_patterns` with `source='manual'` and your intent_id — but it's optional. The LLM tier handles natural language (in any language) without any patterns.

See [system-module-development.md](system-module-development.md) for the worked example with file paths and full code.

## 9. Upgrading translator quality

The voice pipeline uses Argos Translate (opus-mt under the hood) for
input normalisation. For some language pairs the default Argos package
is an older / smaller opus-mt checkpoint and you can see systematic
mistakes on single-word commands (dropped verbs, article insertion,
register shifts). The router compensates with:

- **Grammar normalisation before MT** — [`_normalize_for_mt`](../core/translation/local_translator.py)
  adds a capitalised first letter and a trailing period to every Vosk
  utterance before Argos runs. NMT models were trained on normal
  written sentences, so this pays +3-5 BLEU on every language pair
  with zero per-language code.
- **Bilingual catalog filter** — the router tokenises BOTH the
  original utterance and the Argos output, so a misfire on the verb
  still surfaces the right devices via matches on the native room/
  device name. See [`_build_filtered_catalog`](../system_modules/llm_engine/intent_router.py).

If that is still not enough, you can upgrade the installed Argos
package to an `opus-mt-tc-big-<src>-en` variant:

| Default package | Recommended upgrade | Size delta | Quality |
|---|---|---|---|
| `opus-mt-uk-en` | `opus-mt-tc-big-uk-en` | ~300 MB → ~1.2 GB | +10-15 BLEU |
| `opus-mt-ru-en` | `opus-mt-tc-big-ru-en` | similar | similar |
| `opus-mt-de-en` | `opus-mt-tc-big-de-en` | similar | similar |

The `tc-big` checkpoints come from the Tatoeba Challenge and are
trained on significantly more data. Install them via **Settings →
Translation → Install package** if the upgrade is available in the
Argos Package Index, or sideload by dropping the `.argosmodel` file
into `/var/lib/selena/argos-packages/` and restarting the core.

### Optional: switch to transformers + MarianMT directly

Argos under the hood is just pre-converted opus-mt models. For maximum
quality you can bypass Argos entirely and use `transformers` +
`MarianMTModel` with `num_beams=4` beam search. That adds a ~2 GB
dependency (`transformers` + `torch`), but gives:

- Up-to-date model checkpoints (Argos lags behind Helsinki-NLP releases)
- Beam search instead of greedy decoding (+3-5 BLEU)
- Full control over preprocessing / postprocessing
- Any model from the Helsinki-NLP HuggingFace org, not just the ones
  Argos packaged

This is *not* wired up in the default build because of the disk
footprint. If you want it, write a `HelsinkiTranslator` class under
`core/translation/` that exposes the same interface as
[`InputTranslator`](../core/translation/local_translator.py)
(`to_english(text, source_lang) -> str`), then route
`voice_core/_process_command` through it instead.

```python
from transformers import MarianMTModel, MarianTokenizer

class HelsinkiTranslator:
    def __init__(self, models_dir="/var/lib/selena/models/translate"):
        self._cache = {}
        self._models_dir = Path(models_dir)

    def _get_model(self, lang_pair):
        if lang_pair not in self._cache:
            local = self._models_dir / lang_pair
            source = str(local) if local.exists() else f"Helsinki-NLP/opus-mt-{lang_pair}"
            tok = MarianTokenizer.from_pretrained(source)
            mdl = MarianMTModel.from_pretrained(source)
            self._cache[lang_pair] = (mdl, tok)
        return self._cache[lang_pair]

    def to_english(self, text, source_lang):
        from core.translation.local_translator import _normalize_for_mt
        text = _normalize_for_mt(text)
        mdl, tok = self._get_model(f"{source_lang}-en")
        tokens = tok([text], return_tensors="pt", padding=True)
        out = mdl.generate(**tokens, num_beams=4)
        return tok.decode(out[0], skip_special_tokens=True)
```

Note the re-use of `_normalize_for_mt` — it's language-agnostic and
works exactly the same way for any MT backend.

## 10. References

- Source: [system_modules/llm_engine/intent_router.py](../system_modules/llm_engine/intent_router.py)
- Source: [system_modules/llm_engine/intent_compiler.py](../system_modules/llm_engine/intent_compiler.py)
- Source: [system_modules/voice_core/action_phrasing.py](../system_modules/voice_core/action_phrasing.py) — registry + `register_formatter`
- Source: [core/translation/local_translator.py](../core/translation/local_translator.py) — `_normalize_for_mt`
- Source: [system_modules/device_control/module.py](../system_modules/device_control/module.py) — canonical hard-intent + `_OWNED_INTENT_META.entity_types`
- Related docs: [voice-settings.md](voice-settings.md), [architecture.md](architecture.md), [system-module-development.md](system-module-development.md), [climate-and-gree.md](climate-and-gree.md)
