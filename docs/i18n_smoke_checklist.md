# i18n smoke checklist

Manual verification to run before cutting a `v0.4.0.x` release.
Automation covers generator internals + API endpoints
(`tests/test_i18n_generator.py`, `tests/test_api_i18n.py`); this
checklist covers the last-mile UI behavior that's impractical to script
without a full E2E framework.

## Pre-flight

- [ ] `python scripts/i18n_diff.py` — reports **zero** issues between
      `en.ts` and `uk.ts` (keys + placeholder parity).
- [ ] `python scripts/i18n_lint.py` — hardcoded-string candidate count
      matches `docs/i18n_audit_baseline.json`.
- [ ] `python scripts/i18n_audit.py` — no new hardcoded strings in
      recently-touched components.
- [ ] `npx tsc --noEmit` — clean on new files (pre-existing errors in
      Dashboard/Modules/etc are not this release's concern).
- [ ] `npx vite build` — completes without errors, UK chunk emits
      separately, index chunk ≤ 750 KB.

## SPA — language switching (4 sample languages)

For each of **en, uk, ru, ja**:

1. Settings → Appearance → Language → click the tile.
2. **Top-bar labels** (Dashboard, Devices, Automations, Voice) switch
   within ~200 ms.
3. **First-time auto-language banner** (ru, ja) appears once — click
   "Got it", switch away, switch back → banner stays dismissed.
4. **Safe-harbor**: Settings sidebar still shows the Language tab
   label + native name in the new language.
5. Long-press logo (1.5 s) → "Reset UI to English?" modal appears —
   close without confirming, verify UI still in the selected language.

## Wizard — language step (cold install)

Requires a fresh install (or manually `sudo cp config/core.yaml.example
config/core.yaml` and restart core).

- [ ] Wizard step 1 shows **all 16 languages** grouped as
      "Fully translated" (2) and "Machine-translated (beta)" (14).
- [ ] Search box filters by both native name and ISO code.
- [ ] Selecting an auto-language shows the "β" badge + first-time banner.

## Dashboard edit-mode gate

- [ ] Default: Settings → Users → Interface protection → `edit_mode_pin`
      is **on**.
- [ ] Dashboard → tap "Edit" → PIN modal appears.
- [ ] Enter correct PIN → enters edit mode; widget drag/drop works.
- [ ] Tap "Done" → exits edit mode without re-prompting.
- [ ] Toggle `edit_mode_pin` off → "Edit" enters directly, no PIN.
- [ ] Toggle `kiosk_mode` on → "Edit" prompts even when `edit_mode_pin`
      is off.

## Navigation (B1)

- [ ] Top-bar links: Dashboard / Devices / Automations / Voice.
- [ ] Each route renders the corresponding module's settings iframe.
- [ ] Old URL `/settings/system-modules/voice-core` 302s to `/voice`.
- [ ] <768px viewport (browser devtools → mobile): bottom-nav appears,
      top-nav links collapse.

## Module settings — i18n fetch

Pick 3 different modules (include voice-core as the pilot):

- [ ] Open settings iframe → labels are in the active UI language.
- [ ] Change UI language → iframe re-renders without page reload
      (widget-common.js posts `lang_changed`).
- [ ] Network tab shows `GET /api/i18n/bundle/{module}?lang={lang}`
      returning a merged bundle ≥ 30 keys.

## TTS language-mismatch suggestion

- [ ] Current TTS voice is UK → switch UI to EN or PL → banner
      "Voice does not match interface" appears under LanguagePicker.
- [ ] If a matching installed voice exists: "Activate" button works,
      banner disappears.
- [ ] If no matching voice: banner points at voice downloads.
- [ ] Dismiss → next UI change to the same pair stays silent.
- [ ] Global opt-out checkbox → all future switches stay silent.

## Visual theme

- [ ] Default dark theme paints a subtle gradient (not a flat black).
- [ ] Default light theme paints a subtle gradient (not a flat grey).
- [ ] Any wallpaper — widgets remain readable (backdrop-blur + 70%
      surface-color mix). Test on a contrasty wallpaper.

## Regression (quick pass)

- [ ] Dashboard: wake word works, voice intent renders.
- [ ] Modules page: list, install, start/stop still work.
- [ ] Settings tabs: every sub-route loads without console errors.
- [ ] `curl http://localhost/api/v1/health` → `"status": "ok"`.
