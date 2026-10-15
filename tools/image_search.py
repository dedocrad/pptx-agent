"""Поиск картинок в Wikimedia Commons.

Почему Commons:
  * API без ключей, с честными метаданными: описание, категории, автор, лицензия, размер;
  * по описанию + категориям можно ОЧЕНЬ точно судить, что на картинке
    (этим и занимается tools/image_quality.py);
  * есть встроенные знаки качества: категории «Featured pictures» / «Quality images».

Сначала commons_search находит кандидатов, потом image_resolver оценивает и скачивает их.
"""

import hashlib
import os
from urllib.parse import urlparse

from pptx_engine.spec import ImageMeta
from tools._http import get_json, download
from tools.image_types import ImageCandidate
from tools.text_utils import significant_words, strip_tags, slugify

COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Лицензии, которые безопасно класть в презентацию.
ALLOWED_LICENSE_PREFIXES = (
    "cc by", "cc-by", "cc0", "public domain", "pd",
    "cc0-", "cc-by-", "cc by-sa",
)
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# Категории Commons, которые означают «сообщество уже проверило эту картинку».
QUALITY_CATEGORY_MARKERS = {
    "featured pictures": "featured",
    "quality images": "quality",
    "valued images": "valued",
}

THUMB_WIDTH = 1600          # ширина превью, которое реально скачиваем


# --------------------------------------------------------------------------
# поиск кандидатов (без скачивания)
# --------------------------------------------------------------------------

def commons_search(query: str, limit: int = 10,
                   min_width: int = 0, min_height: int = 0) -> list[ImageCandidate]:
    """Найти файлы в Commons. Возвращает список кандидатов (может быть пустым).

    Делаем два прохода:
      1. с фильтром `filetype:bitmap|drawing` — сразу только растровые картинки;
      2. если картинок мало — обычный поиск и отсев PDF/SVG-мусора по mime.
    Второй проход нужен потому, что фильтр по типу иногда рубит релевантную выдачу
    (например, запрос матчится только по описаниям PDF-статей).
    """
    query = (query or "").strip()
    if not query:
        return []

    pages: dict[int, dict] = {}
    enough = min(3, limit)          # сколько картинок считаем «нормальной выдачей»

    # Лестница запросов: длинный запрос в Commons часто не находит НИЧЕГО, потому что
    # поиск идёт по И. Поэтому постепенно укорачиваем запрос и убираем фильтр типа файла.
    attempts = [
        f"{query} filetype:bitmap|drawing",
        f"{significant_words(query, 3)} filetype:bitmap|drawing",
        f"{significant_words(query, 2)} filetype:bitmap|drawing",
        query,                                        # без фильтра: отсеем по mime руками
    ]
    for attempt in attempts:
        if not attempt.strip():
            continue
        if _count_images(pages) >= enough:
            break
        for page in _search_pages(attempt, limit * 3):
            pages.setdefault(page.get("pageid", 0), page)

    candidates = []
    for page in pages.values():
        candidate = _page_to_candidate(page)
        if candidate is None:
            continue
        if candidate.width < min_width or candidate.height < min_height:
            continue
        candidates.append(candidate)

    # API отдаёт страницы словарём, порядок выдачи теряется — восстанавливаем по index,
    # а при равенстве поднимаем наверх файлы со знаком качества Commons.
    candidates.sort(key=lambda c: (c.search_rank, 0 if c.commons_quality else 1))
    return candidates[:limit]


def _count_images(pages: dict[int, dict]) -> int:
    return sum(1 for page in pages.values() if _is_image(page))



def _search_pages(search_query: str, limit: int) -> list[dict]:
    """Один запрос к API Commons. Возвращает список страниц (может быть пустым)."""
    data = get_json(COMMONS_API, params={
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrsearch": search_query,
        "gsrnamespace": "6",                    # namespace 6 = File:
        "gsrlimit": str(max(limit, 1)),
        "prop": "imageinfo|categories",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": str(THUMB_WIDTH),
        "cllimit": "30",
    })
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    return list(pages.values())


def _is_image(page: dict) -> bool:
    infos = page.get("imageinfo") or []
    return bool(infos) and str(infos[0].get("mime", "")).startswith("image/")



