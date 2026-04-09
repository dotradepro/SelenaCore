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

- The **whole pipeline operates on English** internally. Non-English input is translated by Tier 3 (LLM) into an English `intent` + English `params.location` / `params.entity`. There are no Ukrainian / Russian / German FastMatcher patterns and there never will be (`IntentCompiler.match()` only walks `patterns["en"]` by design).
- The TTS *response* language is independent — it follows `voice.tts.primary.lang`.
- All routing tiers go through `IntentRouter` and emit one `voice.intent` event with a uniform payload shape.

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

## 5. LLM tier (Tier 3) — dynamic registry context

Source: [system_modules/llm_engine/intent_router.py](../system_modules/llm_engine/intent_router.py) — `_load_db_catalog()` and `_build_intent_catalog()`.

The local LLM (Ollama) prompt is rebuilt on every device CRUD. It contains:

1. **Registered intents** — every row in `intent_definitions`, with name, description, params schema. Includes pattern-less hard intents.
2. **Connected modules** — each user / system module's display name and intent list.
3. **Devices by room** — grouped by `meta.location_en` with `entity_type: name_en` per device:
   ```
   Devices by room (use the room name to scope intents):
     bedroom: light: bedside lamp, light: ceiling light
     kitchen: outlet: kettle, light: kitchen light
     living room: air_conditioner: air conditioner
   ```
4. **Known indoor rooms** — distinct list with the routing rule:
   > "If the user names any of these rooms, choose an intent that acts on or reads from a device in that room. Pick a non-room/global intent only when the user does NOT name any known room or explicitly says 'outside' / 'outdoor' / 'globally'."
5. **Radio stations / scenes** — for media-player and automation-engine.
6. **TTS language directive** — `Response MUST be in <lang>`.

### 5.1 Prompt size guards

Two module-level constants prevent the prompt from growing without bound:

```python
_DEVICES_PER_ROOM_LIMIT = 10
_ROOMS_LIMIT = 30
```

A house with 60 devices in 35 rooms produces:
- Up to 30 visible rooms × 10 devices each = 300 entries
- `... (5 more rooms omitted)` footer for the rest
- Each room line truncates with `(+N more)` if it has more than 10 devices

This caps the catalog at roughly 3-5 KB of prompt text — comfortable for a 4 K-context model like `phi-3-mini`, with headroom for `gemma2:9b` to handle ~150 intents at full clarity.

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

## 9. References

- Source: [system_modules/llm_engine/intent_router.py](../system_modules/llm_engine/intent_router.py)
- Source: [system_modules/llm_engine/intent_compiler.py](../system_modules/llm_engine/intent_compiler.py)
- Source: [system_modules/llm_engine/intent_cache.py](../system_modules/llm_engine/intent_cache.py)
- Source: [system_modules/llm_engine/pattern_generator.py](../system_modules/llm_engine/pattern_generator.py)
- Source: [system_modules/device_control/module.py](../system_modules/device_control/module.py) — canonical hard-intent + composite resolver
- Related docs: [voice-settings.md](voice-settings.md), [architecture.md](architecture.md), [system-module-development.md](system-module-development.md), [climate-and-gree.md](climate-and-gree.md)
