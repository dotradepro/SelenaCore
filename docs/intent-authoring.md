# Writing Voice Intents That Actually Get Recognized

This guide codifies the rules we discovered while moving the intent classifier from 57.5% to 96.6% accuracy across ~20 iterations. Every rule below was learned from a specific regression — follow them and your intent will land at ≥ 90% from day one.

Applies to **both user modules** (`modules/*/main.py` with `@intent`) **and system modules** (`system_modules/*/module.py` with `_OWNED_INTENT_META`). The classifier doesn't care which — same rules apply.

For the architecture behind the classifier (MiniLM cosine + Helsinki translation + post-processing cascade), see [intent-routing.md](intent-routing.md). This doc is about how to AUTHOR intents that succeed in that architecture.

---

## The 60-second checklist

Before opening a PR with a new intent, verify:

- [ ] **Name** is module-prefixed: `<module>.<verb_or_noun>` (e.g. `weather.current`).
- [ ] **Description** names the primary action AND explicitly excludes neighbouring intents (“**NOT** for X”). 80–200 chars — not too short, not bloated.
- [ ] **Entity types** (if the intent targets a device class) reuse a canonical value — don't invent new ones.
- [ ] **Anchors** — 5–10 example sentences in [`INTENT_ANCHORS`](../system_modules/llm_engine/embedding_classifier.py) covering the phrasings users actually say, plus Helsinki UK→EN artifacts if Ukrainian is supported.
- [ ] **Handler exists** — every entry in `_OWNED_INTENT_META` has a matching `_handle_*` method. Dead intents steal classifier matches from real intents.
- [ ] **Corpus cases** added in [tests/experiments/corpus_generator.py](../tests/experiments/corpus_generator.py) — ≥ 3 cases (EN + UK, plain + one variety twist).
- [ ] **Bench** — `run_coverage_bench.py` shows your new intent ≥ 80%, overall ≥ 97%, distractors 100%.

---

## 1. Intent name & namespace

Every intent is `<module>.<verb_or_noun>`, lowercase, dot-separated. Examples:
`weather.current`, `device.on`, `media.play_radio_name`, `clock.set_alarm`.

### Reserved namespaces (system modules)

| Namespace | Owner | Use for |
|---|---|---|
| `device.*` | device-control | Device power, lock, climate, queries |
| `media.*` | media-player | Radio playback, volume, track nav |
| `house.*` | device-control | Whole-house mass operations |
| `clock.*` | clock | Alarms, timers, reminders |
| `weather.*` | weather-service | Outdoor conditions + forecast |
| `presence.*` | presence-detection | Who's home queries |
| `automation.*` | automation-engine | Rule enable/disable/list |
| `energy.*` | energy-monitor | Power usage queries |
| `watchdog.*` | device-watchdog | Device liveness |
| `privacy_on` / `privacy_off` | voice-core | Microphone mute |

### User modules

Use your module's name as the prefix: `my_weather.umbrella_check`, `garden.water_schedule`. Don't put anything under a reserved namespace — your intent will be overridden or confused with the system module at classifier time.

---

## 2. The description recipe

**This is the single biggest lever for accuracy.** The description string is embedded and cosine-matched against the user's utterance — its quality directly controls classifier routing.

### Recipe

```
<primary action verb> <primary noun / object>. <neighbour contrast clause>. <concrete phrases>.
```

1. **Primary action** — what the intent DOES. One verb, one noun.
2. **Contrast clause** — what it is NOT for. Name the neighbour explicitly.
3. **Concrete phrases** — 2–3 verbatim user utterances, EN and UK if you support Ukrainian.

### Length

80–200 characters of prose, max ~300 including the phrase list. Two failure modes observed:

- **Too long** (R2, R8 in the tuning log): description bloats past ~50 words, centroid dilutes, cosine peaks collapse. Regression of up to −11pp.
- **Too short** (one-liner like `"Set temp"`): classifier has no anchor at all, fails to match on paraphrased input. Regression of up to −7pp.

### Example: `clock.stop_alarm` (merged from two overlapping intents)

Before (two separate intents, 35% accuracy):
```python
"clock.cancel_alarm": "Cancel / delete an existing alarm by label or position.",
"clock.stop_alarm":   "Silence the alarm that is ringing right now (snooze or dismiss).",
```
Users say "stop" and "cancel" interchangeably — classifier couldn't distinguish.

After (merged, 86% accuracy):
```python
"clock.stop_alarm": (
    "Silence / cancel / dismiss an alarm — covers both "
    "'stop the alarm' when it's ringing AND 'cancel the "
    "morning alarm' when removing from schedule. Single "
    "intent for both verbs (they mean the same thing to "
    "the user)."
),
```

