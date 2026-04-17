# Configuration Reference

SelenaCore uses a dual-source configuration system: environment variables (via `.env` and shell environment) and a YAML file (`core.yaml`). This document covers every available setting, its type, default value, and purpose.

---

## Configuration Sources and Precedence

Settings are resolved in the following order, from highest to lowest priority:

1. **Environment variables** — set in the shell or container runtime
2. **`.env` file** — loaded automatically by Pydantic `BaseSettings`
3. **`core.yaml`** — runtime-changeable settings loaded from disk
4. **Defaults** — hardcoded in the `CoreSettings` class

This means an environment variable always overrides the same setting defined in `.env` or `core.yaml`.

### File Locations

| File | Default Path | Override |
|------|-------------|----------|
| `.env` | Project root (`.env`) | N/A |
| `core.yaml` | `/opt/selena-core/config/core.yaml` | Set `SELENA_CONFIG` env var to an alternate path |
| `logging.yaml` | `/opt/selena-core/config/logging.yaml` | N/A |

---

## CoreSettings Reference (.env / Environment Variables)

All settings below are defined in `core/config.py` as a Pydantic `BaseSettings` model. They can be set as environment variables or placed in the `.env` file.

### Platform

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PLATFORM_API_URL` | `str` | `https://selenehome.tech/api/v1` | URL of the SelenaCore cloud platform API. |
| `PLATFORM_DEVICE_HASH` | `str` | `""` | Unique device identifier registered with the platform. |
| `MOCK_PLATFORM` | `bool` | `False` | When `True`, all platform API calls return stubbed responses. Useful for offline development. |

### Core

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CORE_PORT` | `int` | `80` | TCP port the core API server listens on. |
| `CORE_DATA_DIR` | `str` | `/var/lib/selena` | Directory for persistent data (database, module state). |
| `CORE_SECURE_DIR` | `str` | `/secure` | Directory for secrets and sensitive files (tokens, keys). |
| `CORE_LOG_LEVEL` | `str` | `INFO` | Logging verbosity. One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `DEBUG` | `bool` | `False` | Enables debug mode across the application (verbose output, auto-reload). |

### UI

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `UI_HTTPS` | `bool` | `True` | Whether HTTPS TLS proxy should be started on port 443. |

> **Note:** The UI is served by the same process as the Core API on port 80. There is no separate `UI_PORT` — it was removed when the UI proxy server was merged into Core.

### Agent

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AGENT_CHECK_INTERVAL` | `int` | `30` | Interval in seconds between module health checks. |
| `AGENT_MAX_RESTORE_ATTEMPTS` | `int` | `3` | Maximum number of automatic restart attempts for a failed module before giving up. |

### Docker

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DOCKER_SOCKET` | `str` | `/var/run/docker.sock` | Path to the Docker daemon socket. |
| `MODULE_CONTAINER_IMAGE` | `str` | `smarthome-modules:latest` | Default Docker image used when launching module containers. |
| `SANDBOX_IMAGE` | `str` | `smarthome-sandbox:latest` | Docker image used for sandboxed code execution. |

### OAuth

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GOOGLE_CLIENT_ID` | `str` | `""` | OAuth 2.0 client ID for Google integration (Assistant, Calendar). |
| `GOOGLE_CLIENT_SECRET` | `str` | `""` | OAuth 2.0 client secret for Google integration. |
| `TUYA_CLIENT_ID` | `str` | `""` | Tuya IoT platform client ID. |
| `TUYA_CLIENT_SECRET` | `str` | `""` | Tuya IoT platform client secret. |

### Tailscale

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TAILSCALE_AUTH_KEY` | `str` | `""` | Pre-authentication key for automatic Tailscale VPN enrollment. |

### Derived Properties

These are computed at runtime and cannot be set directly:

| Property | Value | Description |
|----------|-------|-------------|
| `db_url` | `sqlite+aiosqlite:////{core_data_dir}/selena.db` | SQLAlchemy async connection string for the SQLite database. |
| `secure_dir_path` | `Path(core_secure_dir)` | `pathlib.Path` object for the secure directory. |

---

## core.yaml Reference

The YAML configuration file is intended for settings that may be changed at runtime through the UI or setup wizard. Copy `config/core.yaml.example` to `/opt/selena-core/config/core.yaml` as a starting point.

### core

```yaml
core:
  host: "0.0.0.0"
  port: 80
  data_dir: "/var/lib/selena"
  secure_dir: "/secure"
  log_level: "INFO"
  debug: false
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `host` | `str` | `0.0.0.0` | Bind address for the core API server. |
| `port` | `int` | `80` | TCP port for the core API server. |
| `data_dir` | `str` | `/var/lib/selena` | Persistent data directory. |
| `secure_dir` | `str` | `/secure` | Secure storage directory for secrets. |
| `log_level` | `str` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `debug` | `bool` | `false` | Enable debug mode. |

### ui

```yaml
ui:
  host: "0.0.0.0"
  port: 80
  https: true
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `host` | `str` | `0.0.0.0` | Bind address for the web UI server. |
| `port` | `int` | `80` | TCP port for the web UI. |
| `https` | `bool` | `true` | Serve the UI over HTTPS. |

