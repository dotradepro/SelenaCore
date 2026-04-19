# Intent Routing — deep dive

> Companion to [voice-settings.md](voice-settings.md).
> Single source of truth for how SelenaCore classifies and dispatches user
> voice / text commands.
>
> Українська версія: [docs/uk/intent-routing.md](uk/intent-routing.md)
>
> The legacy regex FastMatcher, IntentCompiler pattern rows, IntentCache,
> composite device patterns and LLM-as-classifier tiers are **all removed**.
> Every request classifies fresh against the current DB state.

## 1. Pipeline at a glance

```
                                        ┌────────────────────┐
 Audio ─► Vosk / Whisper ─► text ─► ── │ InputTranslator    │ ─► English text
                                        │ (Argos / Helsinki) │
                                        └────────────────────┘

 ┌────────────────────────────────────────────────────────────┐
 │                        IntentRouter                        │
 │                                                            │
 │  Tier 0   Module Bus (WebSocket → user modules)    ~50 ms  │
 │  Tier 1   Embedding classifier (MiniLM-L6-v2)      ~50 ms  │
 │           cosine over per-utterance filtered catalog       │
 │  Tier 2   Assistant LLM (chat prompt, NO catalog) 300-800  │
 │           returns conversational reply, intent="unknown"   │
 │  Fallback deterministic "I didn't understand" phrase       │
 └────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    publish("voice.intent", payload)
                              │
                ┌─────────────┴─────────────┐
                ▼                           ▼
        System module                 VoiceCore speaks
        handles action                assistant / fallback reply
        + self.speak_action()
```

**Key source files:**

- `system_modules/llm_engine/intent_router.py` — tiers 0/1/2 orchestration
- `system_modules/llm_engine/embedding_classifier.py` — MiniLM-L6-v2 ONNX cosine
- `system_modules/llm_engine/intent_compiler.py` — live cache of `intent_definitions` rows
- `core/module_loader/system_module.py::_claim_intent_ownership` — static intent registration
- `core/api/helpers.py::on_entity_changed` — invalidation hook on device / station / scene CRUD

**Intents are classified by the embedding model, not by the LLM.** The LLM is a conversational fallback for utterances the classifier flagged as `unknown`. It sees no intent catalog and returns a natural-language reply, never an intent label.

## 2. Where intents come from

### 2.1 Static — `OWNED_INTENTS` + `_OWNED_INTENT_META`

Every system module declares its intents on the class:

```python
class WeatherServiceModule(SystemModule):
    name = "weather-service"

    OWNED_INTENTS = [
        "weather.current",
        "weather.forecast",
        "weather.temperature",
    ]

    _OWNED_INTENT_META: dict[str, dict] = {
        "weather.current": dict(
            noun_class="WEATHER", verb="query", priority=100,
            description=(
                "Report the CURRENT outdoor weather conditions "
                "(temperature + summary). Use for 'what's the weather' "
                "style questions. NOT for indoor AC / thermostat readings."
            ),
        ),
        # ... one entry per intent
    }

    async def start(self) -> None:
        self.subscribe(["voice.intent"], self._on_event)
        await self._claim_intent_ownership()   # idempotent
```

`SystemModule._claim_intent_ownership()` (in `core/module_loader/system_module.py`):

1. `UPDATE intent_definitions SET module=self.name WHERE intent IN OWNED_INTENTS` — claims any pre-existing rows.
2. `UPDATE description, entity_types` from `_OWNED_INTENT_META` — the module is the source of truth for the wording the classifier sees.
3. `INSERT` any missing rows from `_OWNED_INTENT_META`.

Runs on every module `start()` — a fresh container boot re-registers the whole catalog in under a second. Change a description in code, restart the container, and the next embedding classify sees the new wording.

### 2.2 Dynamic — devices / radio stations / scenes

There are **no dynamic intents**. Entities are *slots* on existing static intents, not new intent labels.

- `device.on` + `params.name="bedroom light"` — not a new `device.turn_on_bedroom_light` intent
- `media.play_radio_name` + `params.station_name="Radio Relax"` — not a new `media.play_radio_relax` intent

When a device / station is added via `POST /api/v1/devices` or `POST /api/ui/modules/media-player/radio`, the route calls `core.api.helpers.on_entity_changed(entity_type, id, action)` which:

