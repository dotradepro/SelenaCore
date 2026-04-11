# Перекладач Helsinki-NLP (CTranslate2)

SelenaCore постачається з двома backend'ами перекладу за єдиним
інтерфейсом. За замовчуванням — **Argos Translate**: без налаштувань,
ставиться через UI. Для користувачів, яким потрібна вища якість
перекладу — особливо `en→цільова мова` для TTS — є другий backend:
**Helsinki-NLP / opus-mt** на існуючому runtime CTranslate2.

Цей документ описує:

1. Коли обирати Helsinki, а коли Argos
2. Одноразову конвертацію моделей у Google Colab (PyTorch на Jetson не
   потрібен)
3. Обов'язковий розклад файлів у папці моделі
4. Як встановити й активувати
5. Як додати нові мовні пари

> **Архітектура не змінюється.** Обидва backend'и поділяють інтерфейс
> `InputTranslator` / `OutputTranslator` у
> [`core/translation/local_translator.py`](../../core/translation/local_translator.py).
> Усі шість callsite'ів пайплайну користуються `get_input_translator()`
> / `get_output_translator()`. Вибір backend'у живе у
> `translation.engine` (`argos` | `helsinki`) у `core.yaml` і
> переписується кожним кліком **Activate** в UI.

## Коли використовувати Helsinki

Trace-bench (`tests/benchmark/run_trace_bench.py`) на `qwen2.5:1.5b`
показує, як одні й ті самі українські фрази перекладаються по-різному:

| Українською | Argos | Helsinki tc-big-zle-en |
|---|---|---|
| `яка температура у вітальні` | `What a temperature in the living room.` | `What is the temperature in the living room?` |
| `встанови режим охолодження` | `Set the coolant mode.` | `Set the cooling mode.` |
| `вмикни джазове радіо` | `Put your jazz radio down.` | `Turn on the jazz radio.` |
| `замкни вхідні двері` | `Shut the front door.` | `Lock the front door.` |

Класифікатор LLM далі по конвеєру — той самий. Виграш повністю на
рівні перекладача. Найбільший — на `en→ua` для TTS, бо стара модель
`Helsinki-NLP/opus-mt-en-uk` має відомий баг — генерує російський
текст замість українського. Ми навмисно використовуємо
**Tatoeba Challenge** модель `tc-big-en-zle` (Англійська → Східно-
слов'янські), яка потребує токену `>>ukr<<` на початку — wrapper це
робить прозоро через `_OUTPUT_LANG_TOKENS` у
[`core/translation/helsinki_translator.py`](../../core/translation/helsinki_translator.py).

Для напряму input використовуємо `tc-big-zle-en` (Східно-слов'янські
→ Англійська), її пара. Той самий формат файлів, токен мови не
потрібен бо ціль завжди англійська.

Залишайтеся на Argos якщо:

- Не хочете запускати одноразову конвертацію в Colab.
- Ваша мовна пара має свіжий (1.9+) Argos-пакет.
- Достатньо приблизного розуміння (наприклад, wake-word + прості
  команди).

Переходьте на Helsinki якщо:

- Argos-пакет `en→target` старий або помітно слабкий (правда для
  української — Argos постачає v1.4 від 2021).
- Ви будуєте голосовий асистент і важлива якість TTS, потрібна
  чиста українська (а не російська).
- Корпус містить ідіоми / питання, які Argos перекладає буквально.

## Вибір сімейства моделей

Helsinki-NLP публікує три сімейства opus-mt моделей за зростанням
якості / розміру:

| Сімейство | Приклад | Розмір (int8) | Нотатки |
|---|---|---|---|
| `opus-mt-{src}-{tgt}` | `opus-mt-uk-en` | ~80 МБ | Оригінальні 2020-2021. Одна пара. Старі тренувальні дані. Напрям `en-uk` має відомий баг — генерує російський. |
| `opus-mt-tc-base-{src}-{tgt}` | `opus-mt-tc-base-uk-en` | ~120 МБ | Tatoeba Challenge base. Краща якість, 2022. Одна пара. Не для всіх мов. |
| `opus-mt-tc-big-{src}-{tgt}` або `tc-big-{group}-{tgt}` | `opus-mt-tc-big-zle-en` | ~240 МБ | Tatoeba Challenge big. Найвища якість, 2022-2023. Group-варіанти (`zle`, `zls`, `gem`, …) покривають кілька мов однією моделлю. Multi-target варіанти потребують префікс `>>xxx<<`. |

**Для української використовуємо:**

- **Input** (`uk → en`): `Helsinki-NLP/opus-mt-tc-big-zle-en` —
  multi-source Східно-слов'янські (білоруська + російська + українська
  + ще кілька) → англійська. Токен мови не потрібен; модель
  визначає джерело автоматично.
