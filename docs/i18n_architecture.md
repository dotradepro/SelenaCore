# i18n Architecture

v0.4.0 ships SelenaCore in 16 languages (2 human-maintained + 14 machine-
generated). This document describes how the pieces fit together so a
future maintainer can extend / debug / replace parts without re-reading
the whole codebase.

## The four surfaces

Translations live in four places:

| Surface | Source of truth | Consumer |
|---------|-----------------|----------|
| React SPA (`src/`) | `src/i18n/locales/*.ts` / `*.auto.json` / `*.community.json` | i18next + react-i18next |
| Core common strings | `core/i18n/common/{en,uk,…}.json` | `/api/i18n/*` endpoint |
| System / user modules | `system_modules/*/locales/*.json` and `modules/*/locales/*.json` | `/api/i18n/bundle/*` + `sdk.base_module.t()` |
| Python backend prompts / responses | Not i18n'd yet — logged + spoken text is English-only in v0.4.0 | n/a |

All four honor the same **4-tier resolution order**:

```
en.json / en.ts                   ← reference (always loaded)
{lang}.auto.json                  ← Argos output (lowest priority)
{lang}.community.json             ← community overrides
{lang}.ts / {lang}.json           ← manual translation (highest priority)
```

Later tiers override earlier ones key-by-key. Keys absent from a higher
tier fall through cleanly — community authors only need to ship the
keys they actually improved.

## The pipeline

```
         en.ts                                       ┌─────────────────┐
           │                                         │   Developer     │
           ▼                                         │   edits en.ts   │
    scripts/i18n_export.mjs ──(JSON)──┐              └─────────────────┘
                                      │
                                      ▼
    scripts/i18n_config.py ────► scripts/generate_auto_locales.py
           │                          │
           │                          ├─► scripts/i18n_backends.py
           │                          │    └─► argostranslate   ← CI only
           │                          │    └─► StubBackend      ← tests / local
           │                          │
           │                          ├─► scripts/i18n_plurals.py
           │                          │    └─► babel.plural     ← CI only
           │                          │
           │                          └─► src/i18n/glossary.json
           │                              └─► per-language overrides
           │
           └─► src/i18n/languages.json   ← manifest consumed by
                                           LanguagePicker / Wizard
```

CI workflow (`.github/workflows/i18n.yml`) triggers on:

- Push to `main` touching `src/i18n/locales/en.ts`, `glossary.json`, or
  any generator script.
- Manual `workflow_dispatch` (release reruns, targeted regeneration).

The workflow installs Argos + Babel, runs the generator, performs a
key-parity sanity check (every auto bundle must cover every source key
modulo plural expansion), and opens a PR against `i18n/auto-update`
with any regenerated files. Human approves, merges, ships.

## The SPA side

`src/i18n/i18n.ts` boots with `en.ts` eager (it's the fallback; costs
nothing) and lazy-loads every other language via `import.meta.glob`
(Vite code-splits each into its own chunk). Per-language chunk is ~40KB
(~12KB gzipped) — so adding 14 languages adds ~170KB gzipped to the
total assets, but the initial bundle stays essentially unchanged.

Resolution inside `loadLanguage()`:

```
manualLoaders    (./locales/{lang}.ts)           ← highest priority
communityLoaders (./locales/{lang}.community.json)
autoLoaders      (./locales/auto/{lang}.auto.json) ← lowest
```

Each tier writes into a single `bundle` object in priority order
(lowest first, so later tiers can override). i18next merges the result
via `addResourceBundle(lang, 'translation', bundle, true, true)`.

When the user picks a new language via `LanguagePicker`, the store
calls `changeLanguage(code)` which:

1. `loadLanguage(code)` — async fetch + chunk resolution.
2. `i18n.changeLanguage(code)` — swaps the active language.
3. `localStorage.setItem('selena-lang', code)` — persists for next boot.
4. postMessages `{type: 'lang_changed'}` into every iframe so module
   widgets can re-render with the new locale.

## The backend side

`core/api/routes/i18n.py` serves two endpoints that module widgets fetch
at boot time (no auth — localhost UI plumbing, like `/shared/*` and
`/api/ui/setup/*`):

- `GET /api/i18n/common?lang=pl` — just the core common strings.
- `GET /api/i18n/bundle/{module-name}?lang=pl` — common + module-specific
  strings merged.

The endpoint normalises `module-name` from kebab to snake case
(`voice-core` → `voice_core`) and looks for `locales/` first under
`system_modules/`, then under `modules/`. It merges the 4 tiers per
directory in the same order the SPA uses.

`functools.lru_cache` fronts the merge so repeated fetches by many
widgets at boot don't hammer disk. Invalidate via restart (dev) or the
cache auto-rebuilds on the next miss (prod — files change infrequently).

## The SDK side

`sdk/base_module.SmartHomeModule.t()` and `_register_locales()` honor
the **same** 4-tier order. User-authored modules can ship any subset of
`{en,uk,…}.json`, `.auto.json`, `.community.json` — the base class
walks the locales dir, discovers every language code mentioned in any
filename, and merges tiers per language.

Test coverage at `tests/test_sdk_base_module.py::test_register_locales_tier_priority`.

## Language manifest

`src/i18n/languages.json` mirrors `scripts/i18n_config.py`. Both files
list the same 2 manual + 14 auto codes with native names and text
direction. The Python copy drives the generator; the JSON copy is
imported by `LanguagePicker.tsx` + Wizard step 1 to render the picker.

Keep them in sync manually when adding a language — plan is to
auto-generate the JSON from the Python config in a future iteration,
but for now it's a 30-second copy-paste and only needed when the list
changes.

## Adding a new language

1. Edit `scripts/i18n_config.py` — add the code to `AUTO_LANGUAGES`
   and its native name to `NATIVE_NAMES`.
2. Mirror the entry in `src/i18n/languages.json`.
3. Push. CI regenerates all target locales including the new one and
   opens a PR.
4. Review + merge.

See [CONTRIBUTING_i18n.md](CONTRIBUTING_i18n.md) for the community side
of the story.

## Known limitations (v0.4.0)

- **Python backend strings** (logged text, TTS responses from core
  modules) are English-only. Speaking to the user in their language is
  handled by the TTS voice model + the rephrase LLM, not by i18n
  dictionaries. Moving those to `core/i18n/python/*.json` is a
  potential v0.5 direction.

- **RTL languages** (Arabic, Hebrew, Urdu, Farsi) are not shipped.
  `LanguagePicker` + `languages.json` have a `direction: 'ltr' | 'rtl'`
  field wired through; the CSS work to support `dir="rtl"` on every
  custom component is the blocker. Tracked as A2.5 in the roadmap.

- **Plural forms require `babel`** (dev-time / CI-time). `i18n_plurals.py`
  degrades gracefully to `['other']` when babel is absent — plural
  expansion just becomes a no-op. The generator + endpoint + SDK all
  still work.

- **Community tier is infrastructure-only in v0.4.0.** The 3-tier
  resolver, docs, and issue templates ship; no community files ship
  with the release itself. The LanguagePicker shows a "Community-improved"
  badge when a `.community.json` exists — that badge only appears after
  the first PR is merged.
