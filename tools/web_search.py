"""Поиск текстовой информации в интернете.

Провайдеры (выбираются автоматически, первый рабочий побеждает):
  1. Tavily  — если в окружении есть TAVILY_API_KEY (самый качественный, с готовыми ответами);
  2. ddgs    — бесплатный агрегатор DuckDuckGo/Brave/Mojeek и т.п. (pip install ddgs);
  3. Wikipedia — всегда доступный запасной вариант (энциклопедический текст).

Смысл такой цепочки: локальный проект должен работать без ключей и платных API,
но если ключ появился — качество поиска сразу растёт, а код не меняется.
"""

import os

from tools.text_utils import clean_snippet
from tools.wiki import wiki_search

DEFAULT_LIMIT = 5
DDGS_TIMEOUT = 12


def web_search(query: str, limit: int = DEFAULT_LIMIT,
               backend: str | None = None) -> list[dict]:
    """Найти страницы по запросу.

    Возвращает список словарей: {title, url, snippet, source}.
    Пустой список = «ничего не нашли» (никогда не бросает исключение).
    """
    query = (query or "").strip()
    if not query:
        return []

    # порядок провайдеров; backend="tavily"/"ddgs"/"wikipedia" — принудительный выбор
    chain = [backend] if backend else ["tavily", "ddgs", "wikipedia"]

    for name in chain:
        results = _PROVIDERS[name](query, limit)
        if results:
            return results
    return []


# --------------------------------------------------------------------------
# провайдер 1: Tavily (нужен ключ)
# --------------------------------------------------------------------------

def _search_tavily(query: str, limit: int) -> list[dict]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return []

    from tools._http import post_json
    data = post_json("https://api.tavily.com/search", {
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "search_depth": "basic",
        "include_answer": False,
    }, timeout=25)
    if not data:
        return []

    return [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": clean_snippet(item.get("content", ""), limit=300),
            "source": "tavily",
        }
        for item in (data.get("results") or [])
    ]


# --------------------------------------------------------------------------
# провайдер 2: ddgs (бесплатно, без ключа)
# --------------------------------------------------------------------------

def _search_ddgs(query: str, limit: int) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        return []                      # библиотека не стоит — просто идём дальше по цепочке

    try:
        raw = DDGS(timeout=DDGS_TIMEOUT).text(query, max_results=limit)
    except Exception:
        # ddgs кидает свои исключения при блокировках/таймаутах — нам они не нужны
        return []

    results = []
    for item in raw or []:
        url = item.get("href") or item.get("url") or ""
        if not url:
            continue
        results.append({
            "title": clean_snippet(item.get("title", ""), limit=160),
            "url": url,
            "snippet": clean_snippet(item.get("body", ""), limit=300),
            "source": "ddgs",
        })
    return results


# --------------------------------------------------------------------------
# провайдер 3: Wikipedia (работает всегда)
# --------------------------------------------------------------------------

def _search_wikipedia(query: str, limit: int) -> list[dict]:
    return [
        {
            "title": hit["title"],
            "url": hit["url"],
            "snippet": hit["snippet"],
            "source": f"wikipedia:{hit['lang']}",
        }
        for hit in wiki_search(query, limit=limit)
    ]


_PROVIDERS = {
    "tavily": _search_tavily,
    "ddgs": _search_ddgs,
    "wikipedia": _search_wikipedia,
}
