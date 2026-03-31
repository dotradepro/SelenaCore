# Voice Pipeline Configuration

## Pipeline Overview

Wake word → Audio recording → Vosk STT → Speaker ID (resemblyzer) → Intent Router (6-tier) → Cloud LLM Rephrase → Piper TTS

```
Microphone (parecord)
     │
     ▼
  Vosk STT ──► text
     │
     ▼
  Intent Router
     ├── Tier 1:   Fast Matcher (keyword/regex)          ~0 ms
     ├── Tier 1.5: System Module Intents (in-process)    ~μs
     ├── Tier 2:   Module Bus (user modules, WebSocket)  ~ms
     ├── Tier 3a:  Cloud LLM Classification (Gemini/…)   ~1-2 sec
     ├── Tier 3b:  Ollama LLM (local, RAM ≥ 5GB)         3-8 sec
     └── Fallback: i18n "not understood"
     │
     ▼
  Module executes action
     │
     ▼
  Cloud LLM Rephrase (variative TTS)
     │
     ▼
  Piper TTS (native host server, CPU/GPU) → Speaker
```

## STT - Vosk

- Offline speech recognition (Kaldi engine)
- ARM-optimized for Raspberry Pi
- Models: tiny, base, small, medium (in `/var/lib/selena/models/vosk/`)
- Configured in core.yaml: `voice.stt_model`

## TTS - Piper

- ONNX-based text-to-speech via native host server (`piper-server.py`)
- Models loaded once and kept warm in memory (~100-400ms CPU, ~30-80ms GPU)
- CPU/GPU mode: `--device auto|cpu|gpu` (auto-detects CUDAExecutionProvider)
- Models in `/var/lib/selena/models/piper/`
- Configured in core.yaml: `voice.tts_voice`, `voice.tts_settings`

### Piper TTS Server

Runs natively on host (not in Docker) as a systemd service on port 5100.

```bash
# Start manually
python3 scripts/piper-server.py --port 5100 --device auto

# systemd service
sudo systemctl enable --now piper-tts
```

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| POST | `/synthesize` | Text → WAV audio |
| POST | `/synthesize/raw` | Text → raw PCM s16le (for streaming to paplay) |
| GET | `/health` | Status, device (cpu/gpu), loaded voices |
| GET | `/voices` | List installed voice models |

**GPU acceleration:** Requires `onnxruntime-gpu` with CUDAExecutionProvider.

On Jetson (JetPack 6, CUDA 12.x):

```bash
# Automated install (recommended)
bash scripts/build-onnxruntime-gpu.sh

# Or manual steps:
pip3 install --user onnxruntime-gpu --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu126
pip3 install --user "numpy<2"                    # NumPy 2.x is incompatible
sudo ln -sf /usr/lib/aarch64-linux-gnu/libcudnn.so.9 /usr/lib/aarch64-linux-gnu/libcudnn.so
sudo systemctl restart piper-tts
```

> **Note:** PyPI `onnxruntime-gpu` does NOT support aarch64. Use the NVIDIA Jetson AI Lab index.

### TTS Performance (Jetson Orin Nano)

| Text | CPU (warm) | GPU (est.) | Cold start |
|------|-----------|-----------|------------|
| Short (1 word) | ~420 ms | ~280 ms | ~2500 ms |
| Medium (4 words) | ~780 ms | ~500 ms | ~2500 ms |
| Long (15 words) | ~2280 ms | ~740 ms | ~2500 ms |

## Wake Word

- openWakeWord / Vosk grammar-based
- Sensitivity: core.yaml `voice.wake_word_sensitivity` (0.0 to 1.0)

## Speaker ID

- resemblyzer library for voice print fingerprinting
- Local-only processing, no cloud

## Privacy Mode

- Toggle via voice command or GPIO pin
- Events: `voice.privacy_on`, `voice.privacy_off`
- Config: `voice.privacy_gpio_pin`

---

## Intent Routing (6-tier)

The intent router uses a multi-tier cascade. Each tier is tried in order; the first match wins.

| Tier | Name | Latency | Mechanism | Source |
|------|------|---------|-----------|--------|
| 1 | Fast Matcher | ~0 ms | YAML keyword/regex rules | `fast_matcher.py` |
| 1.5 | System Module Intents | ~μs | In-process regex with named groups | `intent_router.py` |
| 2 | Module Bus | ~ms | WebSocket round-trip to user modules | `module_bus.py` |
| 3a | Cloud LLM Classification | ~1-2 sec | Structured intent JSON via Gemini/OpenAI/etc. | `cloud_providers.py` |
| 3b | Ollama LLM | 3-8 sec | Local model semantic understanding (RAM ≥ 5GB) | `ollama_client.py` |
| — | Fallback | ~0 ms | i18n "not understood" message | `i18n` |