### agent

```yaml
agent:
  check_interval_sec: 30
  max_restore_attempts: 3
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `check_interval_sec` | `int` | `30` | Seconds between module health checks. |
| `max_restore_attempts` | `int` | `3` | Maximum automatic restart attempts for a failed module. |

### modules

```yaml
modules:
  container_image: "smarthome-modules:latest"
  sandbox_image: "smarthome-sandbox:latest"
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `container_image` | `str` | `smarthome-modules:latest` | Docker image for module containers. |
| `sandbox_image` | `str` | `smarthome-sandbox:latest` | Docker image for sandboxed execution. |

### voice

```yaml
voice:
  wake_word_sensitivity: 0.5
  stt_model: "vosk-model-small-uk"
  stt_silence_timeout: 1.0
  rephrase_enabled: false
  output_volume: 50               # Master TTS playback volume (0-150%)
  input_gain: 100                 # Microphone gain (0-150%)
  audio_force_input: null         # ALSA capture device (auto-detect if null)
  audio_force_output: null        # ALSA playback device (auto-detect if null)
  privacy_gpio_pin: null          # GPIO pin for physical mic kill switch
  tts:
    primary:
      voice: "uk_UA-ukrainian_tts-medium"
      lang: "uk"
      cuda: true
      settings:
        length_scale: 0.65
        noise_scale: 0.667
        noise_w_scale: 0.8
        volume: 0.7
        speaker: 1
    fallback:
      voice: "en_US-ryan-low"
      lang: "en"
      cuda: false
      settings:
        length_scale: 0.75
        noise_scale: 0.667
        noise_w_scale: 0.8
        volume: 0.55
        speaker: 0
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `wake_word_sensitivity` | `float` | `0.5` | Sensitivity threshold for wake word detection (0.0-1.0). |
| `stt_model` | `str` | `vosk-model-small-uk` | Vosk STT model name (downloaded from alphacephei.com/vosk/models). |
| `stt_silence_timeout` | `float` | `1.0` | Seconds of silence before processing command (0.5-5.0). |
| `rephrase_enabled` | `bool` | `false` | LLM rephrase for module responses. Adds latency. |
| `output_volume` | `int` | `100` | Master TTS output volume 0-150%. Software PCM scaling. |
| `input_gain` | `int` | `100` | Microphone gain 0-150%. Applied via `amixer`. |
| `audio_force_input` | `str\|null` | `null` | ALSA capture device (e.g., `plughw:0,0`). |
| `audio_force_output` | `str\|null` | `null` | ALSA playback device (e.g., `plughw:1,3`). |
| `privacy_gpio_pin` | `int\|null` | `null` | GPIO pin for physical mic kill switch. |
| `tts.primary.voice` | `str` | `uk_UA-ukrainian_tts-medium` | Primary Piper TTS voice. |
| `tts.primary.lang` | `str` | `uk` | Primary voice language code. |
| `tts.primary.cuda` | `bool` | `false` | GPU acceleration for primary voice. |
| `tts.primary.settings.*` | `dict` | see above | Per-voice synthesis parameters. |
| `tts.fallback.voice` | `str` | `en_US-ryan-low` | Fallback (English) voice. |
| `tts.fallback.lang` | `str` | `en` | Fallback voice language. |
| `tts.fallback.settings.*` | `dict` | see above | Per-voice synthesis parameters. |

**TTS settings per voice:**

| Setting | Range | Default | Description |
|---------|-------|---------|-------------|
| `length_scale` | 0.3-2.0 | 1.0 | Speech speed (lower = faster). |
| `noise_scale` | 0.0-1.0 | 0.667 | Intonation variability. |
| `noise_w_scale` | 0.0-1.0 | 0.8 | Phoneme width variability. |
| `volume` | 0.1-3.0 | 1.0 | Synthesis volume (Piper native). |
| `speaker` | 0-N | 0 | Speaker ID for multi-speaker models. |

### Environment variables (voice/TTS/LLM)

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPER_MODELS_DIR` | `/var/lib/selena/models/piper` | Piper voice model directory |
| `PIPER_VOICE` | `uk_UA-ukrainian_tts-medium` | Default TTS voice |
| `PIPER_GPU_URL` | `http://localhost:5100` | Native Piper server URL |
| `PIPER_DEVICE` | `auto` | Piper device mode: `auto`, `cpu`, `gpu` |

### voice.llm_provider + voice.providers.*