- **Output** (`en → uk`): `Helsinki-NLP/opus-mt-tc-big-en-zle` —
  англійська → multi-target Східно-слов'янські. Потребує `>>ukr<<`
  як окремий vocab piece (НЕ як текст), інакше за замовчуванням
  видає російський. Wrapper це робить; вам не треба думати про це
  після встановлення.

## Крок 1: Конвертувати opus-mt моделі в Colab (одноразово)

Jetson Orin і Raspberry Pi не мають достатньо нової версії PyTorch
для конвертера. Запустіть це один раз у Google Colab (безкоштовного
CPU-runtime достатньо; уся конвертація — близько 5 хвилин на напрям
після того, як модель завантажена).

### Простий шлях: відкрити готовий ноутбук

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/drive/14e_lpp8kuUJXvnjhybdtI_1z3rK9TOd1)

Натисніть на значок вище → **File → Save a copy in Drive** (щоб ваші
правки не впливали на спільний ноутбук) → запускайте клітинки зверху
вниз. Ноутбук містить ті самі п'ять клітинок, що описані нижче;
inline-копії залишені як запасний варіант на випадок якщо спільне
посилання колись зламається або ви хочете зрозуміти що робить кожен
крок.

### Ручний шлях: зібрати ноутбук самостійно

> **Чому стільки клітинок?** Якщо запхати все в одну клітинку,
> здається що конвертація зависла — `subprocess.run` ковтає stdout
> до завершення. Розбиття на окремі клітинки з `!` shell-магією
> стримить вивід наживо, і ви бачите що насправді відбувається. Крок
> завантаження зокрема може зависнути на rate-limit'ах HuggingFace —
> важливо побачити це одразу.

