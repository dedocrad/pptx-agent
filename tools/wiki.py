"""Текстовый поиск и чтение страниц Википедии (MediaWiki Action API).

Почему Википедия, а не «весь веб»:
  * бесплатно, без ключей и почти без лимитов;
  * текст структурированный (разделы), его удобно резать на тезисы для слайдов;
  * ссылка на статью сразу годится в слайд «Источники».

Публичные функции:
    wiki_search(query, lang=None, limit=5)  -> список кандидатов со сниппетами
    wiki_page(title, lang=None, max_chars)  -> полный текст статьи + ссылка
"""

import re

from tools._http import get_json
from tools.text_utils import clean_snippet, truncate

SEARCH_LIMIT = 5
MAX_CHARS = 8000


def detect_lang(text: str) -> str:
    """ru, если в тексте заметная доля кириллицы, иначе en."""
    if not text:
        return "en"
    cyrillic = len(re.findall(r"[а-яА-ЯёЁ]", text))
    letters = len(re.findall(r"[a-zA-Zа-яА-ЯёЁ]", text))
    if letters == 0:
        return "en"
    return "ru" if cyrillic / letters > 0.3 else "en"


def _api_url(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _langs(lang: str | None, query: str) -> list[str]:
    """Порядок языков: явный → угаданный по запросу → второй из пары."""
    primary = lang or detect_lang(query)
    secondary = "en" if primary == "ru" else "ru"
    return [primary, secondary]


def wiki_search(query: str, lang: str | None = None, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Полнотекстовый поиск статей. Возвращает список словарей (может быть пустым)."""
    query = (query or "").strip()
    if not query:
        return []

    for lang_code in _langs(lang, query):
        data = get_json(_api_url(lang_code), params={
            "action": "query",
            "format": "json",
            "list": "search",
            "srsearch": query,
            "srlimit": str(limit),
            "srprop": "snippet|wordcount|timestamp",
        })
        hits = ((data or {}).get("query") or {}).get("search") or []
        if hits:
            return [_hit_to_result(hit, lang_code) for hit in hits]
    return []


def _hit_to_result(hit: dict, lang: str) -> dict:
    title = hit.get("title", "")
    return {
        "title": title,
        "snippet": clean_snippet(hit.get("snippet", ""), limit=240),
        "pageid": hit.get("pageid"),
        "words": hit.get("wordcount", 0),
        "lang": lang,
        "url": f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
    }


def wiki_page(title: str, lang: str | None = None, max_chars: int = MAX_CHARS) -> dict | None:
    """Полный plain-text статьи (с разделами). None, если статьи нет."""
    title = (title or "").strip()
    if not title:
        return None

    for lang_code in _langs(lang, title):
        data = get_json(_api_url(lang_code), params={
            "action": "query",
            "format": "json",
            "prop": "extracts",
            "explaintext": "1",              # без вики-разметки
            "exsectionformat": "wiki",       # заголовки как "== Раздел =="
            "redirects": "1",
            "titles": title,
        })
        pages = ((data or {}).get("query") or {}).get("pages") or {}
        for page in pages.values():
            extract = page.get("extract") or ""
            if not extract.strip():
                continue
            real_title = page.get("title", title)
            text = truncate(extract, max_chars)
            return {
                "title": real_title,
                "lang": lang_code,
                "url": f"https://{lang_code}.wikipedia.org/wiki/{real_title.replace(' ', '_')}",
                "summary": _first_paragraph(extract),
                "sections": _split_sections(text),
                "text": text,
            }
    return None


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text or ""):
        block = block.strip()
        if block and not block.startswith("=="):
            return truncate(block, 700)
    return ""


def _split_sections(text: str) -> list[dict]:
    """'== Заголовок ==' → [{heading, text}, ...]; текст до первого заголовка — ''."""
    sections, current = [], {"heading": "", "text": []}
    for line in (text or "").splitlines():
        match = re.match(r"^\s*(={2,4})\s*(.+?)\s*\1\s*$", line)
        if match:
            sections.append({"heading": current["heading"],
                             "text": "\n".join(current["text"]).strip()})
            current = {"heading": match.group(2), "text": []}
        else:
            current["text"].append(line)
    sections.append({"heading": current["heading"],
                     "text": "\n".join(current["text"]).strip()})
    return [s for s in sections if s["heading"] or s["text"]]
