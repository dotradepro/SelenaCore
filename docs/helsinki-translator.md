# Helsinki-NLP translator engine (CTranslate2)

SelenaCore ships with two translation backends behind a single
interface. The default is **Argos Translate**: zero-config, install
through the UI. For users who need better translation quality —
especially `en→target` for TTS — there is a second backend:
**Helsinki-NLP / opus-mt** running on the existing CTranslate2 runtime.

This document covers:

1. When to use Helsinki vs Argos
2. One-time model conversion on Google Colab (no PyTorch on Jetson)
3. Required folder layout
4. How to install and activate
5. How to add new language pairs

> **Architecture stays the same.** Both engines share the
> `InputTranslator` / `OutputTranslator` interface in
> [`core/translation/local_translator.py`](../core/translation/local_translator.py).
> All six pipeline callsites continue to use `get_input_translator()` /
> `get_output_translator()`. Engine selection lives in
> `translation.engine` (`argos` | `helsinki`) in `core.yaml` and is
> rewritten on every UI **Activate** click.

## When to use Helsinki

Trace-bench (`tests/benchmark/run_trace_bench.py`) on `qwen2.5:1.5b`
shows the same Ukrainian utterances translating differently:

| Native | Argos | Helsinki tc-big-zle-en |
|---|---|---|
| `яка температура у вітальні` | `What a temperature in the living room.` | `What is the temperature in the living room?` |
| `встанови режим охолодження` | `Set the coolant mode.` | `Set the cooling mode.` |
| `вмикни джазове радіо` | `Put your jazz radio down.` | `Turn on the jazz radio.` |
| `замкни вхідні двері` | `Shut the front door.` | `Lock the front door.` |

The LLM downstream is exactly the same in both runs. The wins come
purely from the translator. The biggest win is on `en→target` for
TTS speech, where the legacy `Helsinki-NLP/opus-mt-en-uk` model has
a known bug producing Russian text instead of Ukrainian. We
deliberately use the **Tatoeba Challenge** `tc-big-en-zle` model
(English → East Slavic) instead, which needs a leading `>>ukr<<`
language token piece — the wrapper handles this transparently via
`_OUTPUT_LANG_TOKENS` in
[`core/translation/helsinki_translator.py`](../core/translation/helsinki_translator.py).

For input direction we use `tc-big-zle-en` (East Slavic → English),
the matching multi-source counterpart. Same model file format, no
language token needed because the target is always English.

Use Argos if:

- You don't want to run a one-off Colab conversion.
- Your language pair already has a recent (1.9+) Argos package.
- You only need rough understanding (e.g. wake-word + simple commands).

Use Helsinki if:

- The Argos `en→target` package is old or visibly poor (true for
  Ukrainian — Argos ships v1.4 from 2021).
- You're shipping a voice assistant where TTS quality matters and
  you need clean Ukrainian output (not Russian).
- The corpus has idioms / questions that Argos translates literally.

## Model family choice

Helsinki-NLP publishes three families of opus-mt models, in
increasing quality / size:

| Family | Example | Size (int8) | Notes |
|---|---|---|---|
| `opus-mt-{src}-{tgt}` | `opus-mt-uk-en` | ~80 MB | Original 2020-2021. Single pair. Old training data. The `en-uk` direction has a known bug producing Russian. |
| `opus-mt-tc-base-{src}-{tgt}` | `opus-mt-tc-base-uk-en` | ~120 MB | Tatoeba Challenge base. Better quality, 2022. Single pair. Not all languages available. |
| `opus-mt-tc-big-{src}-{tgt}` or `tc-big-{group}-{tgt}` | `opus-mt-tc-big-zle-en` | ~240 MB | Tatoeba Challenge big. Highest quality, 2022-2023. Group variants (`zle`, `zls`, `gem`, …) cover several languages with one model. Multi-target variants need a `>>xxx<<` token prefix. |

**For Ukrainian we use:**

- **Input** (`uk → en`): `Helsinki-NLP/opus-mt-tc-big-zle-en` — multi-source
  East Slavic (Belarusian + Russian + Ukrainian + a few others) → English.
  No language token needed; the model auto-detects the source.
