# pptx-agent

Локальный агент, который собирает презентации PowerPoint: ищет текстовую информацию,
подбирает картинки в Wikimedia Commons, проверяет их качество и рендерит `.pptx`.

Всё работает на локальной модели в **LM Studio** (`http://127.0.0.1:1234/v1`) — без облачных ключей.

---

## Что добавлено в этой итерации

| Слой | Файл | Что делает |
|---|---|---|
| HTTP | `tools/_http.py` | один `requests.Session`, троттлинг, ретраи, `get_json` / `post_json` / `get_text` / `download` с лимитом размера |
| Текст | `tools/web_search.py` | веб-поиск: Tavily (если есть ключ) → ddgs → Википедия |
| Текст | `tools/wiki.py` | поиск статей, чтение статьи целиком (разделы + ссылка), авто-определение языка ru/en |
| Текст | `tools/web_page.py` | читаемый текст произвольной страницы (без bs4, на stdlib) |
| Текст | `tools/text_utils.py` | HTML→text, сниппеты, лёгкий стемминг, лексическое пересечение |
| Картинки | `tools/image_search.py` | поиск в Commons: описание, категории, автор, лицензия, размер, «знак качества» |
| Картинки | `tools/image_quality.py` | валидация: техника (Pillow) + релевантность по описанию (эмбеддинги + LLM-судья) |
| Картинки | `tools/image_resolver.py` | конвейер: поиск → реранк → скачивание → проверка → победитель или `None` |
| Агент | `tools/langchain_tools.py` | 10 тулз на `@tool` из LangChain, включая сборку `.pptx` |
| Агент | `agent/react.py` | ReAct-цикл: нативный (`create_agent`) + текстовый fallback |
| Пайплайн | `pipeline/resolve_images.py` | подстановка проверенной картинки в слайд, деградация до текста |

---

## Как получается .pptx

Есть два пути, оба заканчиваются файлом на диске:

```bash
# 1. Агентный: агент исследует тему и сам вызывает create_presentation
.venv/bin/python scripts/agent_demo.py --out mars.pptx "Сделай презентацию про Марс, 5 слайдов"

# 2. Детерминированный: без агента, сразу тема → файл
.venv/bin/python scripts/make_deck.py "Космос: Солнечная система, 6 слайдов" --out space.pptx
```

Важно: если попросить агента просто «собери материал», он вернёт **текст** (тезисы, источники,
пути к картинкам) — файла не будет. Чтобы получить `.pptx`, в задаче должно быть слово
«презентация» (тогда системный промпт обязывает вызвать `create_presentation`/`render_deck`)
или нужен флаг `--out`.

---

## Быстрый старт

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

В LM Studio должны быть загружены:

* chat-модель с поддержкой tools — по умолчанию `google/gemma-4-e4b`;
* эмбеддинги — `text-embedding-nomic-embed-text-v1.5` (для оценки картинок).

```bash
# все тулзы вживую (сеть + LM Studio)
.venv/bin/python scripts/smoke_tools.py

# агент
.venv/bin/python scripts/agent_demo.py "Собери материал и картинку про Солнечную систему"
.venv/bin/python scripts/agent_demo.py --out mars.pptx "Сделай презентацию про Марс, 5 слайдов"
.venv/bin/python scripts/agent_demo.py --mode text --model google/gemma-3-1b "Что такое блокчейн?"
.venv/bin/python scripts/agent_demo.py --list-models

# готовая презентация без агента
.venv/bin/python scripts/make_deck.py "Квантовые вычисления, 7 слайдов" --out quantum.pptx

# тесты (офлайн, без сети и модели)
.venv/bin/python -m pytest tests -q
```

Разбор по слоям с примерами — в ноутбуке [`notebooks/tools_and_agent.ipynb`](notebooks/tools_and_agent.ipynb).

---

## Тулзы

Все тулзы возвращают **строку** (JSON или текст) и никогда не бросают исключение: агент
должен получить Observation, а не упасть. Реестр — `ALL_TOOLS` в `tools/langchain_tools.py`.