1. `IntentCompiler.full_reload()` — rebuilds the in-memory intent catalog. The next embedding classify sees the full, fresh set of intents.
2. For `entity_type == "device"`: `PatternGenerator.rebuild()` refreshes the `name_en → device_id` lookup index used by `device-control` to resolve the classifier's `params.name` back to a real device.
3. Publishes `REGISTRY_ENTITY_CHANGED` on EventBus for any other module that wants to react.

## 3. Tier 1 — Embedding classifier

### 3.1 Per-utterance catalog filter

`IntentRouter._build_filtered_catalog(user_text, native_text)` assembles the candidate pool for a single request:

```
tokens = tokenize(user_text) ∪ tokenize(native_text)    # Unicode \w{3,}

Intents:
  for each intent in IntentCompiler.get_all_intents():
    if tokens ∩ (tokenize(description) ∪ tokenize(intent_name)) != ∅:
      include intent with 120-char-capped description
  always include "unknown" bail-out

Devices:
  for each device in registry:
    if tokens ∩ tokenize(name_en, name, location_en, location) != ∅:
      include device line (bilingual)

Radio stations:
  for each station:
    if tokens match name_user / name_en / genre_*:
      include station line

→ returns (catalog_text, allowed_intent_set)
```

The filter is bilingual: tokens from BOTH the English post-Argos text AND the original native text contribute to the match set. An utterance "вимкни лампу у спальні" still includes "bedroom light" in the filtered catalog — "спальня" hits the Ukrainian `meta.location` field of the bedroom light device.

### 3.2 Cosine similarity + confidence gates

`_parse_catalog_to_candidates(catalog_text)` extracts the `Intents:` block into a list of `{"name", "description"}` pairs. `EmbeddingIntentClassifier.classify(query, candidates)` runs a single MiniLM-L6-v2 forward pass over `[query, desc1, desc2, ...]` and returns `(intent, score, runner_up, margin, params)`.

Two config gates stop low-confidence picks (keys under `intent.*`):

| Key | Default | Meaning |
|---|---|---|
| `embedding_score_threshold` | `0.30` | Absolute cosine floor (query vs winner) |
| `embedding_margin_threshold` | `0.05` | Winner − runner-up |

Below either → `_embedding_classify` returns `None` → router falls through to Tier 2.

An **allowed-set guard** rejects any intent not in the filtered `allowed` set — defence against MiniLM picking a phrase that wasn't in the candidate list.

### 3.3 Post-processing for imperative verbs

`device.set_mode` / `device.set_temperature` sometimes beat `device.on` / `device.off` on close utterances like "turn on the air conditioning". A short heuristic flips the classifier's answer back to `device.on` / `device.off` when the user uttered a bare on/off verb WITHOUT a mode / value parameter. See `_ON_VERBS` / `_OFF_VERBS` in `intent_router.py::_embedding_classify`.

### 3.4 Why MiniLM and not the LLM

| | MiniLM-L6-v2 (ONNX) | Local LLM (phi-3-mini / qwen 1.5b) |
|---|---|---|
| Latency | ~50 ms | 300-2000 ms |
| Memory | ~30 MB | ~1-5 GB |
| Deterministic | yes — picks from candidate list | no — hallucinates intent names |
| Non-English | handled via translator + bilingual filter | classifier prompt-engineering nightmare |
| Runs on | any Pi / x86 / Jetson | GPU-only for sensible latency |

The classifier doesn't try to *understand* — it measures semantic similarity between the utterance and the description text. That's enough to pick the right intent and avoids every pitfall of small-model prompt engineering.

### 3.5 What to put in the `description`

The description text is the ONLY thing MiniLM sees. Two rules:

1. **Lead with the verb + noun the user is likely to say.** `"Turn a device on (light, switch, AC, curtain, vacuum)..."` beats `"Powers a device on."` because the user's utterance ("turn on") lands near "Turn" in embedding space.
2. **Add negatives for close-pair intents.** `device.query_temperature` and `weather.temperature` cosine closely to "what's the temperature". The clause *"Returns the live sensor value, NOT the outdoor weather forecast"* separates them.

Descriptions are capped at 120 chars in the filtered prompt block — concise beats verbose.

