"""Утилиты работы с текстом: HTML → plain text, обрезка, лёгкая нормализация.

Никаких bs4/lxml — только stdlib. Этого хватает, чтобы:
  * вытащить читаемый текст из HTML-страницы;
  * почистить сниппеты поисковой выдачи;
  * посчитать лексическое пересечение описания картинки и запроса слайда
    (fallback, если локальные embeddings недоступны).
"""

import html
import re
from html.parser import HTMLParser

# теги, внутри которых текст нам не нужен вообще
_SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form", "iframe"}
# теги, после которых логично поставить перевод строки
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "blockquote", "pre", "table", "ul", "ol",
}

_STOPWORDS = {
    # русские
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со", "как", "а", "то", "все",
    "она", "так", "его", "но", "да", "ты", "к", "у", "же", "вы", "за", "бы", "по",
    "только", "ее", "мне", "было", "вот", "от", "меня", "еще", "нет", "о", "из",
    "ему", "теперь", "когда", "даже", "ну", "вдруг", "ли", "если", "уже", "или",
    "ни", "быть", "был", "него", "до", "вас", "нибудь", "опять", "уж", "вам", "ведь",
    "там", "потом", "себя", "ничего", "ей", "может", "они", "тут", "где", "есть",
    "надо", "ней", "для", "мы", "тебя", "их", "чем", "была", "сам", "чтоб", "без",
    "будто", "чего", "раз", "тоже", "себе", "под", "будет", "ж", "тогда", "кто",
    "этот", "того", "потому", "этого", "какой", "совсем", "ним", "здесь", "этом",
    "один", "почти", "мой", "тем", "чтобы", "нее", "сейчас", "были", "куда", "зачем",
    "эти", "так", "это", "при", "про", "их", "эти", "такой", "который", "которая",
    # английские
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "with", "and", "or",
    "is", "are", "was", "were", "be", "been", "by", "as", "that", "this", "these",
    "those", "it", "its", "from", "into", "over", "under", "about", "than", "then",
}


class _HtmlToText(HTMLParser):
    """Минимальный HTML→text: выкидываем script/style, блочные теги → \\n."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def html_to_text(raw_html: str) -> str:
    """HTML → плоский текст с сохранёнными абзацами."""
    if not raw_html:
        return ""
    parser = _HtmlToText()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        # парсер не должен ронять тулзу из-за кривой разметки
        pass
    text = html.unescape(parser.text())
    text = text.replace("\xa0", " ")          # &nbsp; ломает сравнение строк
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def strip_tags(raw_html: str) -> str:
    """Снять теги, но НЕ трогать переводы строк (для сниппетов выдачи)."""
    if not raw_html:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def clean_snippet(text: str, limit: int = 300) -> str:
    """Сниппет в одну строку, без тегов, обрезанный по границе слова."""
    text = strip_tags(text or "")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return (cut or text[:limit]).rstrip(" ,;:.") + "…"


def truncate(text: str, limit: int = 1500) -> str:
    """Обрезаем длинный текст, добавляя маркер — чтобы не раздувать промпт."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …[обрезано]"


def slugify(text: str, limit: int = 40) -> str:
    """Человекочитаемый кусок имени файла (для имён скачанных картинок)."""
    text = (text or "").lower()
    text = re.sub(r"[^0-9a-zа-я]+", "-", text)
    return text.strip("-")[:limit] or "image"


def stem(token: str) -> str:
    """Очень грубая нормализация: русские окончания и английское множественное.

    Задача не лингвистическая точность, а чтобы "планеты" и "планет" совпали.
    """
    token = token.lower()
    for suffix in ("ениями", "аниями", "ения", "ания", "ение", "ание",
                   "иями", "ями", "ами", "ов", "ев", "ий", "ый", "ой", "ая", "ое",
                   "ые", "ия", "ие", "ing", "ers", "ies", "es", "s",
                   "я", "е", "ы", "и", "у", "ю", "а", "о", "ь", "й"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> set[str]:
    """Множество значимых основ слова (стоп-слова и короткие токены выкинуты)."""
    words = re.findall(r"[0-9a-zA-Zа-яА-ЯёЁ]{3,}", (text or "").lower())
    return {stem(w) for w in words if w not in _STOPWORDS}


def significant_words(text: str, limit: int = 3) -> str:
    """Первые N значимых слов в исходном виде.

    Нужно для «лестницы укорачивания» поискового запроса: Commons ищет по И,
    поэтому длинный запрос часто не находит вообще ничего.
    """
    words = re.findall(r"[0-9a-zA-Zа-яА-ЯёЁ\-]{3,}", text or "")
    kept = [w for w in words if w.lower() not in _STOPWORDS]
    return " ".join(kept[:limit])


def lexical_overlap(query: str, text: str) -> float:
    """0..1: какая доля значимых слов запроса нашлась в тексте.

    Считаем именно покрытие запроса (а не Jaccard), потому что описание
    картинки в Commons обычно сильно длиннее запроса слайда.
    """
    q, t = tokenize(query), tokenize(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q)