Відкрийте [colab.research.google.com](https://colab.research.google.com),
**New notebook**, вставте кожну клітинку нижче в окрему клітинку та
запускайте зверху вниз. Повторіть для зворотного напряму, поміняючи
коди мов (`uk-en` → `en-uk`).

### Клітинка 1 — встановити конвертер (~30 сек)

```python
!pip install -q ctranslate2 transformers sentencepiece huggingface_hub
```

### Клітинка 2 — завантажити файли моделі з HuggingFace (~1-2 хв)

Завантажуйте файли **по одному** через `hf_hub_download`. Це значно
надійніше за `snapshot_download` бо кожен файл має власний 30-секундний
etag-таймаут — застрягле з'єднання на одному файлі не повісить весь
ноутбук, як це робив `snapshot_download`.

```python
import os
from huggingface_hub import hf_hub_download

# Напрям input (uk → en). Поміняйте на opus-mt-tc-big-en-zle для output.
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

Побачите два warning'и про відсутність `HF_TOKEN` —
**ігноруйте їх**. Публічні моделі не потребують токена; безкоштовного
анонімного rate-limit'у достатньо для однієї моделі.

### Клітинка 3 — конвертувати в CTranslate2 int8 (~1-2 хв)

```python
!ct2-transformers-converter --model {src_path} --output_dir opus-mt-tc-big-zle-en-ct2 --quantization int8 --force
```

Конвертер виведе кілька warning'ів — **усі вони безпечні**:

| Warning | Що це значить |
|---|---|
| `torch_dtype is deprecated! Use dtype instead!` | Внутрішній deprecation transformers, на результат не впливає |
| `tied weights mapping and config for this model specifies to tie model.shared.weight…` | Особливість Helsinki opus-mt, вихід ідентичний |
| `Recommended: pip install sacremoses` | Потрібен лише для tokenizer'а transformers; ми використовуємо sentencepiece напряму, тому не потрібен |
| `Loading weights: 100% 258/258` | Прогрес-бар реальної конвертації — це добре |

Результат: папка `opus-mt-tc-big-zle-en-ct2/` з `model.bin`,
`config.json` та `shared_vocabulary.json`. **`.spm` файлів ще немає
— це наступний крок.**

### Клітинка 4 — скопіювати sentencepiece токенайзери (КРИТИЧНО)

`ct2-transformers-converter` **НЕ** копіює `source.spm` /
`target.spm` у вихідну папку. Без них runtime не може ні
токенізувати вхід, ні детокенізувати вихід, і модель непридатна.
Клітинка 2 вже принесла їх у `src_path`, скопіюйте їх:

```python
import shutil, os
shutil.copy(os.path.join(src_path, "source.spm"), "opus-mt-tc-big-zle-en-ct2/")
shutil.copy(os.path.join(src_path, "target.spm"), "opus-mt-tc-big-zle-en-ct2/")
!ls -la opus-mt-tc-big-zle-en-ct2/
```

Вивід `ls` **повинен** показати всі п'ять файлів:

```
config.json
model.bin
shared_vocabulary.json   (або shared_vocabulary.txt)
source.spm
target.spm
```

Якщо чогось бракує — зупиніться і перезапустіть відповідний крок.
Upload-маршрут SelenaCore валідовує ті самі п'ять файлів і
відхилить архів з чітким повідомленням про помилку — але швидше
помітити це тут.

### Клітинка 5 — запакувати в .tar.gz (~5 сек)

```python
!tar -czvf opus-mt-tc-big-zle-en-ct2.tar.gz opus-mt-tc-big-zle-en-ct2/
!ls -lh opus-mt-tc-big-zle-en-ct2.tar.gz
```

Результат: `opus-mt-tc-big-zle-en-ct2.tar.gz` (~240 МБ int8 — tc-big
значно більший за legacy `opus-mt-uk-en` (~80 МБ), але приріст
якості того вартий) у файловій панелі Colab зліва. Правий клік →
**Download** щоб зберегти на свій комп'ютер.

### Повторити для зворотного напряму

Тепер повторіть **Клітинки 2-5** для напряму output. Зверніть увагу:
це **інша модель** (`opus-mt-tc-big-en-zle`), а не та сама модель
з протилежними кодами. Helsinki публікує їх як окремі multi-target
/ multi-source пари.

```python
# Клітинка 2 (output)
REPO = "Helsinki-NLP/opus-mt-tc-big-en-zle"
# … решта Клітинки 2 без змін
```

```python
# Клітинка 3 (output)
!ct2-transformers-converter --model {src_path} --output_dir opus-mt-tc-big-en-zle-ct2 --quantization int8 --force
```

```python
# Клітинка 4 (output)
shutil.copy(os.path.join(src_path, "source.spm"), "opus-mt-tc-big-en-zle-ct2/")
shutil.copy(os.path.join(src_path, "target.spm"), "opus-mt-tc-big-en-zle-ct2/")
!ls -la opus-mt-tc-big-en-zle-ct2/
```

```python
# Клітинка 5 (output)
!tar -czvf opus-mt-tc-big-en-zle-ct2.tar.gz opus-mt-tc-big-en-zle-ct2/
```

Завантажте другий `.tar.gz` з панелі файлів. Тепер у вас є обидва
архіви, готові для завантаження в SelenaCore.

### Troubleshooting: завантаження зависло на X% більше 2 хвилин

Якщо Клітинка 2 зависла на якомусь відсотку і не рухається — це майже
завжди rate-limit / обрив з'єднання з HuggingFace. **Зупиніть
клітинку** (⏹) і використайте резервний шлях з прямим `wget`, який
має агресивний resume і нуль HF-бібліотек:

```python
REPO = "Helsinki-NLP/opus-mt-tc-big-zle-en"   # або opus-mt-tc-big-en-zle для output
SLUG = "tc-big-zle-en"                          # назва локальної папки

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

`wget -c` автоматично відновлює часткові завантаження і показує
реальний прогрес-бар. Після завершення — переходьте до **Клітинки 3**
як зазвичай.

### Troubleshooting: номер клітинки залишається `[ ]` (без галочки і без спінера)

Безкоштовний Colab runtime від'єднався. **Runtime → Reconnect**, потім
перезапустіть з Клітинки 1 (модель кешується на диску `hf_hub_download`'ом
з Клітинки 2, тому при reconnect Клітинки 2 і далі йдуть швидко).

## Крок 2: Обов'язковий розклад файлів (прочитайте двічі)

Після розпакування **кожна** папка з моделлю ПОВИННА містити такі
файли:

```
opus-mt-tc-big-zle-en-ct2/
├── model.bin               # ваги CTranslate2 (~240 МБ int8 для tc-big)
├── config.json             # конфіг CTranslate2 (виставляє add_source_eos=false)
├── shared_vocabulary.json  # повний vocab включно зі спец-токенами як >>ukr<<
├── source.spm              # ← ОБОВ'ЯЗКОВО, скопійовано вручну вище
└── target.spm              # ← ОБОВ'ЯЗКОВО, скопійовано вручну вище
```

`shared_vocabulary.json` критичний для multi-target tc-big моделей —
саме там живуть спец-токени як `>>ukr<<` (id 30040 у моделі en-zle).
Їх НЕМАЄ у sentencepiece vocab; це Marian/HuggingFace концепція
поверх. Wrapper `HelsinkiOutputTranslator` додає правильний токен
як окремий piece перед передачею у CTranslate2, який знаходить його
у `shared_vocabulary.json` під час перекладу.

`config.json` для tc-big моделей має `"add_source_eos": false`. Це
каже CTranslate2 НЕ додавати `</s>` до source tokens автоматично —
викликач (ми) це робить. Wrapper також це обробляє. Якщо ви робите
свій runtime, не забудьте вручну додати `</s>`, інакше отримаєте
багатореченеві портянки на кшталт
`"weather weather .. what weather outdoor..."`.

Якщо `source.spm` або `target.spm` відсутній, `_load()` кидає
`FileNotFoundError` і engine логує:

```
WARN  Helsinki: model missing for uk-en (...) — falling back to pass-through.
      Drop the converted CT2 folder under /var/lib/selena/models/translate/helsinki/in
```

Голосовий пайплайн продовжує працювати — він просто перестає
перекладати, точно як коли engine вимкнено.

## Крок 3: Встановлення — три шляхи

### Шлях A: завантажити через адмін-UI (рекомендовано для кінцевих користувачів)

Це шлях для не-програмістів — без SSH, без SCP, без шеллу.

1. Відкрийте адмінку SelenaCore → **Settings → Voice → Translation**
2. Знайдіть рядок `Ukrainian` з бейджем **Helsinki** (поряд з
   існуючим рядком Argos)
3. Під ним з'являться два поля вибору файлу:
   - `uk → en:` → клік і виберіть `opus-mt-tc-big-zle-en-ct2.tar.gz`
   - `en → uk:` → клік і виберіть `opus-mt-tc-big-en-zle-ct2.tar.gz`
4. Кожне завантаження стримиться на сервер, розпаковується у потрібну
   папку і валідовує наявність усіх п'яти файлів (`model.bin`,
   `config.json`, `shared_vocabulary.json`, `source.spm`,
   `target.spm`). Якщо чогось бракує — toast покаже точну помилку.