### Example: `presence.check_user` vs `presence.who_home` (sharp contrast)

Both query presence state but differ in scope. The descriptions explicitly contrast:

```python
"presence.who_home": (
    "List WHO is currently at home — returns names of all "
    "household members present. Open question without a "
    "specific person. Use for 'who's home', 'who is here'. "
    "NOT for 'who are you' (about the assistant)."
),
"presence.check_user": (
    "Check whether ONE SPECIFIC named person is at home. "
    "Query mentions a person's name. Use for 'is Alice home', "
    "'is Bob here'. Contains a proper name — distinguishes "
    "from generic who_home."
),
```

The phrase “contains a proper name — distinguishes from generic who_home” is the critical disambiguator. Without it, the classifier routed “is Alice home” to `who_home` every time.

---

## 3. Canonical `entity_types`

The registry's `Device.entity_type` column uses a fixed vocabulary. Your intent's `entity_types` constraint must reference one of these:

| Type | Typical devices |
|---|---|
| `light` | bulbs, lamps, LED strips |
| `switch` | smart switches |
| `outlet` | plugs, sockets, power strips |
| `fan` | ventilators, ceiling fans |
| `air_conditioner` | AC units |
| `thermostat` | smart thermostats |
| `radiator` | heaters, radiators |
| `humidifier` | humidifiers |
| `kettle` | electric kettles |
| `tv` | televisions |
| `curtain` | curtains, blinds, shades |
| `vacuum` | robot vacuums |
| `media_player` | speakers, audio streamers |
| `door_lock` | smart locks |
| `speaker` | standalone speakers |
| `sensor` | motion/temp/humidity sensors |
| `camera` | security cameras |

### Don't invent variants

If a user says "lamp", "bulb", "light fixture" — they all map to `entity_type="light"`. Adding `entity_type="lamp"` as a new value splits the device pool and breaks type+location resolution. Variant words go into [`ENTITY_MAP`](../system_modules/llm_engine/embedding_classifier.py) (the entity extractor), not into the registry.

### When to use `entity_types` on an intent

Only when the intent semantically applies to a narrow set. Example:

```python
"device.set_temperature": dict(
    entity_types=["air_conditioner", "thermostat", "radiator"],
),
```

Without this, "set the temperature in the bedroom" could match any device in the bedroom. With it, the resolver restricts to climate-only devices. This is what gave thermostat cases 13% → 91% in one change.

For generic intents like `device.on` or `device.off` that apply to ANY device — leave `entity_types=None`.

---

## 4. Anchors — `INTENT_ANCHORS`

Anchors are example sentences pre-computed into the intent's embedding centroid. **This is where real accuracy gains happen.** Descriptions alone give a coarse match; anchors sharpen it.

Location: [`system_modules/llm_engine/embedding_classifier.py`](../system_modules/llm_engine/embedding_classifier.py), the `INTENT_ANCHORS` dict (starts around line 78).

### How many

**5–10 anchors per intent.** Fewer → classifier misses paraphrases. More → dilutes (same failure mode as bloated descriptions). The tuning log showed +12pp on AC cases from just 6 anchor additions.

### What to include

1. **Canonical phrasings** — the 2–3 most natural ways a user would say it.
2. **Synonyms and casual verbs** — if your intent description mentions "flip on" as a synonym, add "flip on the X" as an anchor.
3. **Indirect phrasings** — “I want the X on” (device.on), “no need for the X” (device.off), “I want X to work” (device.on).
4. **Helsinki translation artifacts for UK** — see section 5.
5. **Short / bare forms** — if your intent accepts one-word commands (`pause`, `resume`, `next`), add them explicitly as anchors.

### Example: `media.resume`

```python
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
```

The last two are the key — without them, `продовж` → Helsinki → `"Continued."` lands below threshold and falls to unknown. With them, it hits `media.resume` with confident margin.

---

## 5. Helsinki UK→EN translation quirks

If your intent supports Ukrainian input, the user's UK utterance gets translated to English by the Helsinki opus-mt model BEFORE reaching the embedding classifier. Helsinki is lossy and biased toward declarative sentences — not imperative commands. You must account for this.

### Known artifacts (feed as anchors)

| UK phrase | Helsinki output | Why it matters |
|---|---|---|
| `слухай увімкни X` | `"Listen to the X."` | Verb `увімкни` dropped entirely |
| `запали X` | `"Light the X"` or just `"X."` | Verb `запали` lost or mistranslated |
| `замок` (lock) | `"Castle."` | Misclassified as place name |
| `зволожувач` | `"Moisturizer."` | Cosmetic product mistranslation |
| `продовж` | `"Continued."` | Declarative; no imperative remains |
| `тихіше` | `"Be quiet."` | Full sentence from single word |
| `голосніше` | `"Louder."` | Correct — no special handling needed |
| `постав джаз` | `"Let's jazz."` | Idiom; works if `media.play_genre` has a jazz anchor |