LLM connectivity is owned by the `voice` section — Ollama is a provider on
equal footing with OpenAI / Anthropic / Groq / Google. Selena does not
install or manage any Ollama server; install it yourself from
[ollama.ai](https://ollama.ai), `ollama pull <model>` the weights you
want, and point Selena at the URL through the wizard's **LLM Provider**
step or **System → Engines**.

```yaml
voice:
  llm_provider: "ollama"      # "ollama" | "openai" | "anthropic" | "groq" | "google"
  llm_model: "llama3.2"       # active model id for the selected provider
  providers:
    ollama:
      url: "http://localhost:11434"
      # api_key: "optional-bearer-token"   # only needed for remote/proxied Ollama
      model: "llama3.2"
    openai:
      api_key: "sk-..."
      model: "gpt-4o-mini"
    # anthropic, groq, google — same shape
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `voice.llm_provider` | `str` | *(unset)* | Active provider. Missing/empty → LLM disabled; novel voice phrases fall through to the deterministic fallback. |
| `voice.llm_model`    | `str` | *(unset)* | Shortcut for the active provider's model. Takes priority over provider-specific `model`. |
| `voice.providers.ollama.url`     | `str` | `http://localhost:11434` | HTTP endpoint of any reachable Ollama instance (local or remote). |
| `voice.providers.ollama.api_key` | `str` | *(unset)* | Optional Bearer token for proxied / hosted Ollama deployments. Local Ollama needs no key. |
| `voice.providers.{cloud}.api_key`| `str` | — | Cloud-provider API key. Required for non-Ollama providers. |
| `voice.providers.{id}.model`     | `str` | — | Per-provider model id. |

### llm (inference tuning)

```yaml
llm:
  enabled: true
  min_ram_gb: 5
  timeout_sec: 30
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | `bool` | `true` | Enable the LLM subsystem. |
| `min_ram_gb` | `int` | `5` | Minimum available RAM (in GB) required before dispatching a request. |
| `timeout_sec` | `int` | `30` | Request timeout in seconds for LLM inference calls. |

> **Migrated key:** older `core.yaml` files used `llm.ollama_url` and
> `llm.default_model`. The one-shot migration in `core.config.
> migrate_ollama_url_key` moves `llm.ollama_url` →
> `voice.providers.ollama.url` on first boot and prunes the old key.

### platform

```yaml
platform:
  api_url: "https://selenehome.tech/api/v1"
  device_hash: ""
  heartbeat_interval_sec: 60
  mock: false
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `api_url` | `str` | `https://selenehome.tech/api/v1` | Cloud platform API endpoint. |
| `device_hash` | `str` | `""` | Device identifier for platform registration. |
| `heartbeat_interval_sec` | `int` | `60` | Interval in seconds between heartbeat pings to the platform. |
| `mock` | `bool` | `false` | Stub all platform API responses for offline development. |

### wizard

```yaml
wizard:
  completed: false
  current_step: null
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `completed` | `bool` | `false` | Whether the initial setup wizard has been completed. |
| `current_step` | `str` or `null` | `null` | The last active wizard step, used to resume an interrupted setup. |

### system

```yaml
system:
  device_name: "Selena Hub"
  language: "uk"
  timezone: "Europe/Kyiv"
```

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `device_name` | `str` | `Selena Hub` | Human-readable name for this hub instance. |
| `language` | `str` | `uk` | System language code (ISO 639-1). |
| `timezone` | `str` | `Europe/Kyiv` | IANA timezone identifier for scheduling and display. |

---

## Additional .env Variables

These variables are not part of `CoreSettings` but are used by supporting services and development tooling.

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | API key for Google Gemini, used as a cloud LLM fallback when local inference is unavailable. |
| `APP_URL` | Base URL of the core API (e.g., `http://localhost`). Used by external services that need to call back into SelenaCore. |
| `HOST_UID` | Host user ID, passed into containers for PulseAudio socket permissions. |
| `OLLAMA_URL` | Override `voice.providers.ollama.url` (useful for one-off env-var-driven tests). |
| `OLLAMA_API_KEY` | Override `voice.providers.ollama.api_key` at runtime. |
| `DEV_MODULE_TOKEN` | A static bearer token accepted during development for module API testing. Do not use in production. |

---

## Logging Configuration

Logging is configured via `/opt/selena-core/config/logging.yaml`, which is loaded using Python's `logging.config.dictConfig()`.

If the file is missing or fails to load, SelenaCore falls back to Python's `basicConfig` with the level taken from the `CORE_LOG_LEVEL` environment variable (default `INFO`).

---

## Runtime Configuration Updates

Settings stored in `core.yaml` can be modified at runtime through:

- **Setup wizard** — writes initial configuration during first-run setup.
- **UI settings panel** — allows changing voice, LLM, and system settings without a restart.

These updates are handled by the `core/config_writer.py` module, which reads the current YAML, applies changes, and writes the file back atomically.

---

## Quick Start Example

1. Copy the example files:

   ```bash
   cp config/core.yaml.example /opt/selena-core/config/core.yaml
   cp .env.example .env
   ```

2. Edit `.env` with credentials and secrets:

   ```dotenv
   PLATFORM_DEVICE_HASH=your-device-hash
   GOOGLE_CLIENT_ID=your-google-client-id
   GOOGLE_CLIENT_SECRET=your-google-client-secret
   TAILSCALE_AUTH_KEY=tskey-auth-xxxxx
   ```

3. Adjust `core.yaml` for your environment (language, timezone, voice model).

4. Start SelenaCore. The setup wizard will guide you through remaining configuration if `wizard.completed` is `false`.