| Тулза | Аргументы | Что возвращает |
|---|---|---|
| `web_search` | `query`, `limit` | список результатов: title, url, snippet, source |
| `wiki_search` | `query`, `limit` | статьи Википедии со сниппетами и ссылками |
| `wiki_page` | `title`, `lang` | summary, список разделов, до 4000 символов текста, url |
| `fetch_page` | `url` | title + читаемый текст страницы |
| `search_images` | `query`, `slide_title`, `bullets`, `max_candidates` | кандидаты с оценками (релевантность, лицензия, качество, размер) — **без скачивания** |
| `download_image` | `pageid`, `query` | путь к файлу + техническая проверка + метаданные |
| `validate_image` | `path` | разрешение, резкость, контраст, энтропия, проблемы |
| `resolve_slide_image` | `query`, `slide_title`, `bullets` | готовый путь к картинке «в один вызов» + объяснение выбора |
| `create_presentation` | `topic`, `slide_count`, `out_path`, `with_images` | **готовый файл `.pptx`** + отчёт по слайдам |
| `render_deck` | `spec_json`, `out_path`, `with_images` | **готовый файл `.pptx`** из JSON-спецификации, которую сочинил сам агент |

Своя тулза добавляется в три строки:

```python
from langchain_core.tools import tool

@tool
def my_tool(query: str, limit: int = 5) -> str:
    """Одно предложение для модели: что делает тулза.

    Args:
        query: что искать.
        limit: сколько результатов вернуть.
    """
    return '{"found": 0}'
```

…и в список `ALL_TOOLS`. Docstring — это фактически промпт для модели, поэтому пишите его осмысленно.

---

## Как валидируются картинки

Идея: у файлов в Commons почти всегда есть **человеческое описание, категории и осмысленное имя**,
поэтому качество можно оценивать текстом — до скачивания и без vision-модели.

```
запрос слайда
   │
   ├─ 1. commons_search      → до 8 кандидатов: описание, категории, лицензия, размер
   │
   ├─ 2. релевантность       → эмбеддинги (косинус описания и интента слайда)
   │                           + LLM-судья для топ-2 (смесь 50/50)
   │
   ├─ 3. жёсткие фильтры     → лицензия, разрешение, соотношение сторон
   │
   ├─ 4. скачивание          → только лучшие 1-4 кандидата (не весь список)
   │
   └─ 5. проверка файла      → Pillow: разрешение, резкость (дисперсия лапласиана),
                               экспозиция, контраст, энтропия
```

Итоговый балл (0-10): `0.50 × релевантность + 0.30 × техника + 0.15 × знак качества Commons + 0.05 × лицензия`.

Жёсткие отбраковки: разрешение < 800×450, соотношение сторон > 3.2 или < 0.35, однотонная
картинка (контраст < 8 и энтропия < 1), «выбеленная» или почти чёрная заглушка.
Пороги собраны в начале `tools/image_quality.py` — их легко крутить.

Если ничего не прошло — `find_best_image` вернёт `None`, и слайд станет текстовым:
лучше текст, чем мусорная картинка.

### Калибровка (на этом железе)

* косинус nomic-embed-text: ~0.45 — «мимо», ~0.62-0.72 — «в точку» → отображение в 0-10;
* дисперсия лапласиана: ~30 — мыло, ~300 — нормальный скриншот, 1000+ — чёткая схема/фото;
* скорость: ~6-9 c на кандидатов (эмбеддинги) + ~7 c на каждого LLM-судью.

---

## ReAct-агент

`agent/react.py` поддерживает два режима, потому что локальные модели очень разные:

* **native** — модель умеет function calling (gemma-4-e4b умеет): граф собирается через
  `langchain.agents.create_agent`, модель сама возвращает `tool_calls`;
* **text** — модель function calling не умеет (например, gemma-3-1b): ведём классический цикл
  `Thought → Action → Action Input → Observation → … → Final Answer`, а схема аргументов
  уходит в промпт текстом. Тулзы те же самые.

