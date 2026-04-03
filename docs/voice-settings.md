# Voice Pipeline Configuration

## Pipeline Overview

Wake word → Audio recording → Whisper STT → Speaker ID (resemblyzer) → Intent Router (6-tier) → Cloud LLM Rephrase → Piper TTS

```
Microphone (arecord, ALSA)
     │
     ▼
  Whisper STT ──► text
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
  Piper TTS (native host server, CPU/GPU) → aplay (ALSA) → Speaker
```

## STT - Whisper

- Speech recognition via whisper.cpp HTTP server running at `http://localhost:9000`
- Models: ggml format (tiny, base, small, medium) in `whisper.cpp/models/`
- Supports GPU acceleration on NVIDIA Jetson (CUDA)
- Configured in core.yaml: `stt.whisper_cpp.host`

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
| POST | `/synthesize/raw` | Text → raw PCM s16le (for streaming to aplay) |
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

- openWakeWord
- Sensitivity: core.yaml `voice.wake_word_sensitivity` (0.0 to 1.0)

## Speaker ID

- resemblyzer library for voice print fingerprinting
- Local-only processing, no cloud

## Privacy Mode

- Toggle via voice command or GPIO pin
- Events: `voice.privacy_on`, `voice.privacy_off`
- Config: `voice.privacy_gpio_pin`

---

## Intent Routing (multi-tier)

The intent router uses a multi-tier cascade. Each tier is tried in order; the first match wins.

| Tier | Name | Latency | Mechanism | Source |
|------|------|---------|-----------|--------|
| 1 | Fast Matcher | ~0 ms | YAML keyword/regex rules | `fast_matcher.py` |
| 1.5 | IntentCompiler | ~0.1 ms | YAML vocabulary → compiled regex | `intent_compiler.py` |
| 1.7 | SmartMatcher | ~2-4 ms | TF-IDF cosine similarity | `smart_matcher.py` |
| 2 | Module Bus | ~5-50 ms | WebSocket round-trip to user modules | `module_bus.py` |
| 3 | LLM (two-step or single) | ~0.5-8 sec | Ollama / Cloud LLM classification | `intent_router.py` |
| — | Fallback | ~0 ms | i18n "not understood" message | `i18n` |

### Tier 1: Fast Matcher

Keyword and regex rules defined in `/opt/selena-core/config/intent_rules.yaml` or built-in defaults. Zero latency. Supports basic device control (lights, temperature, privacy, media transport).

### Tier 1.5: IntentCompiler

28 intents defined in `config/intents/definitions.yaml` with YAML templates and vocabulary. IntentCompiler expands templates into regex at startup (cached via pickle). Supports named groups for parameter extraction (e.g., `(?P<genre>rock|jazz)`). Vocabulary files per language in `config/intents/vocab/{en,uk}.yaml`.

### Tier 1.7: SmartMatcher

TF-IDF + cosine similarity catches near-miss utterances that regex misses. Uses noun_class pre-filtering (only compares within same semantic class). Two thresholds: confident (≥ 0.55, stops routing) and uncertain (≥ 0.46, falls through). **AutoLearner** saves successful LLM classifications → SmartMatcher learns and catches them next time (~3ms vs ~3sec).

### Tier 2: Module Bus

User modules (running in containers) register intents via WebSocket `announce` message. The Module Bus maintains a sorted intent index and routes commands with circuit breaker pattern.

### Tier 3: LLM Classification

When local tiers miss, the router sends the command to LLM for classification.

**Two-step mode** (`voice.llm_two_step: true`):
1. Step 1: classify noun_class (~6 categories, ~100ms)
2. Step 2: extract intent within class (~5 intents, ~400ms)

**Single-step mode** (default): full intent catalog in one prompt.

**Supported providers:** Ollama (local), llama.cpp (local), OpenAI, Anthropic, Google AI (Gemini), Groq

**AutoLearner:** successful LLM results saved to SmartMatcher for future fast matching.

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

## Audio Subsystem

### Overview

SelenaCore uses **ALSA direct** for all audio I/O. PulseAudio is not required.

```
                        ┌─────────────────────────┐
  USB Mic (plughw:0,0)──►  arecord (voice loop)   │
                        │  s16le, 16kHz, mono      │
                        └───────────┬─────────────┘
                                    ▼
                           Whisper STT server
                                    │
                          ┌─────────▼──────────┐
                          │   Intent Router    │
                          └─────────┬──────────┘
                                    ▼
              ┌────────────────┬────┴────┬──────────────┐
              ▼                ▼         ▼              ▼
         Piper TTS        VLC (radio)  Module      voice.speak
              │                │       action          event
              ▼                ▼
    ┌─────────────────┐  ┌──────────────────┐
    │ Software volume │  │ VLC ALSA output  │
    │ (PCM scaling)   │  │ (--aout=alsa)    │
    └────────┬────────┘  └────────┬─────────┘
             ▼                    ▼
    ┌────────────────────────────────────────┐
    │   HDMI output (plughw:1,3)             │
    │   ALSA plughw auto-resampling          │
    └────────────────────────────────────────┘
```

### Device Detection

Audio devices are detected via `aplay -l` / `arecord -l` (ALSA fallback) or PulseAudio
(`pactl list`) when available. Detection logic:

1. Parse real device numbers from `aplay -l` (e.g., `hw:1,3` for HDMI 0)
2. Filter out internal virtual buses (tegra APE/ADMAIF on Jetson)
3. Classify devices: `usb`, `i2s_gpio`, `bluetooth`, `hdmi`, `jack`, `builtin`
4. Use `plughw:X,Y` prefix for automatic format/rate/channel conversion

