# i18n CI workflow

`.github/workflows/i18n.yml` regenerates `src/i18n/locales/auto/*.auto.json`
from `src/i18n/locales/en.ts` on every change to the English source (or
the generator pipeline). Output lands as a PR — never committed direct
to `main` — so a human eyeballs machine-translation quality before it
ships.

## Triggers

- **Push to `main`** that touches any of:
  - `src/i18n/locales/en.ts`
  - `src/i18n/glossary.json`
  - `scripts/generate_auto_locales.py`, `i18n_backends.py`,
    `i18n_plurals.py`, `i18n_config.py`, `i18n_export.mjs`
  - `.github/workflows/i18n.yml`
- **`workflow_dispatch`** — manual trigger, optional `targets=ru,pl`
  input for narrow reruns. Release-time regeneration uses this path
  (tags don't trigger the workflow directly; see the YAML comment).

## Step-by-step

1. **Checkout** + setup Python 3.11 (with pip cache) + Node 20 (npm cache).
2. **Install Python deps** — `argostranslate>=1.9` and `Babel>=2.14`.
   Full `requirements.txt` is intentionally skipped here because it
   pulls torch / ctranslate2 / cuda which would blow out the CI disk.
3. **Install minimal Node deps** — `npm ci --ignore-scripts` for `tsx`.
4. **Restore Argos package cache** from `~/.local/share/argos-translate`,
   keyed on `hashFiles('scripts/i18n_config.py')`. Adding / removing a
   language invalidates the cache cleanly. Cold start ~10 min (14 lang
   packs × ~30 MB each). Warm rerun ~2 min.
5. **Export `en.ts` → JSON** via `npx tsx scripts/i18n_export.mjs en`.
6. **Run the generator** with `--backend argos` over the exported JSON.
7. **Key-parity sanity check** — every auto bundle must cover every
   source key (modulo plural expansion which adds `_one`/`_few`/etc.).
   Fails the job with `::error` lines listing the first 5 missing keys
   per offending bundle.
8. **Detect changes** — skip PR creation if nothing actually diffed.
9. **Create / update PR** via `peter-evans/create-pull-request@v6` on
   branch `i18n/auto-update`. PR body has a checklist for the reviewer.

## Local reruns

You don't need the CI to regenerate locally — everything the workflow
does is runnable on a dev machine:

```bash
# One-off regenerate all 14 auto-languages using the real Argos backend.
# Works only with Python 3.9+ where argostranslate installs cleanly.
pip install 'argostranslate>=1.9' 'Babel>=2.14'
npx tsx scripts/i18n_export.mjs en > /tmp/en.json
python scripts/generate_auto_locales.py \
    --source /tmp/en.json \
    --output src/i18n/locales/auto \
    --backend argos

# Single-language regenerate (e.g. after fixing a glossary entry):
python scripts/generate_auto_locales.py \
    --source /tmp/en.json \
    --output src/i18n/locales/auto \
    --targets ja \
    --backend argos \
    --force

# Dry-run (useful for pipeline debugging):
python scripts/generate_auto_locales.py --dry-run
```

The first real run downloads Argos language packs automatically — plan
for ~10 min and ~500 MB of disk usage.

## Overriding targets

To regenerate only a subset via `workflow_dispatch`:

1. Actions tab → "Auto-translate locales" → "Run workflow"
2. Branch: `main`
3. `targets`: comma-separated codes, e.g. `ru,pl,cs`
4. `force`: `true` if the source hash hasn't changed but you still want
   a re-run (rare — usually only when fixing a generator bug).

## Debugging a failed run

**Parity check failed** — generator silently dropped some keys. Most
likely a placeholder like `{{count}}` got stripped by Argos. Fix by
adding the offending term / pattern to `src/i18n/glossary.json` and
rerunning.

**Cache miss on warm run** — check whether `scripts/i18n_config.py` was
touched. Every change to that file invalidates the cache intentionally.

**PR not opening** — check `permissions:` in the workflow. Needs
`contents: write` + `pull-requests: write`. GitHub occasionally tightens
defaults; if you see "resource not accessible by integration" errors,
re-read the workflow permissions block.

## Disabling / pausing

Short-term: comment out the `on.push` stanza and rely on
`workflow_dispatch` only. Long-term: delete the workflow file — locale
output is committed, so disabling CI doesn't break the SPA, it just
stops automatic regeneration.
