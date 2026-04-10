# Local Translation System

SelenaCore uses **Argos Translate** for fully offline language translation
between the user's language and the English internal core.

## Why translation

The voice pipeline's internal language is **English**. This decision is
deliberate and lets us:

- Use compact LLM intent prompts (~200 tokens vs 1700+) — local 1-3B
  models can keep up
- Skip per-language prompt translation (no LLM calls on language change)
- Reuse the same intents/devices/rooms catalog regardless of user's
  spoken language
- Avoid the LLM mixing languages or producing transliteration

A small offline translator at the edges (after Vosk STT, before Piper
TTS) bridges the user's spoken language to the English core.

## Pipeline

```
Vosk STT → "увімкни світло на кухні"
   ↓
[InputTranslator] uk→en  ~200ms warm
   ↓
"turn on the light in the kitchen"
   ↓
IntentRouter (English prompt, English LLM, English intents)
   ↓
result.response = "Turning on the kitchen light."
   ↓
[OutputTranslator] en→uk  ~200ms warm
   ↓
"Вмикаю світло на кухні."
   ↓
preprocess_for_tts (numbers → Ukrainian words, lowercase)
   ↓
Piper TTS → audio
```

If `translation.enabled=false` or no model is installed, both
translators pass text through unchanged and the system behaves as a
single-language assistant.

## Backend: Argos Translate

| Detail | Value |
|--------|-------|
| Library | `argostranslate>=1.9.0` |
| Models | Pre-compiled, ~50–100 MB per language pair |
| License | MIT (library) + CC0 (models) |
| Offline | Yes — fully local after install |
| Languages | 49 supported (EN ↔ UK, RU, DE, FR, ES, PL, …) |
| Speed (warm) | 200–900 ms per sentence on Pi 5 |
| RAM | ~300 MB per loaded language pair |

Models are stored under
`~/.local/share/argos-translate/packages/` and loaded lazily on first
use.

## API

All endpoints under `/api/ui/setup/translate/`.

### `GET /translate/status`

```json
{
  "enabled": true,
  "fallback_to_llm": true,
  "active_lang": "uk",
  "input_available": true,
  "output_available": true
}
```

### `GET /translate/catalog`

Returns the full list of language pairs (49 entries) with installed /
active status:

```json
{
  "models": [
    {
      "id": "argos-uk-en",
      "lang_code": "uk",
      "lang_name": "Ukrainian",
      "input_installed": true,
      "input_version": "1.9",
      "output_installed": true,
      "output_version": "1.4",
      "installed": true,
      "active": true
    },
    ...
  ]
}
```

### `POST /translate/download`

```json
{ "lang": "uk" }
```

Downloads both directions (`uk→en` + `en→uk`) and auto-activates the
language if it's the first one installed.

### `GET /translate/download/status`

Polled by the UI during downloads:

```json
{
  "active": true,
  "package": "uk→en",
  "progress": 70.0,
  "error": "",
  "done": false
}
```

### `POST /translate/activate`

```json
{ "lang": "uk" }
```

### `DELETE /translate/lang/{lang_code}`

Removes both directions of the pair. Cannot delete the active pair.

### `POST /translate/settings`

```json
{ "enabled": true, "fallback_to_llm": true }
```

## Configuration

`config/core.yaml`:

```yaml
translation:
  enabled: false                # Set true after installing a pair
  active_lang: ""               # e.g. "uk" — set automatically by activate
  fallback_to_llm: true         # Use core.llm.translate when local model
                                # is unavailable (slower but always works)
```

## UI

Settings → **Voice & AI** → **Translate** tab. Each language pair shows:

- **Quality / size** badges
- **Direction badges**: `uk→en` and `en→uk` (green if installed)
- **Install** / **Activate** / **Delete** actions
- **Download progress bar** with live percentage

## Live STT Monitor events

Two new events show up in the live debug log:

- `translate_in` — fired right after Vosk STT, before IntentRouter
- `translate_out` — fired right after IntentRouter, before Piper TTS

Each carries:

```json
{
  "event": "translate_in",
  "from": "увімкни світло",
  "to": "turn on the light",
  "lang": "uk",
  "ms": 318,
  "msg": "🔄 uk→en (318ms): увімкни світло → turn on the light"
}
```

Use these to spot translation latency, mistranslations, or language
mismatches end-to-end.

## When translation is skipped

Both translators short-circuit (returning text unchanged, ~0 ms) when:

- The text is already ASCII (likely English)
- `source_lang == "en"` for input / `target_lang == "en"` for output
- The model is not installed
- `translation.enabled = false`

This means a Pi configured with English Vosk + English Piper voices
pays **no translation cost** even if a translator pair is installed.

## Quality notes

Argos Translate UK↔EN quality is good for short smart-home commands
(turn on/off, set temperature, query weather). It is **not** a
general-purpose translator for long or literary text. For ambiguous or
specialised vocabulary, the LLM fallback (`fallback_to_llm: true`)
takes over via `core.llm.translate`.

## Adding a new language pair

1. Open Settings → Voice & AI → Translate
2. Find the language in the catalog (sorted alphabetically)
3. Click **Install** — both directions download (~100–200 MB total)
4. Click **Activate** — translation is now enabled for that language
5. Set Vosk STT to that language and Piper voice to that language
6. Test through the Test Console — `translate_in` and `translate_out`
   events should appear in the Live Monitor

## Architecture rationale

**Why English internal core?**

- Local LLMs (qwen2.5:3b, phi3:mini, gemma2:2b) are trained mostly on
  English. Non-English JSON output is unreliable.
- Compact intent prompt (~200 tokens) fits in 2 K context windows of
  small models without truncation.
- Single source of truth: device names, intents, locations live in
  English in the registry (`meta.name_en`, `meta.location_en`).
- Translation cost (200–900 ms warm) is amortised across the whole
  pipeline — small price for stable LLM behaviour.

**Why not always go through LLM for translation?**

- LLM translation is slow (1–3 s per call) and model-dependent.
- Argos Translate is purpose-built — faster, more consistent, fully
  offline, no token budget impact on intent classification.