## 4. Tier 0 — Module Bus

User modules (type=UI / INTEGRATION / DRIVER) register their own intents via the WebSocket Module Bus. `IntentRouter.route()` asks the bus BEFORE running the embedding classifier — if any user module claims `handled=true`, that wins and the classifier never runs. This lets user modules override built-in behaviour (e.g. a custom weather module can grab `weather.current` away from `weather-service`).

See `core/module_bus/` and the module SDK docs for bus protocol details.

## 5. Tier 2 — Assistant LLM

`IntentRouter._ask_as_assistant(text)` is the LAST tier. Called only when:

- Tier 0 Module Bus missed, AND
- Tier 1 Embedding returned `unknown` or low confidence, AND
- `intent.llm_assistant_enabled` is `true` (default), AND
- A provider is configured (`voice.llm_provider` is set), AND
- Available RAM ≥ `llm.min_ram_gb` (default 5)

```python
reply = await llm_call(
    text,
    prompt_key="chat",        # loaded from PromptStore
    temperature=0.7,
    max_tokens=100,
    num_ctx=2048,
)
→ IntentResult(intent="unknown", response=reply, source="assistant")
```

**The LLM never sees the intent catalog.** The chat prompt is a system prompt like "You are a helpful home-assistant. Keep answers short..." — the user gets a human reply instead of a robotic "I didn't understand", but no new intent is ever created.

If the LLM returns empty or the tier is disabled, the router returns `IntentResult(intent="unknown", response="<deterministic phrase>", source="fallback")`.

## 6. EventBus delivery

`IntentRouter.route()` publishes `voice.intent` with the classification result. Every system module that owns intents in that namespace subscribes to this event and handles its own:

```python
async def _on_voice_intent(self, event):
    payload = event.payload or {}
    if payload.get("intent") not in self.OWNED_INTENTS:
        return
    # ... execute action, then:
    await self.speak_action(payload["intent"], {"result": "ok", ...})
```

`speak_action()` defers the TTS wording to VoiceCore's rephrase LLM so the reply ends up in the user's native language even though the classifier ran on English.

## 7. Entity resolution

For intents that act on / read from a specific entity (device, station, scene), the classifier's `params.name` comes straight out of the utterance ("bedroom light"). Device-control's `_resolve_device()` uses `PatternGenerator.get_device_id_by_name()` to map the name back to a `device_id` in O(1). Ambiguous names (two devices sharing the same `name_en`) fall back to `params.location` disambiguation.

Radio stations / scenes go through `IntentRouter._resolve_entity_ref()` which looks up `RadioStation` / `Scene` by `name_user` or `name_en` and injects `params.entity_ref` for the handler.

## 8. What was removed

The old architecture had five tiers. Everything except Module Bus and the LLM-as-chat is **gone**:

| Removed | Replaced by |
|---|---|
| FastMatcher regex (`IntentCompiler.match()`, `_flat_en`, verb buckets, pattern specificity scoring) | Embedding classifier |
| `intent_patterns` regex rows, composite device patterns | Embedding classifier reads `intent_definitions.description` directly |
| `PatternGenerator.rebuild_composite_device_patterns()` | `PatternGenerator.rebuild()` maintains a plain name → device_id index |
| IntentCache + `auto_learned` hot-phrase promotion | Fresh classify on every request (no stale entity pointers) |
| LLM-as-classifier with dynamic registry-aware prompt | LLM is a chat fallback only, no catalog in prompt |
| `config/intents/` directory, `definitions.yaml`, `vocab/*.yaml` | `OWNED_INTENTS` + `_OWNED_INTENT_META` on each module class |
| `scripts/seed_intents_to_db.py` | `_claim_intent_ownership()` in `SystemModule` base class |
| `intent_cache.db`, hourly promotion loop in `core/main.py` lifespan | — |

What survived:

- `intent_definitions` table: static catalog, written by `_claim_intent_ownership()`, consumed by `IntentCompiler.get_all_intents()`.
- `IntentCompiler`: reduced to a live cache of `intent_definitions` rows.
- `PatternGenerator`: reduced to maintaining the `name → device_id` lookup index for entity resolution.
- `on_entity_changed`: unchanged trigger point on CRUD — now only refreshes the IntentCompiler cache and the PatternGenerator index.