5. Після обох завантажень рядок переключиться на зелені бейджі
   `uk→en` / `en→uk` і кнопку **Activate**.
6. Клік на **Activate** → `translation.engine` стає `helsinki`,
   `translation.active_lang` стає `uk`, обидва translator engine'и
   перевантажуються → готово.

Маршрут upload — `POST /api/ui/setup/translate/upload` (multipart:
`engine`, `lang`, `direction`, `file`). Він приймає лише Helsinki
engine — Argos-пакети, як і раніше, керуються через стандартний
маршрут `/translate/download`.

### Шлях B: покласти на диск вручну (доступ через SSH)

Якщо у вас є шелл-доступ до пристрою, цей шлях обходить upload:

```bash
tar -xzf opus-mt-tc-big-zle-en-ct2.tar.gz
sudo mkdir -p /var/lib/selena/models/translate/helsinki/in
sudo mv opus-mt-tc-big-zle-en-ct2 /var/lib/selena/models/translate/helsinki/in/uk-en

tar -xzf opus-mt-tc-big-en-zle-ct2.tar.gz
sudo mkdir -p /var/lib/selena/models/translate/helsinki/out
sudo mv opus-mt-tc-big-en-zle-ct2 /var/lib/selena/models/translate/helsinki/out/en-uk
```

