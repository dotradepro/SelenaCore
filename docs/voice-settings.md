# Voice Pipeline Configuration

## Pipeline Overview

```
Microphone (arecord, ALSA)
     |
     v
  Vosk STT (language from config, per-model) --> text + stt_lang
     |
     v
  Intent Router
     |-- Tier 0:   IntentCompiler (DB regex patterns)       ~0 ms
     |-- Tier 1:   Module Bus (user modules, WebSocket)     ~ms
     |-- Cache:    IntentCache (SQLite, previous LLM hits)  ~0 ms
     |-- Tier 2:   Local LLM (Ollama, single call)          300-800 ms
     |-- Tier 3:   Cloud LLM (OpenAI-compatible, optional)  1-3 sec
     '-- Fallback: "not understood" (i18n)
     |
     v
  Module executes action via EventBus
     |
     v
  Dual Piper TTS
     |-- Primary voice (system language, GPU)
     '-- Fallback voice (English, CPU)
     |
     v
  split_by_language() --> segments --> correct voice per segment
     |
     v
  aplay (ALSA direct) --> Speaker
```

## Language Architecture

Two language concepts -- do not mix:

| Concept | Source | Purpose |
|---------|--------|---------|
| `stt_lang` | Vosk model language (from config) | Regex matching, cache key |
| `tts_lang` | Piper config `voice.tts.primary.lang` | Response language, voice selection |

Rules:
- `stt_lang == primary_lang` --> primary voice, response in primary language
- `stt_lang != primary_lang` --> fallback EN voice, response in English
- EventBus payload: intent/entity/location/params always in **English**
- Response text: in `tts_lang`

## STT -- Vosk

Speech recognition via Vosk (native, no container needed). Vosk uses streaming (chunk-by-chunk) recognition rather than batch transcription, delivering results as audio is received.

| Platform | Model | Latency |
|----------|-------|---------|
| Jetson Orin | vosk-model-small-uk | ~150ms |
| Linux x86_64 | vosk-model-small-uk | ~100ms |
| Raspberry Pi 5 | vosk-model-small-uk | ~300ms |
| Raspberry Pi 4 | vosk-model-small-uk | ~500ms |

Models are downloaded from [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models) and stored locally. Each language requires its own model.

Configuration:

```yaml
stt:
  provider: vosk
  vosk:
    models_dir: /var/lib/selena/models/vosk
    active_model: vosk-model-small-uk
```

Vosk also supports **grammar mode** for wake word detection -- a constrained vocabulary that improves accuracy and reduces CPU usage during always-on listening.

Language is determined by the active model (per-language models, not auto-detected from speech).

## TTS -- Dual Piper (piper1-gpl)

Two PiperVoice models loaded at startup, both hot in memory:

| Voice | Purpose | Model | GPU | RAM |
|-------|---------|-------|-----|-----|
| Primary | System language (e.g. Ukrainian) | uk_UA-ukrainian_tts-medium | Yes | ~65 MB |
| Fallback | English (for non-primary languages) | en_US-ryan-low | No | ~5 MB |

### Voice Selection Logic

```
Text to speak: "Light turned on"
  --> has Cyrillic? No --> fallback EN voice
  --> aplay with EN voice settings

Text to speak: "Увiмкнено"
  --> has Cyrillic? Yes --> primary UK voice
  --> aplay with UK voice settings
```

### Mixed Language Text

```
"Вмикаю WiFi. Signal good. Температура 23 градуси"
     |
     v  split_by_language("uk")
  ["вмикаю" (uk), "wifi. signal good." (en), "температура 23 градуси" (uk)]
     |
     v  Each segment synthesized by correct voice
  voice_uk --> voice_en --> voice_uk --> concatenate --> aplay
```

### Configuration

```yaml
voice:
  output_volume: 50               # Master playback volume (0-150%)
  tts:
    primary:
      voice: "uk_UA-ukrainian_tts-medium"
      lang: "uk"
      cuda: true
      settings:
        length_scale: 0.65        # Speed (0.3-2.0, lower = faster)
        noise_scale: 0.667        # Intonation variability (0.0-1.0)
        noise_w_scale: 0.8        # Phoneme width variability (0.0-1.0)
        volume: 0.7               # Synthesis volume (0.1-3.0)
        speaker: 1                # Speaker ID (for multi-speaker models)
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

### Piper HTTP Server

Runs natively on host as a systemd service on port 5100:

```bash
# Check status
curl http://localhost:5100/health