- **Output** (`en → uk`): `Helsinki-NLP/opus-mt-tc-big-en-zle` — English →
  multi-target East Slavic. Needs `>>ukr<<` prepended as a separate
  vocab piece (NOT as text), otherwise it defaults to Russian. The
  wrapper handles this; you don't have to think about it after install.

## Skip the conversion — pre-built archives on GitHub

Before you fire up Colab, check the companion repo —
**[dotradepro/selena-helsinki-models](https://github.com/dotradepro/selena-helsinki-models)** —
for a ready-made archive. Currently shipped:

| Language | Folder | Release |
|---|---|---|
| Ukrainian (`uk`) | [`languages/uk/`](https://github.com/dotradepro/selena-helsinki-models/tree/main/languages/uk) | [v1.0.0](https://github.com/dotradepro/selena-helsinki-models/releases/tag/v1.0.0) |

Download both archives, then in SelenaCore go to **Settings →
Translation → Upload custom Helsinki model**:

1. Enter lang code `uk`.
2. Pick direction `input`, attach `helsinki-uk-en-input.tar.gz`, click **Install**.
3. Pick direction `output`, attach `helsinki-en-uk-output.tar.gz`, click **Install**.
4. Click **Activate**.

That's it — skip to [Step 4](#step-4-install-in-the-ui) for
verification. The rest of this document describes how to convert new
language pairs yourself (and contribute them back).

## Step 1: Convert opus-mt models on Colab (one-time)

Jetson Orin and Raspberry Pi don't ship a recent enough PyTorch for
the converter. Run this once on Google Colab (free CPU runtime is
enough; the whole conversion is ~5 minutes per direction once the
model is downloaded).

### Easy path: open the ready-made notebook

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/14e_lpp8kuUJXvnjhybdtI_1z3rK9TOd1)

Click the badge above → **File → Save a copy in Drive** (so your edits
don't affect the shared notebook) → run cells top to bottom. The
notebook contains the same five cells documented below; the inline
copies are kept as a fallback in case the shared link ever breaks or
you want to understand what each step does.

### Manual path: build the notebook yourself

> **Why so many cells?** The conversion looks like it hangs if you put
> everything in one cell — `subprocess.run` swallows stdout until it
> exits. Splitting into separate cells with `!` shell magic streams the
> output live so you can actually tell what's happening. The download
> step in particular can stall on HuggingFace rate limits and you need
> to see that immediately.

Open [colab.research.google.com](https://colab.research.google.com),
**New notebook**, paste each cell below into its own cell and run
them top to bottom. Repeat for the second direction with the
language codes flipped (`uk-en` → `en-uk`).

### Cell 1 — install the converter (~30 sec)

```python
!pip install -q ctranslate2 transformers sentencepiece huggingface_hub
```

### Cell 2 — download the model files from HuggingFace (~1-2 min)

Download files **one by one** with `hf_hub_download`. This is far more
reliable than `snapshot_download` because each file has its own
30-second etag timeout — a stalled connection on one file won't hang
the whole notebook the way `snapshot_download` did.

```python
import os
from huggingface_hub import hf_hub_download

# Input direction (uk → en). Flip to opus-mt-tc-big-en-zle for output.
REPO = "Helsinki-NLP/opus-mt-tc-big-zle-en"
FILES = [
    "pytorch_model.bin",
    "config.json",
    "tokenizer_config.json",
    "vocab.json",
    "source.spm",
    "target.spm",
    "generation_config.json",
]

src_path = None
for f in FILES:
    try:
        p = hf_hub_download(repo_id=REPO, filename=f, etag_timeout=30)
        src_path = os.path.dirname(p)
        print(f"  ✓ {f}")
    except Exception as e:
        print(f"  ✗ {f}: {e}")

print("\n→ src_path =", src_path)
!ls -la {src_path}
```

You'll see two unauthenticated-request warnings about `HF_TOKEN` not
being set — **ignore them**. Public models don't need a token; the
free anonymous rate limit is plenty for one model.

### Cell 3 — convert to CTranslate2 int8 (~1-2 min)

```python
!ct2-transformers-converter --model {src_path} --output_dir opus-mt-tc-big-zle-en-ct2 --quantization int8 --force
```

The converter prints a few warnings — **all of them are safe to
ignore**:

| Warning | Meaning |
|---|---|
| `torch_dtype is deprecated! Use dtype instead!` | Internal transformers deprecation, doesn't affect output |
| `tied weights mapping and config for this model specifies to tie model.shared.weight…` | Helsinki opus-mt quirk, output is identical |
| `Recommended: pip install sacremoses` | Only needed for transformers' own tokenizer; we use sentencepiece directly so it's not needed |
| `Loading weights: 100% 258/258` | Progress bar of the actual conversion — this is good |

Output: a folder `opus-mt-tc-big-zle-en-ct2/` with `model.bin`,
`config.json`, and `shared_vocabulary.json`. **No `.spm` files yet —
that's the next step.**

### Cell 4 — copy the sentencepiece tokenizers (CRITICAL)

`ct2-transformers-converter` does **NOT** copy `source.spm` /
`target.spm` into its output. Without them the runtime cannot
tokenize input or detokenize output and the model is unusable. Cell 2
already pulled them into `src_path`; copy them in:

```python
import shutil, os
shutil.copy(os.path.join(src_path, "source.spm"), "opus-mt-tc-big-zle-en-ct2/")
shutil.copy(os.path.join(src_path, "target.spm"), "opus-mt-tc-big-zle-en-ct2/")
!ls -la opus-mt-tc-big-zle-en-ct2/
```

The `ls` output **must** show all five files:

```
config.json
model.bin
shared_vocabulary.json   (or shared_vocabulary.txt)
source.spm
target.spm
```

If any of those is missing, stop and re-run the missing step. The
SelenaCore upload route validates the same five files and will reject
the archive with a clear error message if any are missing — but it's
faster to catch it here.

### Cell 5 — pack as .tar.gz (~5 sec)

```python
!tar -czvf opus-mt-tc-big-zle-en-ct2.tar.gz opus-mt-tc-big-zle-en-ct2/
!ls -lh opus-mt-tc-big-zle-en-ct2.tar.gz
```

Result: `opus-mt-tc-big-zle-en-ct2.tar.gz` (~240 MB int8 — tc-big is
much larger than the legacy `opus-mt-uk-en` which was ~80 MB, but the
quality bump is worth it) in the Colab file panel on the left.
Right-click → **Download** to save it to your computer.

### Repeat for the reverse direction

Now repeat **Cells 2-5** for the output direction. Note: this is a
DIFFERENT model (`opus-mt-tc-big-en-zle`), not the same model with
swapped language codes. Helsinki publishes them as separate
multi-target / multi-source pairs.

```python
# Cell 2 (output direction)
REPO = "Helsinki-NLP/opus-mt-tc-big-en-zle"
# … rest of Cell 2 unchanged
```

```python
# Cell 3 (output direction)
!ct2-transformers-converter --model {src_path} --output_dir opus-mt-tc-big-en-zle-ct2 --quantization int8 --force
```

```python
# Cell 4 (output direction)
shutil.copy(os.path.join(src_path, "source.spm"), "opus-mt-tc-big-en-zle-ct2/")
shutil.copy(os.path.join(src_path, "target.spm"), "opus-mt-tc-big-en-zle-ct2/")
!ls -la opus-mt-tc-big-en-zle-ct2/
```

```python
# Cell 5 (output direction)
!tar -czvf opus-mt-tc-big-en-zle-ct2.tar.gz opus-mt-tc-big-en-zle-ct2/
```

Download the second `.tar.gz` from the file panel. You now have both
archives ready for upload to SelenaCore.

### Troubleshooting: download stalls at X% for >2 minutes

If Cell 2 hangs at a percentage and never moves, it's almost always
a HuggingFace rate-limit / connection drop. **Stop the cell** (⏹) and
fall back to direct `wget` which has aggressive resume support and no
HF library overhead at all:

```python
REPO = "Helsinki-NLP/opus-mt-tc-big-zle-en"   # or opus-mt-tc-big-en-zle for output
SLUG = "tc-big-zle-en"                          # local folder name

!mkdir -p hf_{SLUG} && cd hf_{SLUG} && \
  wget -c https://huggingface.co/{REPO}/resolve/main/pytorch_model.bin && \
  wget -c https://huggingface.co/{REPO}/resolve/main/config.json && \
  wget -c https://huggingface.co/{REPO}/resolve/main/tokenizer_config.json && \
  wget -c https://huggingface.co/{REPO}/resolve/main/vocab.json && \
  wget -c https://huggingface.co/{REPO}/resolve/main/source.spm && \
  wget -c https://huggingface.co/{REPO}/resolve/main/target.spm && \
  wget -c https://huggingface.co/{REPO}/resolve/main/generation_config.json
src_path = f"hf_{SLUG}"
!ls -la {src_path}
```

`wget -c` resumes partial downloads automatically and shows a real
progress bar. After it finishes, jump to **Cell 3** as normal.

### Troubleshooting: cell number stays at `[ ]` (no green check, no spinner)

The free Colab runtime disconnected. **Runtime → Reconnect**, then
re-run from Cell 1 (the model download is cached on disk by Cell 2's
`hf_hub_download`, so on a reconnect Cells 2 onward go fast).

## Step 2: Required folder layout (read this twice)

After extraction, **every** model directory MUST contain these files:

```
opus-mt-tc-big-zle-en-ct2/
├── model.bin               # CTranslate2 weights (~240 MB int8 for tc-big)
├── config.json             # CTranslate2 config (sets add_source_eos=false)
├── shared_vocabulary.json  # Full vocab including special tokens like >>ukr<<
├── source.spm              # ← MUST be present, manually copied above
└── target.spm              # ← MUST be present, manually copied above
```

The `shared_vocabulary.json` is critical for tc-big multi-target
models — it's where special tokens like `>>ukr<<` (ID 30040 for the
en-zle model) live. They are NOT in the sentencepiece vocab; they're
a Marian/HuggingFace concept layered on top. The
`HelsinkiOutputTranslator` wrapper prepends the right token as a
separate piece before passing to CTranslate2, which looks it up in
`shared_vocabulary.json` at translate time.

`config.json` for tc-big models has `"add_source_eos": false`. This
tells CTranslate2 NOT to auto-append `</s>` to source tokens — the
caller (us) does it. The wrapper handles that too. If you bring your
own runtime, remember to append `</s>` manually or you will get
multi-sentence run-on garbage like
`"weather weather .. what weather outdoor..."`.

If `source.spm` or `target.spm` is missing, `_load()` raises
`FileNotFoundError` and the engine logs:

```
WARN  Helsinki: model missing for uk-en (...) — falling back to pass-through.
      Drop the converted CT2 folder under /var/lib/selena/models/translate/helsinki/in
```

The voice pipeline keeps working — it just stops translating, exactly
as if the engine were disabled.

## Step 3: Install — three paths

### Path A: upload through the admin UI (recommended for end users)

This is the path for non-programmers — no SSH, no SCP, no shell.

1. Open the SelenaCore admin → **Settings → Voice → Translation**
2. Find the row labeled `Ukrainian` with the **Helsinki** badge
   (next to the existing Argos row)
3. Below it you'll see two file pickers:
   - `uk → en:` → click and pick `opus-mt-tc-big-zle-en-ct2.tar.gz`
   - `en → uk:` → click and pick `opus-mt-tc-big-en-zle-ct2.tar.gz`
4. Each upload streams to the server, extracts into the right folder,
   and validates that all five files (`model.bin`, `config.json`,
   `shared_vocabulary.json`, `source.spm`, `target.spm`) are present.
   If anything is missing the toast shows a precise error.
5. After both uploads, the row flips to show green `uk→en` / `en→uk`
   badges and an **Activate** button.
6. Click **Activate** → `translation.engine` becomes `helsinki`,
   `translation.active_lang` becomes `uk`, both translator engines get
   reloaded → done.

The upload route is `POST /api/ui/setup/translate/upload` (multipart:
`engine`, `lang`, `direction`, `file`). It only accepts the Helsinki
engine — Argos packages are still managed via the standard
`/translate/download` route.

### Path B: drop on disk manually (SSH access)

If you have shell access to the device, this skips the upload entirely:

```bash
tar -xzf opus-mt-tc-big-zle-en-ct2.tar.gz
sudo mkdir -p /var/lib/selena/models/translate/helsinki/in
sudo mv opus-mt-tc-big-zle-en-ct2 /var/lib/selena/models/translate/helsinki/in/uk-en

tar -xzf opus-mt-tc-big-en-zle-ct2.tar.gz
sudo mkdir -p /var/lib/selena/models/translate/helsinki/out
sudo mv opus-mt-tc-big-en-zle-ct2 /var/lib/selena/models/translate/helsinki/out/en-uk
```

Note the rename: the subdirectory becomes `<src>-<tgt>` (the
**language pair** label, NOT the model name). The downloader scans
for `model.bin` + `source.spm` + `target.spm` so any folder name
would work technically, but `uk-en` / `en-uk` is what the catalog
row matches against. Refresh the admin page and the Helsinki row
will show as installed.

### Path C: GitHub release (mirror for other users)

If you want other people running SelenaCore to install your converted
models through the **Install** button (rather than uploading their own
`.tar.gz`), publish them as release assets:

1. Create a release on `dotradepro/SelenaCore` tagged `translators-v1`.
2. Upload both `.tar.gz` files as release assets. The catalog already
   expects names `opus-mt-tc-big-zle-en-ct2-int8.tar.gz` and
   `opus-mt-tc-big-en-zle-ct2-int8.tar.gz` — keep them.
3. Compute sha256: `sha256sum opus-mt-tc-big-*-ct2-int8.tar.gz`.
4. Edit
   [`core/translation/helsinki_catalog.py`](../core/translation/helsinki_catalog.py)
   and fill `input_sha256` / `output_sha256` for the row.
5. Commit + push. Other users can now `POST /translate/download
   {"id": "helsinki-uk-en"}` from the UI and the downloader will
   fetch from your release URL with sha256 verification.

## Step 4: Activate via UI

The Helsinki rows show up in the same catalog as Argos rows under
**Settings → Translation**. The row id is `helsinki-uk-en`. Clicking
**Activate** writes:

```yaml
translation:
  engine: helsinki
  active_lang: uk
  enabled: true
```

…and reloads both engines (Argos and Helsinki singletons get cleared
so the next request loads the right one from disk).

`GET /api/ui/setup/translate/status` will then report:

```json
{
  "enabled": true,
  "engine": "helsinki",
  "active_lang": "uk",
  "input_available": true,
  "output_available": true
}
```

To switch back to Argos, click **Activate** on the Argos row for the
same language. The `engine` key flips back to `argos` automatically.

## Step 5: Verify

```bash
docker compose exec -T core python3 \
  /opt/selena-core/tests/benchmark/run_trace_bench.py --model qwen2.5:1.5b
```

Look at `STEP 2. InputTranslator (Argos)` (now actually Helsinki —
the label is hardcoded in the bench, ignore it) for cases 15, 17, 22.
The translated English should match the right column of the table at
the top of this doc.

## Adding a new language pair

1. Add the pair to `PAIRS` in the Colab snippet, re-run.
2. Drop the new folders under `helsinki/in/<src>-en/` and
   `helsinki/out/en-<src>/`.
3. Append a row to `HELSINKI_CATALOG` in
   [`core/translation/helsinki_catalog.py`](../core/translation/helsinki_catalog.py).
4. Restart the container; the row appears in the UI catalog.

## Why no PyTorch in production

The runtime is `ctranslate2` (C++ inference engine) +
`sentencepiece` (C++ tokenizer). Both ship as transitive dependencies
of `argostranslate>=1.9.0`, which is already in `requirements.txt`.
**No new pip packages.** PyTorch is only needed during conversion,
which happens once on Colab and never on the device.

## See also

- [`core/translation/helsinki_translator.py`](../core/translation/helsinki_translator.py)
  — runtime wrapper
- [`core/translation/helsinki_downloader.py`](../core/translation/helsinki_downloader.py)
  — install / activate / delete
- [`core/translation/helsinki_catalog.py`](../core/translation/helsinki_catalog.py)
  — pair definitions
- [`docs/translation.md`](translation.md) — overall translation pipeline
- [`docs/intent-routing.md`](intent-routing.md) — how the translator
  feeds the LLM
