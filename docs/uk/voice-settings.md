# Конфігурація голосового конвеєра

## Огляд конвеєра

Wake word (openWakeWord) → Запис аудіо → Vosk STT → Ідентифікація мовця (resemblyzer) → Intent Router (4 рівні) → Piper TTS

## STT - Vosk

- Офлайн-розпізнавання мовлення (рушій Kaldi)
- Оптимізовано для ARM на Raspberry Pi
- Моделі: tiny, base, small, medium (у `/var/lib/selena/models/vosk/`)
- Налаштовується в core.yaml: `voice.stt_model`

## TTS - Piper

- Синтез мовлення на основі ONNX
- Підтримка CUDA на Jetson
- Моделі в `/var/lib/selena/models/piper/`
- Налаштовується в core.yaml: `voice.tts_voice`

## Wake Word

- openWakeWord / граматичний режим Vosk
- Чутливість: core.yaml `voice.wake_word_sensitivity` (від 0.0 до 1.0)

## Ідентифікація мовця

- Бібліотека resemblyzer для зняття голосового відбитка
- Лише локальна обробка, без хмари

## Режим приватності

- Перемикання голосовою командою або через GPIO-пін
- Події: `voice.privacy_on`, `voice.privacy_off`
- Конфігурація: `voice.privacy_gpio_pin`

## Маршрутизація інтентів (4 рівні)

1. **Fast Matcher** — правила на ключових словах/regex у YAML → 0 мс
2. **Інтенти системних модулів** — regex-шаблони в процесі → мікросекунди
3. **Інтенти Module Bus** — користувацькі модулі через WebSocket → мілісекунди
4. **Ollama LLM** — семантичне розуміння → 3-8 сек (потрібно 5 ГБ+ оперативної пам'яті)

## Конфігурація LLM

```yaml
llm:
  enabled: true
  provider: "ollama"
  ollama_url: "http://localhost:11434"
  default_model: "phi-3-mini"
  min_ram_gb: 5
  timeout_sec: 30
```

## Голосові події

- `voice.wake_word` — виявлено wake word
- `voice.recognized` — текстовий результат STT
- `voice.intent` — знайдено відповідний інтент
- `voice.response` — згенеровано TTS-відповідь
- `voice.privacy_on` / `voice.privacy_off` — перемикання режиму приватності

## Голосова конфігурація в core.yaml

```yaml
voice:
  wake_word_sensitivity: 0.5
  stt_model: "base"
  tts_voice: "uk_UA-lada-x_low"
  privacy_gpio_pin: null
```

## WebRTC-стримінг

- Підтримка потокового передавання аудіо в реальному часі через WebRTC
- Використовується для голосової взаємодії через браузер