**Priority order:**

| Direction | Priority (highest first) |
|-----------|--------------------------|
| Input     | usb > i2s_gpio > bluetooth > hdmi > builtin |
| Output    | usb > i2s_gpio > bluetooth > hdmi > jack > builtin |

**Classification rules:**

| Keyword in name | Type |
|-----------------|------|
| `usb`           | usb |
| `i2s`, `rpi`, `simple` | i2s_gpio |
| `hdmi`, `hda`   | hdmi |
| `jack`, `headphone` | jack |
| (other)         | builtin |

### Audio Configuration (core.yaml)

```yaml
voice:
  audio_force_input: "plughw:0,0"     # ALSA capture device (or null for auto)
  audio_force_output: "plughw:1,3"    # ALSA playback device (or null for auto)
  output_volume: 100                   # TTS output volume 0-150 (software PCM scaling)
  input_gain: 100                      # Microphone gain 0-150 (applied via amixer)
```

### Volume Control

**TTS (voice-core):** Software volume — PCM samples are scaled by `output_volume / 100`
before being sent to `aplay`. Works with any ALSA device including HDMI (which has no
hardware mixer).

**Microphone gain:** Applied via `amixer -c N sset 'Control' X%` where `N` is the card
number and `Control` is the first capture volume control found on that card.

**Media Player (VLC):** VLC's internal `audio_set_volume()` (0-100). Controlled via
the Audio Sources UI or voice commands (`media.volume_up`, `media.volume_down`,
`media.volume_set`).

### Audio Sources

System modules that register audio intents (`media.*` in `manifest.json`) are
automatically discovered as audio sources. Each source gets an independent volume slider
in **Settings → Audio → Audio Sources**.

**Auto-discovery:** On `GET /api/ui/setup/audio/sources`, the API iterates running
modules and checks if their manifest `intents` list contains entries starting with
`media.`. Matching modules appear as audio sources.

**Built-in sources:**

| Source | Volume storage | Control method |
|--------|---------------|----------------|
| Selena TTS | `voice.output_volume` in core.yaml | Software PCM scaling |
| Media Player | VLC runtime (resets on restart) | `player.audio_set_volume()` |

**Adding a new audio source:** Any system module with `media.*` intents in its
`manifest.json` will automatically appear. The module must expose `_player._volume`
attribute and `_player.set_volume(int)` async method.

### Audio Test Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ui/setup/audio/devices` | List detected input/output devices |
| POST | `/api/ui/setup/audio/select` | Save device selection to core.yaml |
| POST | `/api/ui/setup/audio/test/output` | Play speaker-test (left/right voice) at configured volume |
| POST | `/api/ui/setup/audio/test/input` | Record 3s from mic, measure peak, play back on speaker |
| GET | `/api/ui/setup/audio/mic-level` | Quick 1s mic sample, return peak level (0.0-1.0) |
| GET | `/api/ui/setup/audio/levels` | Get current output_volume and input_gain |
| POST | `/api/ui/setup/audio/levels` | Set output_volume and/or input_gain |
| GET | `/api/ui/setup/audio/sources` | List audio source modules with volumes |
| POST | `/api/ui/setup/audio/sources/volume` | Set volume for a specific source |

**Mic test concurrency:** The mic test automatically pauses the voice loop (kills the
running `arecord` process), records the test, then resumes the voice loop.

### NVIDIA Jetson Audio Notes

On Jetson Orin Nano, the audio card layout is:

| Card | Name | Devices | Type |
|------|------|---------|------|
| 0 | UACDemoV1.0 (USB) | `hw:0,0` capture | USB microphone |
| 1 | NVIDIA Jetson Orin Nano HDA | `hw:1,3` `hw:1,7` `hw:1,8` `hw:1,9` | HDMI outputs |
| 2 | NVIDIA Jetson Orin Nano APE | 20× tegra-dlink ADMAIF | Internal bus (filtered) |

- HDMI audio requires stereo minimum and specific sample rates — use `plughw:` for
  automatic format conversion
- Card 2 (APE) is an internal NVIDIA audio bus with 20 virtual ADMAIF channels — these
  are filtered from the device list as they are not real audio endpoints
- The HDA card name does not contain "hdmi" but the classifier recognizes `hda` as HDMI type

### Media Player Audio

The media-player module uses python-vlc (libvlc) with ALSA output:

```python
# VLC flags (set automatically)
--aout=alsa
--alsa-audio-device=plughw:1,3    # from core.yaml audio_force_output
```

The output device is read from `voice.audio_force_output` in core.yaml at startup.
To override, set `MEDIA_AUDIO_OUTPUT=alsa` and `MEDIA_ALSA_DEVICE=plughw:1,3` env vars.

---

## Voice Config in core.yaml

```yaml
voice:
  wake_word_sensitivity: 0.5
  stt_model: "ggml-small"
  stt_silence_timeout: 1.0            # seconds of silence before processing (0.5-5.0)
  tts_voice: "uk_UA-ukrainian_tts-medium"
  rephrase_enabled: false              # LLM rephrase for module responses (adds latency)
  audio_force_input: "plughw:0,0"     # ALSA capture device
  audio_force_output: "plughw:1,3"    # ALSA playback device
  output_volume: 100                   # TTS software volume (0-150)
  input_gain: 100                      # Mic gain via amixer (0-150)
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
