"""Тулзы агента на LangChain.

Каждая функция обёрнута в @tool: LangChain сам строит из сигнатуры и docstring
JSON-схему аргументов, которую видит модель. Поэтому:
  * типы аргументов обязательны;
  * docstring — это фактически промпт для модели, пишем его по-человечески.

Все тулзы возвращают СТРОКУ (JSON или текст) и никогда не бросают исключение:
агент должен получить Observation, а не упасть.

Реестр: ALL_TOOLS, get_tools(), TOOL_NAMES.
"""

import json

from langchain_core.tools import tool
from pydantic import ValidationError

from llm.generator import generate_spec
from pipeline.resolve_images import resolve_images
from pptx_engine import save as save_presentation
from pptx_engine.spec import PresentationSpec
from tools.image_quality import check_technical, score_candidates
from tools.image_resolver import find_best_image, validate_image_file
from tools.image_search import commons_search, download_candidate, to_image_meta
from tools.image_types import ImageCandidate, ImageIntent
from tools.web_page import fetch_web_page
from tools.web_search import web_search as _web_search
from tools.wiki import wiki_page as _wiki_page
from tools.wiki import wiki_search as _wiki_search

# Кандидаты, найденные search_images: pageid → кандидат.
# Нужен, чтобы download_image не искал всё заново (модель передаёт только id).
_CANDIDATES: dict[int, ImageCandidate] = {}


# ---------------------------------------------------------------------------
# мелкие помощники
# ---------------------------------------------------------------------------