### Pre-translation stripping

Some prefixes are stripped BEFORE Helsinki runs — see [`_strip_uk_listener_prefix()`](../core/translation/helsinki_translator.py). Current list: `слухай`, `послухай`, `дивись`, `скажи будь ласка`, `привіт`. If your intent frequently fails on UK utterances starting with an attention-getter, extend this list rather than adding artifact anchors (cleaner and covers the whole phrase space).

### Rule of thumb

Run your UK test phrases through the translator manually before writing anchors:

```bash
docker exec -t selena-core python3 -c "
from core.translation.local_translator import get_input_translator
t = get_input_translator()
print(t.to_english('ваша фраза тут', 'uk'))
"
```

Whatever it outputs — THAT is what your anchors need to match.

---

## 6. When to split vs merge intents

### Merge when

Same user action, different verbs. Users don't care about the verb — they care about the outcome.

Example: `clock.cancel_alarm` + `clock.stop_alarm` were two intents. User says "cancel the alarm" or "stop the alarm" interchangeably — whether the alarm is ringing now or scheduled for tomorrow. One intent, one handler that dispatches on state: if ringing, silence it; else, delete it from the schedule. The handler makes the contextual decision, not the classifier.

### Split when

Same verb phrase, genuinely different downstream actions with different reversibility semantics.

Example: `media.pause` vs `media.stop`. Users sometimes say "stop the music" when they mean pause — anchors overlap. But pause is resumable (session preserved), stop is not (session destroyed). Keeping them separate means the user can recover "wait, actually resume" after a pause. If you merge, "stop then resume" becomes "stop, then restart from scratch" — user-visible regression.

### Rule of thumb

If you can't write a sharp description that contrasts two intents in **under 20 words** — they should be one intent. `presence.who_home` vs `check_user` passes this test ("open query" vs "contains a proper name"). `cancel_alarm` vs `stop_alarm` fails it ("silence one that's ringing" vs "delete a scheduled one" — same from user perspective).

---

## 7. Post-processing overrides

Located in [`system_modules/llm_engine/intent_router.py`](../system_modules/llm_engine/intent_router.py), inside `_try_embedding_classify()` after the cosine winner is computed.

### Pattern

```python
if result.intent == <wrong_intent> and <query_condition>:
    result.intent = <correct_intent>
```

### When to use

**Only for systematic misroutes that anchors can't fix.** Example: TV commands were routing to `media.play_radio_name` because "TV" looks like a proper-noun station name. No amount of device.on anchors fixed the cosine collision. Post-proc rule:

```python
if (
    result.intent.startswith("media.play_")
    and (result.params or {}).get("entity") == "tv"
):
    result.intent = "device.off" if is_off else "device.on"
```

Similarly: `house.all_off` override when query contains `all/everything/все` and classifier returned `device.off`. And `media.volume_up` override for `turn it up` / `louder` idioms misrouting to `device.on`.

### When NOT to use

Every override is a brittle rule that hides cosine issues instead of fixing them. If you find yourself writing 3+ overrides for one intent — your description and anchors are the problem. Fix those first.

---

## 8. Dead intent antipattern

**If you declare an intent in `OWNED_INTENTS` / `_OWNED_INTENT_META`, you must have a handler for it.**

Declared-but-unhandled intents pollute the classifier's candidate set. The cosine picks the closest match from all declared intents — if your dead intent's description is lexically close to a real utterance, the classifier routes to it, and the handler silently drops the command.

We had this with `media.shuffle_toggle`: declared since v0.3, no handler in `voice_handler.py`. Users saying "shuffle" got their command routed to it, then nothing happened. Removing the declaration from `_OWNED_INTENT_META` unblocked downstream cases — classifier picked real intents instead.

**Rule:** before adding to `_OWNED_INTENT_META`, write the handler. If the handler can't be written now, don't declare the intent.

---

## 9. Confidence thresholds

Configured in `config/core.yaml`:

```yaml
intent:
  embedding_score_threshold: 0.25   # winner cosine must exceed this
  embedding_margin_threshold: 0.003 # winner − runner-up must exceed this
```

Also hardcoded in [`embedding_classifier.py`](../system_modules/llm_engine/embedding_classifier.py) as `UNKNOWN_THRESHOLD` / `MARGIN_THRESHOLD` — keep both layers in sync.

**Don't tune per-intent.** Thresholds are global because the classifier's cosine distribution is global. If the whole distribution shifts (e.g. after a model swap), tune once, re-bench, keep. The current values were found against `paraphrase-multilingual-MiniLM-L12-v2` — a different embedding model may need different thresholds.

