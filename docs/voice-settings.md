# Voice Pipeline Configuration

## Pipeline Overview

Wake word (openWakeWord) → Audio recording → Vosk STT → Speaker ID (resemblyzer) → Intent Router (4-tier) → Piper TTS

## STT - Vosk

- Offline speech recognition (Kaldi engine)
- ARM-optimized for Raspberry Pi
- Models: tiny, base, small, medium (in `/var/lib/selena/models/vosk/`)
- Configured in core.yaml: `voice.stt_model`

## TTS - Piper

- ONNX-based text-to-speech
- CUDA support on Jetson
- Models in `/var/lib/selena/models/piper/`
- Configured in core.yaml: `voice.tts_voice`

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

## Intent Routing (4-tier)

1. **Fast Matcher** — YAML keyword/regex rules → 0ms
2. **System Module Intents** — in-process regex patterns → microseconds
3. **Module Bus Intents** — user modules via WebSocket → milliseconds
4. **Ollama LLM** — semantic understanding → 3-8 sec (requires 5GB+ RAM)

## LLM Configuration

```yaml
llm:
  enabled: true
  provider: "ollama"
  ollama_url: "http://localhost:11434"
  default_model: "phi-3-mini"
  min_ram_gb: 5
  timeout_sec: 30
```

## Voice Events

- `voice.wake_word` — wake word detected
- `voice.recognized` — STT text output
- `voice.intent` — intent matched
- `voice.response` — TTS response generated
- `voice.privacy_on` / `voice.privacy_off` — privacy mode toggled

## Voice Config in core.yaml

```yaml
voice:
  wake_word_sensitivity: 0.5
  stt_model: "base"
  tts_voice: "uk_UA-lada-x_low"
  privacy_gpio_pin: null
```

## WebRTC Streaming

- Supports real-time audio streaming via WebRTC
- Used for browser-based voice interaction