def _json(payload) -> str:
    """Любой объект → компактная JSON-строка для Observation."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _intent(query: str, slide_title: str = "", bullets: str = "") -> ImageIntent:
    """Собрать интент из аргументов тулзы (bullets приходят одной строкой)."""
    return ImageIntent(
        query=(query or "").strip(),
        slide_title=slide_title or "",
        bullets=[b.strip() for b in (bullets or "").split(";") if b.strip()],
    )


def _remember(candidates: list[ImageCandidate]) -> None:
    for candidate in candidates:
        _CANDIDATES[candidate.pageid] = candidate


# ---------------------------------------------------------------------------
# 1. текстовый поиск
# ---------------------------------------------------------------------------

@tool
def web_search(query: str, limit: int = 5) -> str:
    """Найти информацию в интернете (свежие факты, статьи, документация).

    Args:
        query: поисковый запрос; лучше на языке источника (обычно английский).
        limit: сколько результатов вернуть (1-10).
    """
    results = _web_search(query, limit=max(1, min(limit, 10)))
    if not results:
        return _json({"found": 0, "note": "ничего не найдено, попробуй другой запрос"})
    return _json({"found": len(results), "results": results})


@tool
def wiki_search(query: str, limit: int = 5) -> str:
    """Найти статьи Википедии по теме (русская или английская — определится сама).

    Args:
        query: тема или термин для поиска.
        limit: сколько статей вернуть (1-10).
    """
    hits = _wiki_search(query, limit=max(1, min(limit, 10)))
    if not hits:
        return _json({"found": 0, "note": "статей не нашлось"})
    return _json({"found": len(hits), "articles": hits})


@tool
def wiki_page(title: str, lang: str = "") -> str:
    """Прочитать статью Википедии целиком (текст + разбивка на разделы).

    Используй, когда нужны факты для слайда: статья даёт проверяемый текст
    и ссылку, которую можно поставить в слайд «Источники».

    Args:
        title: точное название статьи (как в выдаче wiki_search).
        lang: "ru" или "en"; пустая строка — определить автоматически.
    """
    page = _wiki_page(title, lang=lang or None)
    if not page:
        return _json({"found": 0, "note": f"статья {title!r} не найдена"})
    # отдаём не весь текст, а summary + заголовки разделов: агенту этого хватает,
    # а контекст не раздувается
    return _json({
        "found": 1,
        "title": page["title"],
        "url": page["url"],
        "summary": page["summary"],
        "sections": [s["heading"] for s in page["sections"] if s["heading"]],
        "text": page["text"][:4000],
    })


@tool
def fetch_page(url: str) -> str:
    """Скачать веб-страницу и вернуть её читаемый текст.

    Args:
        url: полный адрес страницы (http/https).
    """
    page = fetch_web_page(url)
    if not page:
        return _json({"ok": False, "note": "страницу не удалось прочитать (PDF, JS или недоступна)"})
    return _json({"ok": True, "title": page["title"], "url": page["url"],
                  "text": page["text"][:4000]})


# ---------------------------------------------------------------------------
# 2. картинки: поиск → скачивание → проверка
# ---------------------------------------------------------------------------

@tool
def search_images(query: str, slide_title: str = "", bullets: str = "",
                  max_candidates: int = 6) -> str:
    """Найти картинки в Wikimedia Commons и оценить их качество БЕЗ скачивания.

    Оценка идёт по описанию файла и категориям: релевантность запросу слайда
    (эмбеддинги + LLM-судья), свободная ли лицензия, знак качества Commons,
    разрешение. Скачивай только лучшего кандидата через download_image.

    Args:
        query: что изобразить, короткий английский запрос ("solar system planets").
        slide_title: заголовок слайда — помогает судье понять контекст.
        bullets: тезисы слайда через ";" (необязательно).
        max_candidates: сколько кандидатов проверить (3-10).
    """
    intent = _intent(query, slide_title, bullets)
    candidates = commons_search(intent.query, limit=max(3, min(max_candidates, 10)),
                                min_width=400, min_height=300)
    if not candidates:
        return _json({"found": 0, "note": "в Commons ничего не нашлось, измени запрос"})

    scored = score_candidates(intent, candidates, llm_top_k=2)
    _remember([item.candidate for item in scored])

    return _json({
        "found": len(scored),
        "candidates": [
            {
                "pageid": item.candidate.pageid,
                "title": item.candidate.title,
                "description": item.candidate.description[:200],
                "size": f"{item.candidate.width}x{item.candidate.height}",
                "license": item.candidate.license_short,
                "license_ok": item.candidate.license_ok,
                "commons_quality": item.candidate.commons_quality or None,
                "relevance": item.relevance.score,
                "total": item.total,
                "why": item.relevance.reason[:200],
                "problems": item.issues,
                "page_url": item.candidate.page_url,
            }
            for item in scored
        ],
    })


@tool
def download_image(pageid: int, query: str = "") -> str:
    """Скачать кандидата из search_images и проверить файл технически.

    Args:
        pageid: id кандидата из результата search_images.
        query: исходный запрос (нужен только для имени файла).
    """
    candidate = _CANDIDATES.get(int(pageid))
    if candidate is None:
        return _json({"ok": False, "note": "неизвестный pageid: сначала вызови search_images"})
    if not candidate.license_ok:
        return _json({"ok": False, "note": f"лицензия не подходит: {candidate.license_short}"})

    path = download_candidate(candidate, out_dir="assets/cache", name_hint=query or candidate.title)
    if path is None:
        return _json({"ok": False, "note": "файл не скачался"})

    technical = check_technical(path)
    return _json({
        "ok": technical.ok,
        "path": path,
        "technical": technical.as_text(),
        "problems": technical.issues,
        "meta": to_image_meta(candidate, score=technical.score).model_dump(),
    })


@tool
def validate_image(path: str) -> str:
    """Проверить уже скачанную картинку: разрешение, резкость, контраст, битость.

    Args:
        path: путь к файлу внутри проекта ("assets/cache/xxx.png").
    """
    return _json(validate_image_file(path))


@tool
def resolve_slide_image(query: str, slide_title: str = "", bullets: str = "") -> str:
    """Один вызов = готовая картинка для слайда (поиск + оценка + скачивание + проверка).

    Используй, когда не нужно перебирать варианты вручную.

    Args:
        query: короткий английский запрос картинки.
        slide_title: заголовок слайда.
        bullets: тезисы слайда через ";" (необязательно).
    """
    pick = find_best_image(_intent(query, slide_title, bullets))
    if pick is None:
        return _json({"found": False, "note": "подходящей картинки нет — сделай слайд текстовым"})

    return _json({
        "found": True,
        "path": pick.path,
        "score": pick.score.total,
        "relevance": pick.score.relevance.score,
        "why": pick.score.relevance.reason[:250],
        "technical": pick.score.technical.as_text() if pick.score.technical else "",
        "meta": pick.meta.model_dump(),
    })


# ---------------------------------------------------------------------------
# 3. сборка самой презентации
# ---------------------------------------------------------------------------

DECK_JSON_EXAMPLE = """{
  "title": "Заголовок презентации",
  "author": "",
  "theme": "default",
  "slides": [
    {"layout": "title", "title": "Титул", "subtitle": "Подзаголовок"},
    {"layout": "text", "title": "Слайд", "bullets": ["тезис 1", "тезис 2"]},
    {"layout": "text_image", "title": "Слайд с картинкой",
     "bullets": ["тезис"], "image_query": "solar system planets"}
  ]
}
Допустимые layout: title, text, image, text_image, image_text, two_columns, references.
Картинки появятся там, где указан image_query (layout: image / text_image / image_text)."""


@tool
def create_presentation(topic: str, slide_count: int = 8,
                        out_path: str = "out_agent.pptx",
                        with_images: bool = True) -> str:
    """Сделать готовый файл презентации .pptx по теме (план → картинки → файл).

    Это финальный шаг, когда пользователь просит именно презентацию, а не справку.

    Args:
        topic: тема презентации, можно с уточнениями ("Космос: Солнечная система, для школьников").
        slide_count: сколько слайдов сделать (3-20).
        out_path: имя файла, например "solar_system.pptx".
        with_images: искать и вставлять картинки (по умолчанию да).
    """
    try:
        request = f"{topic}. Слайдов: {max(3, min(slide_count, 20))}."
        spec = generate_spec(request)
        if with_images:
            spec = resolve_images(spec)
        path = save_presentation(spec, out_path)
    except Exception as exc:                      # noqa: BLE001 — наружу отдаём текстом
        return _json({"ok": False, "note": f"не получилось собрать файл: {type(exc).__name__}: {exc}"})

    return _json(_deck_report(spec, path))


# Описание render_deck собираем из строк: внутрь докстринга пример схемы не влезает
# (докстринг обязан быть одним литералом), а модели нужен именно пример.
RENDER_DECK_DESCRIPTION = (
    "Собрать .pptx из ГОТОВОЙ JSON-спецификации (когда слайды сочинил сам агент).\n\n"
    "Используй, если уже собрал тезисы из источников и хочешь положить их в файл.\n"
    "Формат JSON:\n" + DECK_JSON_EXAMPLE + "\n\n"
    "Args:\n"
    "    spec_json: JSON-строка со спецификацией презентации.\n"
    "    out_path: имя файла .pptx.\n"
    "    with_images: искать картинки для слайдов с image_query."
)


@tool(description=RENDER_DECK_DESCRIPTION)
def render_deck(spec_json: str, out_path: str = "out_agent.pptx",
                with_images: bool = True) -> str:
    if not (spec_json or "").strip():
        return _json({"ok": False, "note": "пустой spec_json", "example": DECK_JSON_EXAMPLE})

    try:
        data = json.loads(spec_json)
    except json.JSONDecodeError as exc:
        return _json({"ok": False,
                      "note": f"это не валидный JSON: {exc}",
                      "example": DECK_JSON_EXAMPLE})

    try:
        spec = PresentationSpec.model_validate(data)
    except ValidationError as exc:
        # первые 3 ошибки: модели хватает, чтобы понять, что чинить
        problems = [
            f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:3]
        ]
        return _json({"ok": False, "note": "спецификация не прошла валидацию",
                      "problems": problems, "example": DECK_JSON_EXAMPLE})

    try:
        if with_images:
            spec = resolve_images(spec)
        path = save_presentation(spec, out_path)
    except Exception as exc:                      # noqa: BLE001
        return _json({"ok": False, "note": f"не получилось собрать файл: {type(exc).__name__}: {exc}"})

    return _json(_deck_report(spec, path))


def _deck_report(spec: PresentationSpec, path: str) -> dict:
    """Короткий отчёт о собранной презентации — его агент пересказывает пользователю."""
    return {
        "ok": True,
        "path": path,
        "title": spec.title,
        "slides": [
            {
                "layout": slide.layout,
                "title": slide.title,
                "image": slide.image,
                "license": slide.image_meta.license if slide.image_meta else None,
            }
            for slide in spec.slides
        ],
        "slides_total": len(spec.slides),
        "images_total": sum(1 for slide in spec.slides if slide.image),
    }


# ---------------------------------------------------------------------------
# реестр
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    web_search,
    wiki_search,
    wiki_page,
    fetch_page,
    search_images,
    download_image,
    validate_image,
    resolve_slide_image,
    create_presentation,
    render_deck,
]

TOOL_NAMES = [t.name for t in ALL_TOOLS]


def get_tools(names: list[str] | None = None) -> list:
    """Все тулзы или только выбранные по именам (удобно для узких агентов)."""
    if not names:
        return list(ALL_TOOLS)
    wanted = set(names)
    return [t for t in ALL_TOOLS if t.name in wanted]


def get_tool(name: str):
    """Одна тулза по имени (нужно текстовому ReAct-циклу)."""
    for t in ALL_TOOLS:
        if t.name == name:
            return t
    return None