def _page_to_candidate(page: dict) -> ImageCandidate | None:
    """Одна страница API → ImageCandidate. None, если это не картинка."""
    infos = page.get("imageinfo") or []
    if not infos:
        return None
    info = infos[0]

    mime = info.get("mime", "")
    if mime and not mime.startswith("image/"):
        return None                             # pdf, видео, аудио — мимо

    meta = info.get("extmetadata") or {}
    categories = _categories(page, meta)
    license_short = _meta_value(meta, "LicenseShortName")
    description = strip_tags(_meta_value(meta, "ImageDescription")) or \
        strip_tags(_meta_value(meta, "ObjectName"))

    return ImageCandidate(
        pageid=page.get("pageid", 0),
        title=page.get("title", ""),
        description=description[:600],
        categories=categories,
        width=int(info.get("width") or 0),
        height=int(info.get("height") or 0),
        mime=mime,
        size_bytes=int(info.get("size") or 0),
        license_short=license_short,
        license_ok=license_ok(license_short),
        author=strip_tags(_meta_value(meta, "Artist"))[:200],
        credit=strip_tags(_meta_value(meta, "Credit"))[:200],
        page_url=info.get("descriptionurl", ""),
        # thumburl — растеризованное превью: для SVG/TIFF это единственный способ
        # получить картинку, которую понимает Pillow.
        download_url=info.get("thumburl") or info.get("url") or "",
        thumb_url=info.get("thumburl", ""),
        commons_quality=detect_commons_quality(categories),
        search_rank=int(page.get("index") or 999),
    )


def _meta_value(meta: dict, key: str) -> str:
    value = (meta.get(key) or {}).get("value", "")
    return value if isinstance(value, str) else str(value or "")


def _categories(page: dict, meta: dict) -> list[str]:
    """Категории из prop=categories, с запасным вариантом из extmetadata."""
    names = [c.get("title", "") for c in (page.get("categories") or [])]
    if not names:
        names = [c for c in _meta_value(meta, "Categories").split("|") if c]
    # убираем префикс "Category:" и служебные скрытые категории
    return [n.replace("Category:", "").strip() for n in names if n.strip()][:30]


def detect_commons_quality(categories: list[str]) -> str:
    """featured / quality / valued — по названиям категорий Commons."""
    lowered = [c.lower() for c in categories]
    for marker, label in QUALITY_CATEGORY_MARKERS.items():
        if any(marker in c for c in lowered):
            return label
    return ""


def license_ok(short_name: str) -> bool:
    """Пропускаем только свободные лицензии."""
    if not isinstance(short_name, str) or not short_name:
        return False
    s = short_name.strip().lower()
    return any(s.startswith(p) for p in ALLOWED_LICENSE_PREFIXES)


# --------------------------------------------------------------------------
# скачивание
# --------------------------------------------------------------------------

def download_candidate(candidate: ImageCandidate, out_dir: str = "assets/cache",
                       name_hint: str = "") -> str | None:
    """Скачать файл кандидата в кэш. Возвращает путь или None.

    Имя файла детерминированное (pageid + запрос), поэтому повторный запуск
    не качает одно и то же второй раз.
    """
    url = candidate.download_url
    if not url:
        return None

    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in ALLOWED_EXT:
        ext = ".jpg"

    key = f"{candidate.pageid}|{name_hint or candidate.title}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    dest = os.path.join(out_dir, f"{slugify(name_hint or candidate.title, 32)}-{digest}{ext}")

    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        return dest                        # кэш-хит
    if os.path.exists(dest):
        os.remove(dest)                    # битый огрызок от прошлого запуска

    return dest if download(url, dest) else None


def to_image_meta(candidate: ImageCandidate, *, score: float = 0.0) -> ImageMeta:
    """ImageCandidate → ImageMeta (то, что едет в спецификацию презентации)."""
    return ImageMeta(
        source_url=candidate.page_url or candidate.download_url,
        license=candidate.license_short or "unknown",
        author=candidate.author[:120],
        width=candidate.width,
        height=candidate.height,
        provider="wikimedia",
        description=candidate.description[:300],
        page_url=candidate.page_url,
        score=round(score, 2),
    )
