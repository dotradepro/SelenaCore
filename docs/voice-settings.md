# Voice Settings — Engine Management & Configuration

## Overview

The `/settings/voice` page provides full management of three voice/AI engines:
- **Vosk** — offline speech-to-text (STT)
- **Piper** — offline text-to-speech (TTS)
- **Ollama / Cloud LLM** — language model for intent processing

All engines can be installed, configured, and tested through the web interface.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (React)     /settings/voice                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │VoskSection│  │PiperSection│ │LlmSection            │  │
│  │ STT test  │  │ TTS settings│ │ Provider tabs       │  │
│  │ AI pipeline│  │ Voice preview│ │ System prompt      │  │
│  └──────────┘  └──────────┘  └──────────────────────┘  │
└─────────────────────┬───────────────────────────────────┘
                      │ HTTP API
┌─────────────────────┴───────────────────────────────────┐
│  Backend (FastAPI)   /api/ui/setup/*                     │
│  voice_engines.py — engine management, catalogs, LLM    │
│  setup.py — model selection, audio, provisioning         │
└─────────┬──────────────────┬────────────────────────────┘
          │                  │
┌─────────┴──────┐  ┌───────┴────────┐
│ selena-core    │  │ selena-ollama  │  (GPU container)
│ Vosk + Piper   │  │ Ollama server  │
│ (CPU)          │  │ (100% GPU)     │
└────────────────┘  └────────────────┘
```

---

## Engine Status & Installation

### Vosk STT
- **Package**: `vosk` (pip, baked into Docker image)
- **Models**: `/var/lib/selena/models/vosk/` (on `selena_data` volume)
- **GPU**: CPU only (Kaldi engine has no GPU support)
- **Catalog**: Dynamic from `https://alphacephei.com/vosk/models` (HTML parsed, cached 24h)
- **API**:
  - `GET /setup/vosk/status` — installed/version
  - `GET /setup/stt/models` — installed models (disk scan)
  - `GET /setup/stt/catalog` — available models (remote)
  - `POST /setup/stt/download` — download model (async + progress)
  - `POST /setup/stt/delete` — delete model
  - `POST /setup/stt/select` — set active model
  - `POST /setup/stt/test` — record 4s → Vosk STT → return text + audio

### Piper TTS
- **Package**: `piper-tts` + `pathvalidate` (pip, baked into Docker image)
- **Models**: `/var/lib/selena/models/piper/` (on `selena_data` volume)
- **GPU**: CUDA via `--cuda` flag (requires `onnxruntime-gpu`, auto-detected)
- **Catalog**: Dynamic from HuggingFace `voices.json` (cached 24h)
- **Settings** (saved in `core.yaml` → `voice.tts_settings`):
  - `length_scale` — speech speed (0.5 fast → 2.0 slow, default 1.0)
  - `noise_scale` — intonation variability (0 monotone → 1 expressive, default 0.667)
  - `noise_w_scale` — phoneme width variation (0 stable → 1 varied, default 0.8)
  - `sentence_silence` — pause between sentences in seconds (default 0.2)
  - `volume` — volume multiplier (default 1.0)
  - `speaker` — speaker ID for multi-speaker models (default 0)
- **Text sanitization** (automatic before synthesis):
  - Removes markdown (`**bold**`, `# headers`, `` `code` ``, etc.)
  - Removes URLs, HTML tags, emoji
  - Strips trailing punctuation (Piper bug: `.!?` cause noise)
  - Splits sentences → rejoins with `\n` for natural pauses
  - Converts to lowercase (Piper phonemizer issues with uppercase)
  - Post-processing: ffmpeg trims trailing noise artifacts
- **API**:
  - `GET /setup/piper/status` — installed/version
  - `GET /setup/tts/voices` — installed voices (disk scan)
  - `GET /setup/tts/catalog` — available voices (HuggingFace)
  - `POST /setup/tts/download` — download voice (async + progress)
  - `POST /setup/tts/delete` — delete voice
  - `POST /setup/tts/select` — set active voice
  - `POST /setup/tts/preview` — synthesize sample text
  - `POST /setup/tts/speak` — synthesize + play on device
  - `GET /setup/tts/settings` — get TTS parameters
  - `POST /setup/tts/settings` — save TTS parameters

### Ollama LLM
- **Container**: `ollama/ollama` (separate Docker container with nvidia runtime)
- **Models**: `/root/.ollama/` (on `ollama_data` volume, persists across restarts)
- **GPU**: 100% GPU on Jetson via nvidia runtime, CPU on Raspberry Pi
- **API**:
  - `GET /setup/ollama/status` — installed/running/version
  - `POST /setup/ollama/start` — start server (OLLAMA_NUM_GPU=999 if GPU)
  - `POST /setup/ollama/stop` — stop server
  - `GET /setup/ollama/models` — list installed models
  - `POST /setup/ollama/pull` — download model (async + progress bar)
  - `GET /setup/ollama/pull-progress` — poll download progress (percent)
  - `POST /setup/ollama/delete-model` — delete model

---

## Cloud LLM Providers

Supported providers (user can switch freely):

| Provider | Auth | Models Endpoint |
|----------|------|-----------------|
| Ollama (Local) | none | `http://localhost:11434/api/tags` |
| OpenAI | Bearer token | `GET /v1/models` |
| Anthropic | x-api-key | `GET /v1/models` |
| Google AI | ?key= param | `GET /v1beta/models` |
| Groq | Bearer token | `GET /openai/v1/models` |

### API Key Workflow
1. Select provider tab in UI
2. Enter API key → click "Validate"
3. Backend calls provider's `/models` endpoint
4. On success: key saved, model list returned
5. Select model → Save

### Configuration (core.yaml)
```yaml
voice:
  llm_provider: "ollama"      # ollama | openai | anthropic | google | groq
  llm_model: "gemma3:1b"
  ollama_url: "http://localhost:11434"
  system_prompt: "..."         # custom system prompt
  providers:
    openai:
      api_key: "sk-..."
      model: "gpt-4o-mini"
    anthropic:
      api_key: "sk-ant-..."
      model: "claude-sonnet-4-20250514"
    google:
      api_key: "AIza..."
      model: "gemini-2.5-pro"
    groq:
      api_key: "gsk_..."
      model: "llama-3.3-70b-versatile"
    ollama:
      model: "gemma3:1b"
```

### System Prompt
- Editable via UI (LLM section → "System prompt" button)
- Saved in `core.yaml` → `voice.system_prompt`
- TTS formatting rules auto-appended (no markdown/emoji)
- Default prompt defines Selena personality and restrictions
- `GET /setup/llm/system-prompt` — read
- `POST /setup/llm/system-prompt` — save
- `POST /setup/llm/system-prompt/reset` — restore default

---

## Voice Pipeline Test

Full end-to-end test available in Vosk section:

```
1. Record & Recognize    [Mic → 4s recording → Vosk STT → text]
2. Manual AI Query       [Text input → LLM → response]
3. AI Response           [Display + "Speak response" → Piper TTS → audio]
```

**Complete pipeline**: Mic → Vosk STT → AI (any provider) → Piper TTS → Speaker

### API
- `POST /setup/stt/test` — record + STT, returns `{text, audio_b64, peak_level}`
- `POST /setup/llm/chat` — send to active LLM, returns `{response, provider, model}`
- `POST /setup/tts/speak` — synthesize + return WAV + play on device

---

## GPU / Hardware Detection

### Auto-detection at container startup (`start.sh`)
1. Check `nvidia-smi` (standard CUDA)
2. Fallback: `/dev/nvidia0` + `libcuda.so` (Jetson without nvidia-smi)
3. Detect type: `jetson` (Tegra) or `discrete`
4. Export `SELENA_GPU_AVAILABLE=1|0`, `SELENA_GPU_TYPE=jetson|discrete|none`
5. Persist to `core.yaml` → `hardware.gpu_detected`, `hardware.gpu_type`

### Python module: `core/hardware.py`
```python
from core.hardware import is_gpu_available, should_use_gpu, get_gpu_type
```
Single source of truth for all engine code.

### Engine GPU usage
| Engine | GPU Support | How |
|--------|-------------|-----|
| Vosk | No | CPU only (Kaldi design) |
| Piper | Yes (CUDA) | `--cuda` flag if `onnxruntime-gpu` available |
| Ollama | Yes (CUDA) | Separate container with nvidia runtime |

### UI
- Hardware status badge at top of voice settings
- Per-engine acceleration badge: ⚡CUDA (green), CPU (blue), CPU only (gray), ☁Cloud (purple)
- "Force CPU" toggle to disable GPU

### API
- `GET /setup/hardware/status` — `{gpu_detected, gpu_type, gpu_active, onnxruntime_gpu, force_cpu}`
- `POST /setup/hardware/gpu-override` — `{force_cpu: bool}`

---

## Docker Configuration

### CPU-only (Raspberry Pi)
```bash
docker compose up -d
```

### GPU (Jetson Orin)
```bash
./scripts/start-docker.sh
# or manually:
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

### Prerequisites for Jetson GPU
```bash
sudo apt-get install -y cuda-cudart-12-6
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### Docker volumes
| Volume | Mount | Purpose |
|--------|-------|---------|
| `selena_data` | `/var/lib/selena` | Vosk models, Piper voices, cache, DB |
| `selena_secure` | `/secure` | TLS certs, secure config |
| `ollama_data` | `/root/.ollama` | Ollama models (shared between containers) |

### Baked into Docker image (Dockerfile.core)
- `vosk` — STT engine
- `piper-tts` + `pathvalidate` — TTS engine
- `ollama` — CLI client (server runs in separate container on GPU)

---

## Performance

### gemma3:1b on Jetson Orin
| Mode | Time | Processor |
|------|------|-----------|
| CPU | 17-18 sec | 100% CPU |
| **GPU** | **6.8 sec** | **100% GPU** |

### Recommended models for 4GB RAM
| Model | Size | Speed | Languages |
|-------|------|-------|-----------|
| **gemma3:1b** | 815 MB | Fast | en/ru/uk + 140 |
| qwen2.5:1.5b | 2 GB | Fast | en/zh/ru |
| llama3.2:1b | 1.5 GB | Fast | en |

---

## File Structure

```
core/
  hardware.py                    # GPU auto-detection module
  api/routes/
    voice_engines.py             # All voice engine management endpoints
    setup.py                     # Audio devices, provisioning, config

system_modules/
  voice_core/
    tts.py                       # Piper TTS wrapper + sanitization
    stt.py                       # Vosk STT wrapper
  llm_engine/
    cloud_providers.py           # OpenAI/Anthropic/Google/Groq adapters
    model_manager.py             # LLM model lifecycle
    ollama_client.py             # Ollama REST client

src/components/settings/
  VoiceSettings.tsx              # Container + hardware badge
  VoskSection.tsx                # STT + voice pipeline test
  PiperSection.tsx               # TTS + settings sliders
  LlmSection.tsx                 # Provider tabs + system prompt
  AccelBadge.tsx                 # CUDA/CPU/Cloud badge component

docker-compose.yml               # Base (CPU)
docker-compose.gpu.yml           # GPU override (Jetson)
scripts/
  start.sh                       # Container entrypoint (GPU detection)
  start-docker.sh                # Auto-select compose files
```
