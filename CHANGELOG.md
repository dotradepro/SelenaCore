# Changelog

All notable changes to SelenaCore are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Breaking changes

- **Piper TTS moved into the container.** The host-side `piper-tts.service`
  systemd unit is gone — `scripts/start.sh` now spawns `piper-server.py`
  as a supervised subprocess inside `smarthome-core` on port `:5100`. On
  an existing deployment, run once to stop and remove the old unit before
  pulling the new image:

  ```bash
  sudo bash scripts/migrate_piper_to_container.sh
  ```

  The script is idempotent. Fresh installs pick up the new layout
  automatically (no host-side pip install of `piper-tts`, no deadsnakes
  Python, no `PIPER_PYTHON` env var).

  **Build-arg note.** On Jetson / JP6 hosts the core image needs
  `onnxruntime-gpu` from the [Jetson AI Lab index](https://pypi.jetson-ai-lab.io).
  Build with:

  ```bash
  docker compose build --build-arg TARGET=jetson core
  ```

  The default `TARGET=cpu` pulls plain `onnxruntime` from PyPI (Pi / x86).

- **Ollama is now user-managed.** SelenaCore no longer installs, starts,
  stops, or pulls models for Ollama. If you want a local LLM, install
  Ollama yourself from <https://ollama.ai> before first launch and
  `ollama pull <model>` the weights you want, then configure the URL in
  the wizard's **LLM Provider** step (or later under **System → Engines**).
  Ollama is treated as a regular provider alongside OpenAI / Anthropic /
  Groq / Google — it can point at localhost, a LAN machine, or a remote
  proxy; the URL + optional Bearer key live at
  `voice.providers.ollama.{url,api_key}`.

  Endpoints that managed the host binary are **removed**: `POST
  /api/ui/setup/ollama/{install,uninstall,start,stop,pull,delete-model}`
  and `GET /api/ui/setup/ollama/{status,models,install-progress,
  pull-progress}`. Use `GET /api/ui/setup/llm/provider/models?provider=ollama`
  and `POST /api/ui/setup/llm/provider/{apikey,validate,model,select}`
  instead — the same endpoints already used for cloud providers.

### Added

- Wizard step **LLM Provider** (optional). Pick Ollama (URL + optional
  Bearer), a cloud provider, or Skip entirely. Cloud model pickers are
  filtered to text-generation SKUs (`?text_only=1`) — no more
  `dall-e-3` / `whisper-1` / embedding models in the wizard dropdown.
- `scripts/migrate_piper_to_container.sh` — upgrade helper for
  deployments still running the old host-side `piper-tts.service`.
- One-shot config migration `core.config.migrate_ollama_url_key` runs
  on container start and moves `llm.ollama_url` →
  `voice.providers.ollama.url` atomically (tmpfile + `os.replace`).
- GPU detection at boot persists `hardware.gpu_detected`/`gpu_type` in
  `core.yaml`. The wizard's TTS step reads this and defaults the
  GPU/CPU toggle to the detected state — users on CPU-only hosts no
  longer see a toggle they can't use.

### Changed

- `voice_engines.py::list_models` accepts `text_only=True` and drops
  image / embedding / TTS / STT / vision SKUs from the result.
- `OllamaClient.probe()` replaces `is_available()` for callers that
  need to tell the difference between *unreachable* and *auth required*
  (401/403). `is_available()` is still provided but treats
  `auth_required=True` as unavailable.
- `SystemPage.tsx` no longer shows a dedicated **Ollama** card or modal;
  Ollama appears inside **Active Provider** / **Cloud Providers** tiles
  like any other remote LLM.

### Removed

- `scripts/piper-tts.service` — the host systemd unit template.
- Host-side Piper runtime installer (`install_piper_runtime`,
  `_ensure_piper_python`, `_try_deadsnakes_python310`, `_try_uv_python`)
  from `install.sh`. No more deadsnakes PPA / uv-managed Python 3.11
  provisioning on the host; the in-container Python 3.11 handles Piper.
- `install_ollama()` from `install.sh`. The `install_native_runtimes`
  function remains as a no-op stub so legacy callers don't break.
- `_switch_local_servers`, `_run_on_host`, `_ollama_install`,
  `OllamaModelRequest`, and `_CURATED_MODELS` / `GET /llm/catalog` from
  `voice_engines.py` — all dead once Ollama is user-managed.
- Top-level `ollama` object on the `/api/ui/system` response (now
  nested under `llm_engine.ollama` for backends that still want the raw
  probe result). Stale frontend types removed in the same commit —
  no dead-code shims.