# Response:
{
  "status": "ok",
  "loaded_voices": ["uk_UA-ukrainian_tts-medium", "en_US-amy-low"]
}
```

### TTS Text Preprocessing

Before synthesis:
1. **Sanitize** -- remove markdown, URLs, emoji, special chars
2. **Lowercase** -- Piper VITS models garble uppercase
3. **Numbers to words** -- `23` --> `twenty three` (EN) / `двадцять три` (UK) via num2words
4. **Silence padding** -- 150ms silence prepended to prevent aplay pipe from cutting first syllable

## Intent System -- DB-Driven Patterns

All intent patterns are stored in the database (no YAML files):

### Tables

| Table | Purpose |
|-------|---------|
| `intent_definitions` | Intent name, module, priority, description |
| `intent_patterns` | Regex patterns per language per intent |
| `intent_vocab` | Verbs, nouns, params, locations vocabulary |

### Auto-Generated Patterns

When a user adds entities through the UI, regex patterns are automatically generated:

| Entity | Example | Generated Pattern (EN) |
|--------|---------|----------------------|
| Radio station "Hit FM" | `media.play_radio_name` | `(?:play\|put on)\s+(?:radio\s+)?hit fm` |
| Device "Kitchen lamp" | `device.on` | `(?:turn on\|switch on)\s+(?:the\s+)?kitchen lamp` |
| Scene "Movie Night" | `automation.run_scene` | `(?:activate\|run)\s+(?:scene\s+)?movie night` |

### Hot-Reload

When data changes (add/remove station, device, scene):
1. PatternGenerator creates/deletes patterns in DB
2. IntentCompiler recompiles regex cache in memory
3. LLM prompt cache invalidated
4. No restart needed

### Seed Script

Initial patterns migrated from YAML to DB via:

```bash
docker exec selena-core python3 /opt/selena-core/scripts/seed_intents_to_db.py
```

## Audio Settings

### Master Volume

```yaml
voice:
  output_volume: 50    # 0-150%, applied as software PCM scaling
```

### Per-Module Volume

Audio sources are auto-discovered from modules with media intents:

- **Selena TTS** -- `voice.output_volume` (software scaling)
- **Media Player** -- internal volume (VLC level)

### Audio Devices

```yaml
voice:
  audio_force_input: "hw:0,0"     # ALSA microphone device
  audio_force_output: "plughw:1,3" # ALSA speaker device
```

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/ui/setup/audio/devices` | GET | List audio I/O devices |
| `/api/ui/setup/audio/levels` | GET/POST | Master volume + mic gain |
| `/api/ui/setup/audio/sources` | GET | Per-module volume list |
| `/api/ui/setup/audio/sources/volume` | POST | Set module volume |
| `/api/ui/setup/audio/test/output` | POST | Speaker test |
| `/api/ui/setup/audio/test/input` | POST | Microphone test |
| `/api/ui/setup/tts/dual-status` | GET | Dual voice status |
| `/api/ui/setup/tts/dual-config` | POST | Save dual voice config |
| `/api/ui/setup/tts/test` | POST | Test specific voice |
| `/api/ui/setup/tts/test-mix` | POST | Test mixed-language synthesis |

## LLM Configuration

### Local LLM (Ollama)

```yaml
ai:
  conversation:
    provider: "local"
    local:
      host: "http://localhost:11434"
      model: "qwen2.5:3b"
      options:
        temperature: 0.1
        num_predict: 80
```

### Cloud LLM (optional)

```yaml
ai:
  conversation:
    cloud:
      url: "https://api.groq.com/openai/v1"
      key: "${GROQ_API_KEY}"
      model: "llama-3.1-8b-instant"
```

### Dynamic LLM Prompt

The system prompt is built dynamically from DB:
- Registered modules with their intents
- Known devices
- Radio stations
- Scenes

Cached per `tts_lang`, invalidated on data changes.

## RAM Budget (Jetson 8GB headless)

```
OS headless (no GNOME)       0.65 GB
Vosk small model             0.05 GB
SelenaCore + modules         0.30 GB
qwen2.5:3b (Ollama Q4)      2.00 GB
Piper uk medium (GPU)        0.065 GB
Piper en low (CPU)           0.005 GB
-----------------------------------------
Total:                       3.07 GB
Free:                        4.93 GB
```
