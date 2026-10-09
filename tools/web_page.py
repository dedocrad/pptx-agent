"""Загрузка одной веб-страницы и превращение её в читаемый текст.

Нужна агенту, когда из выдачи он выбрал конкретный источник и хочет
вытащить оттуда факты для слайда. PDF/JS-приложения не поддерживаем —
возвращаем None, и агент просто возьмёт другой источник.
"""

import re
from urllib.parse import unquote

from tools._http import get_text
from tools.text_utils import html_to_text, truncate

MAX_CHARS = 6000


def fetch_web_page(url: str, max_chars: int = MAX_CHARS) -> dict | None:
    """Скачать страницу и вернуть {url, title, text}. None — если не получилось."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return None

    # Википедию читаем через API: HTML-версия тянет за собой меню, языки и прочий мусор
    wiki = _wikipedia_page(url, max_chars)
    if wiki is not None:
        return wiki

    raw = get_text(url)
    if not raw:
        return None

    if _looks_like_binary(raw):
        return None

    body = _main_block(raw) or raw
    text = html_to_text(body)
    if len(text) < 200:                 # почти наверняка заглушка/JS-шелл
        text = html_to_text(raw)
    if len(text) < 200:
        return None

    return {
        "url": url,
        "title": _page_title(raw) or url,
        "text": truncate(text, max_chars),
    }


def _wikipedia_page(url: str, max_chars: int) -> dict | None:
    """Ссылка на Википедию → та же статья, но чистым текстом из API."""
    match = re.match(r"https?://([a-z\-]+)\.(?:m\.)?wikipedia\.org/wiki/(.+)$", url)
    if not match:
        return None

    from tools.wiki import wiki_page                 # локальный импорт: без циклов
    lang, raw_title = match.group(1), match.group(2)
    title = unquote(raw_title).replace("_", " ").split("#")[0]
    page = wiki_page(title, lang=lang, max_chars=max_chars)
    if not page:
        return None

    return {
        "url": page["url"],
        "title": page["title"],
        "text": page["summary"] + "\n\n" + page["text"],
    }


def _looks_like_binary(raw: str) -> bool:
    """PDF и прочие бинарники декодируются в мусор — ловим по сигнатуре."""
    head = raw[:400].lstrip()
    return head.startswith("%PDF") or "\x00" in raw[:2000]


def _page_title(raw: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    return html_to_text(match.group(1)).strip()[:200] if match else ""


def _main_block(raw: str) -> str | None:
    """Пытаемся взять <article>/<main>: там обычно нет меню и футеров."""
    for tag in ("article", "main"):
        match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", raw, re.IGNORECASE | re.DOTALL)
        if match and len(match.group(1)) > 500:
            return match.group(1)
    return None