### Tier 1: Fast Matcher

Keyword and regex rules defined in `/opt/selena-core/config/intent_rules.yaml` or built-in defaults. Zero latency. Supports basic device control (lights, temperature, privacy).

### Tier 1.5: System Module Intents

System modules register `SystemIntentEntry` patterns at startup. Patterns support named regex groups for parameter extraction (e.g., `(?P<genre>rock|jazz)`). 28 intents registered across 6 modules.

### Tier 2: Module Bus

User modules (running in containers) register intents via WebSocket `announce` message. The Module Bus maintains a sorted intent index and routes commands with circuit breaker pattern.

### Tier 3a: Cloud LLM Intent Classification

When regex tiers miss, the router sends the command to a configured cloud LLM provider for structured intent classification. This is critical on Raspberry Pi where local Ollama is disabled (RAM < 5GB).

**How it works:**

1. Router dynamically builds an intent catalog from all registered intents (Tier 1 + 1.5 + 2)
2. Sends a classification prompt to the cloud LLM (temperature=0.0 for deterministic output)
3. LLM returns structured JSON: `{"intent": "media.play_radio", "params": {}}`
4. If the intent is a general question, LLM returns: `{"intent": "llm.response", "params": {}, "response": "..."}`

**Supported providers:** OpenAI, Anthropic, Google AI (Gemini), Groq

**Timeout:** 15 seconds

### Tier 3b: Ollama LLM

Local model fallback for devices with sufficient RAM (≥ 5GB). Uses compact system prompt optimized for small models. Automatically disabled on low-RAM devices.

---

## Cloud LLM Configuration

```yaml
voice:
  llm_provider: "google"          # "ollama" | "llamacpp" | "openai" | "anthropic" | "google" | "groq"
  providers:
    google:
      api_key: "AIza..."
      model: "gemini-2.0-flash"
    openai:
      api_key: "sk-..."
      model: "gpt-4o-mini"
    anthropic:
      api_key: "sk-ant-..."
      model: "claude-3-haiku-20240307"
```

Configure via UI: **Settings → System Modules → Voice Core → LLM Router**

---

## LLM Response Rephrase (optional)

When enabled (`voice.rephrase_enabled: true`), system module responses are rephrased via LLM before TTS playback. **Disabled by default** to reduce latency (saves 3-10s per response on local LLM).

**How it works (when enabled):**

1. Module calls `m.speak("Playing radio station Kiss FM")`
2. `voice.speak` event reaches voice-core
3. voice-core sends the default text + conversation context to LLM
4. LLM rephrases it naturally (temperature=0.9 for variety)
5. Rephrased text is spoken via Piper TTS
6. Falls back to original text if LLM is unavailable

**Conversation session:** last 20 messages (user + assistant) are kept in memory, reset after 5 minutes of inactivity.

---

## Command Test Console

A debug UI for testing voice commands without speaking. Located at:

**Settings → System Modules → Voice Core → Command Test Console** (bottom of the page)

Features:
- Text input field to simulate voice commands
- TTS toggle checkbox (speak the response or just show results)
- Full pipeline trace showing each tier's status (hit/miss/skip/error) with timing
- Result display: intent name, source tier, latency, response text, action, params
- Enter key to send

**API endpoint:** `POST /api/ui/modules/voice-core/test-command`

```json
// Request
{"text": "turn on the radio", "speak": false}

// Response
{
  "ok": true,
  "input_text": "turn on the radio",
  "lang": "en",
  "intent": "media.play_radio",
  "source": "system_module",
  "latency_ms": 5,
  "duration_ms": 5,
  "action": null,
  "params": {},
  "tts_played": false,
  "trace": [
    {"tier": "1", "name": "Fast Matcher", "status": "miss", "ms": 1},
    {"tier": "1.5", "name": "System Module Intents", "status": "hit", "ms": 5, "detail": "media-player::media.play_radio", "registered": 28}
  ]
}
```

---

## Voice Commands Reference

### media-player (14 intents)

