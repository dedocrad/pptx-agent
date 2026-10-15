# PPTX Agent

Локальный агент, который собирает презентации PowerPoint: ищет текстовую информацию,
подбирает картинки в Wikimedia Commons, проверяет их качество и рендерит `.pptx`.

Всё работает на локальной модели в **LM Studio** (`http://127.0.0.1:1234/v1`) — без облачных ключей.

---
## Как получается .pptx

Два способа:

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

В LM Studio запустите сервер по адресу `http://127.0.0.1:1234/v1` и загрузите модель с поддержкой вызова инструментов (function calling). По умолчанию используется `google/gemma-4-e4b`.

### Веб-интерфейс

```bash
.venv/bin/python -m streamlit run streamlit_app.py
```

Введите задачу в браузере. Например:

> Сделай презентацию про Марс на 5 слайдов.

Если агент создаст файл `.pptx`, его можно скачать прямо со страницы.

### Как устроен проект

Streamlit и CLI используют один LangChain-агент. Агент создаёт план слайдов, сервис изображений подбирает иллюстрации, а `pptx_engine` собирает файл.

### Командная строка

```bash
.venv/bin/python scripts/agent_demo.py "Сделай презентацию про Солнечную систему на 5 слайдов"
.venv/bin/python scripts/agent_demo.py --out mars.pptx "Сделай презентацию про Марс, 5 слайдов"
```

## Настройки

Для автоматического подбора изображений нужен доступ к интернету. Адрес LM Studio и модель можно изменить переменными окружения:

| Переменная | По умолчанию |
|---|---|
| `LM_STUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` |
| `PPTX_LLM_MODEL` | `google/gemma-4-e4b` |

## Проверка

Офлайн-тесты запускаются командой:

```bash
.venv/bin/python -m pytest tests -q
```

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

