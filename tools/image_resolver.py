"""Резолвер картинки: из интента слайда в готовый файл на диске.

Конвейер:
    intent → Commons search → оценка описаний (эмбеддинги + LLM)
           → скачивание по одному → техническая проверка файла
           → первый, кто прошёл обе проверки, побеждает
           → иначе None (вызывающий код деградирует слайд до текста)

Почему именно так: скачивать 8 файлов ради одной картинки дорого и медленно,
поэтому сначала отсеиваем по метаданным (это бесплатно), и только потом качаем.
"""

import os

from pydantic import BaseModel

from pptx_engine.spec import ImageMeta
from tools.image_quality import check_technical, combine_scores, score_candidates
from tools.image_search import ResolvedImage, commons_search, download_candidate, to_image_meta
from tools.image_types import ImageCandidate, ImageIntent, ScoredCandidate

DEFAULT_MAX_CANDIDATES = 8
DEFAULT_MAX_DOWNLOADS = 4          # сколько файлов максимум качаем за один поиск
MIN_TOTAL_SCORE = 4.0              # ниже — считаем, что нормальной картинки не нашлось


class ImagePick(BaseModel):
    """Результат: путь к файлу + метаданные + полный разбор оценок."""

    path: str
    meta: ImageMeta
    score: ScoredCandidate

    def as_resolved(self) -> ResolvedImage:
        """Для старого кода, который ждёт ResolvedImage."""
        return ResolvedImage(path=self.path, meta=self.meta)


def find_best_image(intent: ImageIntent | str,
                    *,
                    max_candidates: int = DEFAULT_MAX_CANDIDATES,
                    max_downloads: int = DEFAULT_MAX_DOWNLOADS,
                    min_width: int = 800,
                    min_height: int = 450,
                    min_score: float = MIN_TOTAL_SCORE,
                    out_dir: str = "assets/cache",
                    use_llm: bool | None = None,
                    llm_top_k: int = 2,
                    allow_unsafe_license: bool = False,
                    debug: bool = False) -> ImagePick | None:
    """Найти лучшую картинку под интент слайда. None — если ничего достойного нет.

    use_llm=None — берём настройку из окружения (PPTX_IMAGE_LLM_JUDGE=0 отключает
    LLM-судью и оставляет только быстрые эмбеддинги).
    """
    intent = _as_intent(intent)
    if not intent.query.strip():
        return None
    if use_llm is None:
        use_llm = os.environ.get("PPTX_IMAGE_LLM_JUDGE", "1") != "0"

    candidates = commons_search(intent.query, limit=max_candidates,
                               min_width=min_width, min_height=min_height)
    if debug:
        print(f"[find_best_image] {intent.query!r}: {len(candidates)} кандидатов")
    if not candidates:
        return None

    scored = score_candidates(intent, candidates, use_llm=use_llm, llm_top_k=llm_top_k)

    if debug:
        for item in scored:
            print(f"  {item.total:5.2f} | {item.relevance.score:5.2f} rel | "
                  f"{item.candidate.title[:60]} | {item.issues or 'ок'}")

    downloads = 0
    for item in scored:
        if item.total < min_score:
            break                                  # список отсортирован — дальше только хуже
        if item.issues and not allow_unsafe_license:
            if debug:
                print(f"  skip {item.candidate.title[:50]}: {item.issues}")
            continue
        if downloads >= max_downloads:
            break
        downloads += 1

        pick = _download_and_check(intent, item, out_dir, min_width, min_height,
                                   min_score, debug)
        if pick is not None:
            return pick

    return None


def _download_and_check(intent: ImageIntent,
                        item: ScoredCandidate,
                        out_dir: str,
                        min_width: int,
                        min_height: int,
                        min_score: float,
                        debug: bool) -> ImagePick | None:
    """Скачать кандидата, проверить пиксели, пересчитать балл."""
    path = download_candidate(item.candidate, out_dir=out_dir, name_hint=intent.query)
    if path is None:
        if debug:
            print(f"  не скачалось: {item.candidate.title[:60]}")
        return None

    technical = check_technical(path, min_width=min_width, min_height=min_height)
    if debug:
        print(f"  проверка файла: {technical.as_text()}")
    if not technical.ok:
        _remove(path)                              # брак в кэше не нужен
        return None

    item.technical = technical
    item.total = combine_scores(item.relevance, technical, item.candidate)
    if item.total < min_score:
        _remove(path)
        return None

    return ImagePick(path=path, meta=to_image_meta(item.candidate, score=item.total), score=item)


def validate_image_file(path: str,
                        intent: ImageIntent | str | None = None,
                        candidate: ImageCandidate | None = None,
                        *,
                        min_width: int = 800,
                        min_height: int = 450) -> dict:
    """Проверить уже скачанную картинку: пиксели + (если есть метаданные) релевантность.

    Возвращает готовый к печати словарь — его отдаём агенту как Observation.
    """
    technical = check_technical(path, min_width=min_width, min_height=min_height)
    result = {
        "path": path,
        "ok": technical.ok,
        "technical": technical.as_text(),
        "issues": list(technical.issues),
    }
    if intent is None or candidate is None:
        return result

    from tools.image_quality import judge_with_llm, judge_relevance   # локально: без циклов импорта
    judgement = judge_with_llm(_as_intent(intent), candidate)
    if judgement is None:
        judgement = judge_relevance(_as_intent(intent), [candidate])[0]

    result["relevance"] = judgement.score
    result["relevance_reason"] = judgement.reason
    result["total"] = combine_scores(judgement, technical, candidate)
    result["ok"] = technical.ok and judgement.score >= 3.0
    return result


def intent_from_slide(slide) -> ImageIntent | None:
    """SlideSpec (llm.schema) → ImageIntent. None, если картинка не нужна."""
    query = (getattr(slide, "image_query", None) or "").strip()
    if not query:
        return None
    return ImageIntent(
        query=query,
        slide_title=getattr(slide, "title", "") or "",
        bullets=list(getattr(slide, "bullets", []) or []),
    )


def _as_intent(intent: ImageIntent | str) -> ImageIntent:
    if isinstance(intent, ImageIntent):
        return intent
    return ImageIntent(query=str(intent))


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