---

## 10. Testing & PR gate

Every new intent needs corpus coverage. Without it, you can't measure whether your intent works.

### Adding cases

Edit [tests/experiments/corpus_generator.py](../tests/experiments/corpus_generator.py) — find the list for your category (or add a new category) and append:

```python
{"lang": "en", "native": "<user phrase>",
 "exp_intent": "<your.intent>",
 "exp_entity": None,  # or the entity_type if known
 "exp_location": None,
 "category": "<category>", "twist": None, "noise": None},
```

Categories in use: `plain`, `variety`, `noise`, `ambiguous`, `all_off`, `all_on`, `media`, `clock`, `weather`, `presence`, `automation`, `system`, `distractor`. Add a new category if none fits — update `_verdict()` in [run_coverage_bench.py](../tests/experiments/run_coverage_bench.py) accordingly.

### Minimum for each intent

- 2 cases EN (plain + one variety: short, polite, syn, or casual)
- 2 cases UK (plain + one variety)
- If the intent is bilingual, run Helsinki on the UK cases manually and check the English output — adjust anchors if output is unusual

### Running the bench

```bash
docker exec -t selena-core python3 /opt/selena-core/tests/experiments/run_coverage_bench.py
```

Takes ~20 seconds. Output includes per-category breakdowns and full failure list.

### PR gate (proposed CI rule)

- **Per-intent**: new intent must hit ≥ 80% on its own cases (≥ 60% if it shares verbs heavily with an existing intent — allowed to overlap, not allowed to steal).
- **Overall**: total accuracy stays ≥ 97.0% (current 96.6%, 0.4pp buffer).
- **Distractors**: must stay at 100%. Any drop means over-trained anchors that produce false-positives on chat.

---

## 11. Checklist (repeated)

- [ ] Name: `<module>.<verb_or_noun>` in a valid namespace
- [ ] Description: IS + IS-NOT + 2–3 concrete phrases, 80–200 chars
- [ ] `entity_types` constraint only if semantically narrow
- [ ] 5–10 anchors, including Helsinki artifacts if UK
- [ ] Handler written — not a declared-but-unhandled intent
- [ ] ≥ 3 corpus cases (EN + UK, plain + variety)
- [ ] Bench: ≥ 80% on new intent, ≥ 97% overall, 100% distractors

---

## 12. Appendix: real before/after examples

### `device.set_temperature` (anchors unlocked it)

Before: description alone, no anchors. Thermostat 13%, AC 36% on set_temperature cases.

Anchors added:
```python
"device.set_temperature": [
    "set the air conditioner to 22 degrees",
    "set temperature to 20",
    "set the temperature to 22 degrees in the living room",
    "set the temperature to 22 degrees in the bedroom",
    "set the temperature to 22 degrees in the bathroom",
    "set temperature to 22 in the kitchen",
    "make it 22 degrees in the living room",
    "change the temperature to 22 degrees",
    # Helsinki outputs for UK "встанови температуру":
    "set the air conditioning to 22 degrees.",
    "set twenty-two degrees.",
],
```

Result: thermostat 91%, AC 94%. One edit, +80pp on thermostat.

### `house.all_off` (post-proc override pattern)

Attempted with anchors first: "turn off everything", "shut everything down", ~15 anchors. **Regression of −19.6pp** because the anchors ("turn off …") collided with `device.off` anchors.

Replaced with post-proc override:
```python
if has_all and result.intent in ("device.on", "device.off"):
    new_intent = "house.all_on" if result.intent == "device.on" else "house.all_off"
    result.intent = new_intent
```

Description kept strict:
```python
"Whole-house mass off — user said 'all' / 'everything'. "
"NOT for single-device off (use device.off). Triggers "
"ONLY when the query explicitly contains 'all', "
"'everything', 'все', 'всі', 'всё'."
```

Result: 15/15 cases pass, no regression on `device.off`.

### `media.volume_up` (bare-verb + idiom handling)

Users say "louder" alone. Classifier originally had no anchor for it — returned `unknown`. Users say "turn it up" which post-proc misrouted to `device.on`. Fixed with:

```python
# Anchors
"media.volume_up": [
    "louder",
    "turn it up",
    "make it louder",
    "increase volume",
    "volume up",
],

# Post-proc override
if result.intent in ("device.on", "device.off"):
    if "turn it up" in q_low or "louder" in q_low:
        result.intent = "media.volume_up"
```

Result: `media.volume_up` / `media.volume_down` now 100%.

---

**Questions / corrections?** This doc is versioned in the repo. Open an issue or PR if your intent doesn't land where the guide says it should — that's a signal this doc is wrong or incomplete, not you.