Зверніть увагу на перейменування: підпапка стає `<src>-<tgt>` —
**мітка мовної пари**, а не назва моделі. Downloader сканує наявність
`model.bin` + `source.spm` + `target.spm`, тому технічно будь-яка
назва спрацює, але `uk-en` / `en-uk` — це те, з чим рядок каталогу
зіставляється. Оновіть сторінку адмінки — Helsinki-рядок з'явиться
як встановлений.

### Шлях C: GitHub release (дзеркало для інших користувачів)

Якщо ви хочете щоб інші користувачі SelenaCore встановлювали ваші
сконвертовані моделі через кнопку **Install** (а не завантажували
свої власні `.tar.gz`), опублікуйте їх як release-asset'и:

1. Створіть release на `dotradepro/SelenaCore` з тегом `translators-v1`.
2. Завантажте обидва `.tar.gz` як release-asset'и. Каталог вже
   очікує імена `opus-mt-tc-big-zle-en-ct2-int8.tar.gz` і
   `opus-mt-tc-big-en-zle-ct2-int8.tar.gz` — залиште їх.
3. Порахуйте sha256: `sha256sum opus-mt-tc-big-*-ct2-int8.tar.gz`.
4. Відредагуйте
   [`core/translation/helsinki_catalog.py`](../../core/translation/helsinki_catalog.py)
   і впишіть `input_sha256` / `output_sha256` у відповідний рядок.
5. Зробіть commit + push. Тепер інші користувачі можуть `POST
   /translate/download {"id": "helsinki-uk-en"}` через UI, і
   downloader забере архіви з URL вашого release з sha256 перевіркою.

## Крок 4: Активація через UI

Helsinki-рядки з'являються у тому самому каталозі, що й Argos-рядки —
**Settings → Translation**. ID рядка — `helsinki-uk-en`. Клік на
**Activate** записує:

```yaml
translation:
  engine: helsinki
  active_lang: uk
  enabled: true
```

…і перевантажує обидва engine'и (singleton'и Argos і Helsinki
скидаються, щоб наступний запит завантажив правильний з диску).

`GET /api/ui/setup/translate/status` після цього повертає:

```json
{
  "enabled": true,
  "engine": "helsinki",
  "active_lang": "uk",
  "input_available": true,
  "output_available": true
}
```

Щоб повернутися на Argos — клікніть **Activate** на Argos-рядку тієї
ж мови. Ключ `engine` автоматично переключиться на `argos`.

## Крок 5: Перевірка

```bash
docker compose exec -T core python3 \
  /opt/selena-core/tests/benchmark/run_trace_bench.py --model qwen2.5:1.5b
```

Подивіться на `STEP 2. InputTranslator (Argos)` (тепер це фактично
Helsinki — лейбл захардкоджений у бенчі, ігноруйте) для кейсів 15,
17, 22. Перекладений англійський текст має збігатися з правою
колонкою таблиці на початку документа.

## Додавання нової мовної пари

1. Додайте пару у `PAIRS` у Colab-сніпеті, перезапустіть.
2. Покладіть нові папки під `helsinki/in/<src>-en/` і
   `helsinki/out/en-<src>/`.
3. Додайте рядок у `HELSINKI_CATALOG` у
   [`core/translation/helsinki_catalog.py`](../../core/translation/helsinki_catalog.py).
4. Перезапустіть контейнер; рядок з'явиться у каталозі UI.

## Чому без PyTorch у продакшені

Runtime — це `ctranslate2` (C++ inference engine) + `sentencepiece`
(C++ tokenizer). Обидва приходять як транзитивні залежності
`argostranslate>=1.9.0`, який вже у `requirements.txt`. **Жодних
нових pip-пакетів.** PyTorch потрібен лише для конвертації, яка
відбувається одноразово в Colab і ніколи на пристрої.

## Дивись також

- [`core/translation/helsinki_translator.py`](../../core/translation/helsinki_translator.py)
  — runtime wrapper
- [`core/translation/helsinki_downloader.py`](../../core/translation/helsinki_downloader.py)
  — install / activate / delete
- [`core/translation/helsinki_catalog.py`](../../core/translation/helsinki_catalog.py)
  — описи мовних пар
- [`docs/uk/translation.md`](translation.md) — загальний пайплайн
  перекладу
- [`docs/uk/intent-routing.md`](intent-routing.md) — як перекладач
  годує LLM
