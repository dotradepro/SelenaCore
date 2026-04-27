# Changelog

All notable changes to SelenaCore are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- **update_manager: version history with GitHub Releases as source of truth.**
  The hub now lists every release on `dotradepro/SelenaCore` (filtered by
  channel) with publish date, prerelease badge, summary and full release
  notes — pick a version, confirm, install. Mandatory SHA256 verification
  against a `selenacore-<tag>.tar.gz.sha256` asset; mismatched downloads
  are rejected with `update.failed { reason: sha256_mismatch }`. The
  rc/stable channel selector lives in the settings page and persists to
  `/var/lib/selena/update_manager.state.json`.
- `scripts/apply-update.sh` — external updater run under `systemd-run`.
  Required because `smarthome-core.service` lives under
  `ReadOnlyPaths=/opt/selena-core /secure`, so in-process self-update
  fails with `PermissionError`. The script does an explicit
  `systemctl stop smarthome-core` first (overrides `Restart=always`),
  then hardlink-delta backup + rsync + `pip install` (or
  `docker compose build` in container deploy) + manifest rebaseline +
  `systemctl start`.
- `agent/manifest.py --rebuild` CLI for re-baselining `core.manifest` +
  `master.hash` after rsync so the integrity agent does not detect
  tampering on the new files.
- `agent/integrity_agent.py` honours `/secure/.update_in_progress`: while
  the flag exists (set by `apply-update.sh`, removed in `trap EXIT`),
  the periodic check returns `ok` without comparing hashes.
- New endpoints under `/api/ui/modules/update-manager/`: `GET /versions`,
  `GET /version/{tag}`, `POST /install`, `POST /config`, `GET /log`.
- `/etc/sudoers.d/selena-update` installed by `install.sh` granting the
  `selena` user `NOPASSWD` for `systemd-run` of the updater unit and
  `systemctl stop|start|restart smarthome-core`.

### Changed

- **update_manager source of truth flipped from `UPDATE_MANIFEST_URL` to
  GitHub Releases API.** The old manifest URL flow was non-functional in
  prod (no manifest was ever published). New flow uses
  `api.github.com/repos/<owner>/<repo>/releases?per_page=30`, with ETag
  caching to avoid rate limits and GitHub Releases assets for both the
  tarball and its SHA256 file.
- `apply_update_from_url` (cloud-triggered via `update.apply_core` event
  from `cloud_sync/commands.py`) now goes through the same external
  systemd-run flow as the UI install path. SHA256 is mandatory and
  validated as 64-char lowercase hex before downloading.
- Default `install_dir` in `UpdateManager`: `/opt/selena-update` →
  `/opt/selena-core`. The `UPDATE_INSTALL_DIR` env override remains for
  tests that need an isolated tree.
- `update_manager` widget/settings HTML moved to inline
  `var L = {en, uk}` to match the project-wide convention; the
  per-module `/api/i18n/bundle/update-manager` lazy-load is gone.
- ZIP archive support removed from `_extract` — releases are tar.gz only.

### Migration

- Releases must be published with three assets:
  `selenacore-<tag>.tar.gz`, `selenacore-<tag>.tar.gz.sha256`, and
  optionally `selenacore-<tag>.meta.json`. Without the first two the
  release is skipped with a warning. CI for asset generation is a
  separate follow-up issue.
- Existing deployments must re-run `install.sh` once to install the
  sudoers file and create `/var/lib/selena/update/` + `/var/log/selena/`.
  Without the sudoers, install attempts will fail at the `systemd-run`
  step with `permission denied`.

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