| Intent | Description | Example (UK) | Example (EN) |
|--------|-------------|--------------|--------------|
| `media.play_radio` | Play radio | "увімкни радіо" | "play radio" |
| `media.play_genre` | Play by genre | "увімкни джаз" | "play jazz music" |
| `media.play_radio_name` | Play station by name | "увімкни радіо Kiss FM" | "play station Kiss FM" |
| `media.play_search` | Search and play | "знайди Yesterday" | "find Yesterday" |
| `media.pause` | Pause | "пауза" | "pause" |
| `media.resume` | Resume | "продовжуй" | "resume" |
| `media.stop` | Stop | "стоп" | "stop" |
| `media.next` | Next track | "наступний" | "next" |
| `media.previous` | Previous track | "попередній" | "previous" |
| `media.volume_up` | Volume up | "гучніше" | "louder" |
| `media.volume_down` | Volume down | "тихіше" | "quieter" |
| `media.volume_set` | Set volume | "гучність на 50" | "volume 50" |
| `media.whats_playing` | What's playing | "що грає" | "what's playing" |
| `media.shuffle_toggle` | Toggle shuffle | "перемішай" | "shuffle" |

### weather-service (3 intents)

| Intent | Description | Example (UK) | Example (EN) |
|--------|-------------|--------------|--------------|
| `weather.current` | Current weather | "яка погода" | "what's the weather" |
| `weather.forecast` | Weather forecast | "прогноз на завтра" | "weather forecast" |
| `weather.temperature` | Temperature | "скільки градусів" | "what's the temperature" |

### presence-detection (3 intents)

| Intent | Description | Example (UK) | Example (EN) |
|--------|-------------|--------------|--------------|
| `presence.who_home` | Who is home | "хто вдома" | "who is home" |
| `presence.check_user` | Check specific user | "чи є Олена вдома" | "is Alice home" |
| `presence.status` | Presence status | "статус присутності" | "presence status" |

### automation-engine (4 intents)

| Intent | Description | Example (UK) | Example (EN) |
|--------|-------------|--------------|--------------|
| `automation.list` | List automations | "які автоматизації" | "list automations" |
| `automation.enable` | Enable automation | "увімкни автоматизацію X" | "enable automation X" |
| `automation.disable` | Disable automation | "вимкни автоматизацію X" | "disable automation X" |
| `automation.status` | Automation status | "статус автоматизацій" | "automation status" |

### energy-monitor (2 intents)

| Intent | Description | Example (UK) | Example (EN) |
|--------|-------------|--------------|--------------|
| `energy.current` | Current consumption | "яке споживання" | "power consumption" |
| `energy.today` | Today's energy | "скільки електрики сьогодні" | "energy today" |

### device-watchdog (2 intents)

| Intent | Description | Example (UK) | Example (EN) |
|--------|-------------|--------------|--------------|
| `watchdog.status` | Device status | "статус пристроїв" | "device status" |
| `watchdog.scan` | Scan devices | "перевір пристрої" | "scan devices" |

### Fast Matcher intents (5 intents)

| Intent | Description | Example (UK) | Example (EN) |
|--------|-------------|--------------|--------------|
| `turn_on_light` | Turn on light | "увімкни світло" | "turn on light" |
| `turn_off_light` | Turn off light | "вимкни світло" | "turn off light" |
| `temperature_query` | Temperature query | "яка температура" | "what's the temperature" |
| `privacy_on` | Enable privacy | "не слухай" | "privacy on" |
| `privacy_off` | Disable privacy | "вийди з приватного" | "privacy off" |

---

## Voice Events

| Event | Description |
|-------|-------------|
| `voice.wake_word` | Wake word detected |
| `voice.recognized` | STT text output |
| `voice.intent` | Intent matched (includes intent, source, params, latency) |
| `voice.response` | TTS response text generated |
| `voice.speak` | Request to speak text (EventBus → voice-core) |
| `voice.speak_done` | TTS playback complete |
| `voice.privacy_on` | Privacy mode enabled |
| `voice.privacy_off` | Privacy mode disabled |

## Voice Config in core.yaml

```yaml
voice:
  wake_word_sensitivity: 0.5
  stt_model: "vosk-model-small-uk-v3-nano"
  stt_silence_timeout: 1.0            # seconds of silence before processing (0.5-5.0)
  tts_voice: "uk_UA-ukrainian_tts-medium"
  rephrase_enabled: false              # LLM rephrase for module responses (adds latency)
  tts_settings:
    length_scale: 1.0                  # speech speed (0.5=fast, 2.0=slow)
    noise_scale: 0.667                 # intonation variability (0.0-1.0)
    noise_w_scale: 0.8                 # phoneme width variability (0.0-1.0)
    sentence_silence: 0.2             # pause between sentences (seconds)
    volume: 1.0                        # volume (0.1-3.0)
    speaker: 0                         # speaker ID for multi-speaker models
  privacy_gpio_pin: null
  llm_provider: "google"
  providers:
    google:
      api_key: "AIza..."
      model: "gemini-2.0-flash"
```

## WebRTC Streaming

- Supports real-time audio streaming via WebRTC
- Used for browser-based voice interaction