```python
from agent.react import run

result = run("Собери материал и картинку про Марс", mode="auto")   # auto: native → text
print(result.answer)
for step in result.steps:          # reasoning / thought / tool_call / observation / final
    print(step.kind, step.tool, step.text[:80])
```

`auto` сначала пробует нативный режим и, если он упал или не дал ответа, повторяет текстовым.

---

## Переменные окружения

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `PPTX_LLM_MODEL` | `google/gemma-4-e4b` | модель агента и LLM-судьи |
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` | адрес LM Studio |
| `PPTX_EMBED_MODEL` | `text-embedding-nomic-embed-text-v1.5` | модель эмбеддингов |
| `PPTX_IMAGE_LLM_JUDGE` | `1` | `0` — выключить LLM-судью (быстрее, только эмбеддинги) |
| `TAVILY_API_KEY` | — | если задан, веб-поиск идёт через Tavily |

---

## Структура

```
agent/                  # модели и ReAct-цикл
  llm.py                #   ChatOpenAI → LM Studio
  react.py              #   нативный и текстовый ReAct
tools/                  # все тулзы и утилиты
  langchain_tools.py    #   @tool-обёртки (реестр ALL_TOOLS)
  image_*.py            #   поиск, типы, валидация, резолвер
  web_search.py wiki.py web_page.py text_utils.py _http.py
pptx_engine/            # пакет рендера: spec.py (модель), theme, layouts, engine
llm/                    # генерация спецификации презентации (JSON)
pipeline/               # resolve_images: интент слайда → картинка
scripts/                # smoke_tools.py, agent_demo.py
tests/                  # офлайн-тесты (pytest)
```

### Одна спецификация на весь проект

Модель презентации (`ImageMeta`, `SlideSpec`, `PresentationSpec`) живёт **только** в
[`pptx_engine/spec.py`](pptx_engine/spec.py), а [`llm/schema.py`](llm/schema.py) — это реэкспорт
тех же объектов (`llm.schema.SlideSpec is pptx_engine.spec.SlideSpec`).

Так было не всегда: файл существовал в двух копиях, и они разъехались — `llm.schema.ImageMeta`
нельзя было передать в `pptx_engine.SlideSpec` (pydantic: «Input is not a valid ImageMeta»),
а поля `description`/`page_url`/`score` при конвертации через `dict` молча пропадали.
Причина копипаста — папка `pptx-engine` с дефисом: её нельзя импортировать как пакет.

Поэтому конвертер `to_engine()` больше не нужен — спецификацию из LLM-слоя можно отдавать
в рендер напрямую:

```python
from pipeline.resolve_images import resolve_images
from llm.generator import generate_spec
from pptx_engine import save

save(resolve_images(generate_spec("Космос: Солнечная система, 6 слайдов")), "deck.pptx")
```

Страж от повторного расхождения — [`tests/test_single_spec.py`](tests/test_single_spec.py).

### Полезные мелочи, о которые легко споткнуться

* **User-Agent.** CDN Wikimedia отдаёт 403, если в UA есть email или не-браузерный вид.
  В `tools/_http.py` стоит рабочий UA — не добавляйте туда контакты.
* **Поиск в Commons ищет по И.** Длинный запрос часто не находит ничего, поэтому в
  `commons_search` есть «лестница»: полный запрос с `filetype:bitmap|drawing` → укороченный
  до 3 и 2 значимых слов → полный запрос без фильтра с отсевом PDF по mime.
* **LM Studio и `response_format`.** Он принимает только `text` и `json_schema`
  (`json_object` даёт 400) — учтено в `llm/client.py`.
* **Reasoning-модели.** gemma-4-e4b тратит часть `max_tokens` на «размышления», поэтому
  лимиты в клиенте/агенте завышены, а `reasoning_content` попадает в трейс шагов.
* **Лэйауты и картинки.** Рендереры `title`, `two_columns` и `references` картинку не рисуют,
  поэтому `resolve_images` их и не резолвит (иначе качали бы файлы в никуда). Слайд `text`
  с `image_query` автоматически повышается до `text_image`, а слайд `text_image` без
  найденной картинки деградирует до `text`.

