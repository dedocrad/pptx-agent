"""Валидация картинок: «эта картинка вообще годится для слайда?»

Оценка идёт по двум независимым осям, и обе нужны:

  1. ТЕХНИЧЕСКОЕ КАЧЕСТВО (Pillow) — когда файл уже скачан:
     разрешение, соотношение сторон, резкость (дисперсия лапласиана),
     экспозиция, контраст, энтропия. Ловит «мыло», белые заглушки,
     апскейл 200x150 до 4000px и прочий мусор.

  2. РЕЛЕВАНТНОСТЬ ПО ТЕКСТОВОМУ ОПИСАНИЮ (главная фишка Wikimedia):
     у файлов в Commons почти всегда есть описание, категории и осмысленное
     имя. Сравниваем этот текст с интентом слайда:
       * эмбеддингами (локальная модель в LM Studio) — быстро и без галлюцинаций;
       * LLM-судьёй для топ-N — точнее, понимает смысл и русский запрос.

Плюс бонус за «знак качества» Commons (Featured / Quality / Valued images) и
за свободную лицензию.
"""

import math
import os

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

from llm.client import chat_json, DEFAULT_MODEL
from tools._http import post_json
from tools.image_types import (
    ImageCandidate,
    ImageIntent,
    RelevanceJudgement,
    ScoredCandidate,
    TechnicalQuality,
)
from tools.text_utils import lexical_overlap, truncate

# ---------------------------------------------------------------------------
# настройки (все пороги в одном месте — их удобно крутить)
# ---------------------------------------------------------------------------

DEFAULT_MIN_WIDTH = 800          # меньше — на слайд 33 см лучше не ставить
DEFAULT_MIN_HEIGHT = 450
MIN_CONTRAST = 8.0               # СКО яркости: ниже — почти однотонная картинка
MIN_ENTROPY = 1.0
MAX_ASPECT = 3.2                 # 3.2:1 — уже баннер, а не иллюстрация
MIN_ASPECT = 0.35
MAX_BRIGHTNESS = 250.0           # 250+ при низком контрасте = белая заглушка
MIN_BRIGHTNESS = 12.0

EMBEDDINGS_URL = "http://127.0.0.1:1234/v1/embeddings"
EMBEDDING_MODEL = os.environ.get("PPTX_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5")
# Калибровка косинуса для nomic-embed-text: ~0.45 — «мимо», ~0.72 — «в точку».
COSINE_FLOOR = 0.45
COSINE_CEIL = 0.72

# Веса итогового балла (сумма = 1.0).
W_RELEVANCE = 0.50
W_TECHNICAL = 0.30
W_COMMONS_QUALITY = 0.15
W_LICENSE = 0.05

QUALITY_BONUS = {"featured": 10.0, "quality": 7.0, "valued": 4.0, "": 0.0}

_LAPLACIAN_3X3 = (0, 1, 0,
                  1, -4, 1,
                  0, 1, 0)


# ---------------------------------------------------------------------------
# 1. техническое качество файла
# ---------------------------------------------------------------------------

def measure_image(path: str) -> dict | None:
    """Сырые метрики картинки. None, если файл не читается."""
    if not path or not os.path.exists(path):
        return None
    try:
        with Image.open(path) as img:
            img.load()
            width, height = img.size
            gray = img.convert("L")
            # Дисперсия лапласиана: классический прокси-показатель резкости.
            # offset=128 нужен, потому что PIL зажимает отрицательные значения в 0.
            edges = gray.filter(ImageFilter.Kernel((3, 3), _LAPLACIAN_3X3, scale=1, offset=128))
            stat = ImageStat.Stat(gray)
            return {
                "width": width,
                "height": height,
                "sharpness": round(ImageStat.Stat(edges).var[0], 2),
                "brightness": round(stat.mean[0], 2),
                "contrast": round(stat.stddev[0], 2),
                "entropy": round(gray.entropy(), 3),
            }
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def check_technical(path: str,
                    min_width: int = DEFAULT_MIN_WIDTH,
                    min_height: int = DEFAULT_MIN_HEIGHT) -> TechnicalQuality:
    """Проверить скачанный файл. ok=False — использовать нельзя."""
    raw = measure_image(path)
    if raw is None:
        return TechnicalQuality(ok=False, issues=["файл не открывается (битый или не картинка)"])

    width, height = raw["width"], raw["height"]
    aspect = round(width / height, 3) if height else 0.0
    issues: list[str] = []

    # --- жёсткие отказы ---
    if width < min_width or height < min_height:
        issues.append(f"низкое разрешение: {width}x{height} < {min_width}x{min_height}")
    if aspect and (aspect > MAX_ASPECT or aspect < MIN_ASPECT):
        issues.append(f"неудачное соотношение сторон: {aspect}")
    if raw["contrast"] < MIN_CONTRAST and raw["entropy"] < MIN_ENTROPY:
        issues.append("почти однотонная картинка (пустая/заглушка)")
    if raw["brightness"] > MAX_BRIGHTNESS and raw["contrast"] < MIN_CONTRAST:
        issues.append("выбеленная картинка без деталей")
    if raw["brightness"] < MIN_BRIGHTNESS and raw["entropy"] < MIN_ENTROPY:
        issues.append("почти чёрная картинка без деталей")

    quality = TechnicalQuality(
        ok=not issues,
        width=width,
        height=height,
        megapixels=round((width * height) / 1_000_000, 2),
        aspect_ratio=aspect,
        sharpness=raw["sharpness"],
        brightness=raw["brightness"],
        contrast=raw["contrast"],
        entropy=raw["entropy"],
        issues=issues,
    )
    quality.score = _technical_score(quality)
    return quality


def _technical_score(q: TechnicalQuality) -> float:
    """Свести метрики в один балл 0..10 (для сортировки кандидатов)."""
    resolution = _scale_linear(q.megapixels, low=0.4, high=2.5)
    sharpness = _scale_log(q.sharpness, low=30, high=3000)
    exposure = _scale_band(q.brightness, low=45, high=235)
    contrast = _scale_linear(q.contrast, low=15, high=55)
    entropy = _scale_linear(q.entropy, low=1.5, high=5.0)

    score = (0.35 * resolution + 0.30 * sharpness + 0.15 * exposure
             + 0.10 * contrast + 0.10 * entropy) * 10

    if q.issues:
        score = min(score, 3.0)          # жёсткий брак не может быть «хорошим»
    return round(min(score, 10.0), 2)


def _scale_linear(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _scale_log(value: float, low: float, high: float) -> float:
    value = max(value, 0.01)
    return _scale_linear(math.log10(value), math.log10(low), math.log10(high))


def _scale_band(value: float, low: float, high: float) -> float:
    """1.0 внутри [low, high], линейно падает к 0 на краях диапазона."""
    if low <= value <= high:
        return 1.0
    if value < low:
        return max(0.0, 1 - (low - value) / max(low, 1))
    return max(0.0, 1 - (value - high) / max(255 - high, 1))


# ---------------------------------------------------------------------------
# 2. релевантность по текстовому описанию: эмбеддинги
# ---------------------------------------------------------------------------

def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """Векторы для списка текстов через локальный LM Studio. None при недоступности."""
    if not texts:
        return None
    data = post_json(EMBEDDINGS_URL, {"model": EMBEDDING_MODEL, "input": texts}, timeout=60)
    if not data:
        return None
    try:
        return [item["embedding"] for item in data["data"]]
    except (KeyError, TypeError):
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _cosine_to_score(similarity: float) -> float:
    """Косинус → 0..10 по откалиброванному диапазону."""
    return round(_scale_linear(similarity, COSINE_FLOOR, COSINE_CEIL) * 10, 2)


def judge_relevance(intent: ImageIntent,
                    candidates: list[ImageCandidate]) -> list[RelevanceJudgement]:
    """Оценить релевантность всех кандидатов по их описаниям.

    Сначала пробуем эмбеддинги (быстро, один запрос на всех).
    Если LM Studio недоступен — падаем на лексическое пересечение слов.
    """
    if not candidates:
        return []

    texts = [candidate.as_text() for candidate in candidates]
    vectors = embed_texts([intent.as_text()] + texts)

    if vectors and len(vectors) == len(texts) + 1:
        intent_vector, candidate_vectors = vectors[0], vectors[1:]
        return [
            RelevanceJudgement(
                score=_cosine_to_score(cosine_similarity(intent_vector, vector)),
                reason="косинусная близость описания к интенту слайда",
                method="embeddings",
            )
            for vector in candidate_vectors
        ]

    return [
        RelevanceJudgement(
            score=round(lexical_overlap(intent.as_text(), text) * 10, 2),
            reason="лексическое пересечение слов (эмбеддинги недоступны)",
            method="lexical",
        )
        for text in texts
    ]


# ---------------------------------------------------------------------------
# 3. релевантность: LLM-судья (для топа кандидатов)
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """Ты — арт-директор презентации. Реши, подходит ли картинка как иллюстрация к слайду.

СЛАЙД:
{slide}

КАРТИНКА (данные из Wikimedia Commons):
{candidate}

Оцени по шкале 0-10:
  0-2  — не по теме / это иконка, логотип, портрет не по делу;
  3-5  — тема рядом, но иллюстрация слабая или непонятная;
  6-8  — хорошая уместная иллюстрация;
  9-10 — именно то, что нужно, схема/фото прямо по теме.

Ответь коротко: score (число 0-10), reason (одна фраза по-русски),
better_query (английский поисковый запрос, если картинка не подходит, иначе пустая строка).
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"},
        "reason": {"type": "string"},
        "better_query": {"type": "string"},
    },
    "required": ["score", "reason", "better_query"],
    "additionalProperties": False,
}


def judge_with_llm(intent: ImageIntent,
                   candidate: ImageCandidate,
                   model: str = DEFAULT_MODEL) -> RelevanceJudgement | None:
    """Спросить у LLM: «картинка по теме?». None, если модель не ответила валидно."""
    prompt = JUDGE_PROMPT.format(
        slide=truncate(intent.as_text(), 400),
        candidate=truncate(candidate.as_text(), 900),
    )
    data = chat_json([{"role": "user", "content": prompt}], model=model,
                     max_tokens=1024, schema=JUDGE_SCHEMA)
    if not data or "score" not in data:
        return None

    try:
        score = max(0.0, min(10.0, float(data["score"])))
    except (TypeError, ValueError):
        return None

    return RelevanceJudgement(
        score=round(score, 2),
        reason=str(data.get("reason", ""))[:300],
        method="llm",
    )


# ---------------------------------------------------------------------------
# 4. сборка итогового балла
# ---------------------------------------------------------------------------

def combine_scores(relevance: RelevanceJudgement,
                   technical: TechnicalQuality | None,
                   candidate: ImageCandidate) -> float:
    """Итоговый балл 0..10: релевантность + техника + знак качества + лицензия."""
    technical_score = technical.score if technical is not None else 5.0   # ещё не скачан
    total = (
        W_RELEVANCE * relevance.score
        + W_TECHNICAL * technical_score
        + W_COMMONS_QUALITY * QUALITY_BONUS.get(candidate.commons_quality, 0.0)
        + W_LICENSE * (10.0 if candidate.license_ok else 0.0)
    )
    return round(total, 2)


def score_candidates(intent: ImageIntent,
                     candidates: list[ImageCandidate],
                     *,
                     use_llm: bool = True,
                     llm_top_k: int = 3,
                     model: str = DEFAULT_MODEL) -> list[ScoredCandidate]:
    """Оценить и отсортировать кандидатов (по метаданным, без скачивания).

    1) считаем релевантность описаний эмбеддингами;
    2) для топ-N уточняем её LLM-судьёй и усредняем;
    3) помечаем несоответствия (лицензия, размер) в issues.
    """
    if not candidates:
        return []

    judgements = judge_relevance(intent, candidates)
    scored = [
        ScoredCandidate(candidate=candidate, relevance=judgement)
        for candidate, judgement in zip(candidates, judgements)
    ]
    scored.sort(key=lambda item: item.relevance.score, reverse=True)

    if use_llm:
        for item in scored[:max(llm_top_k, 0)]:
            llm_judgement = judge_with_llm(intent, item.candidate, model=model)
            if llm_judgement is None:
                continue
            # эмбеддинги хороши на «про что картинка», LLM — на «уместно ли»; берём смесь
            item.relevance = RelevanceJudgement(
                score=round(0.5 * item.relevance.score + 0.5 * llm_judgement.score, 2),
                reason=f"{llm_judgement.reason} (эмбеддинги: {item.relevance.score})",
                method="embeddings+llm",
            )

    for item in scored:
        item.issues = _metadata_issues(item.candidate)
        item.total = combine_scores(item.relevance, item.technical, item.candidate)

    scored.sort(key=lambda item: item.total, reverse=True)
    return scored


def _metadata_issues(candidate: ImageCandidate) -> list[str]:
    """Проблемы, которые видно ещё до скачивания."""
    issues = []
    if not candidate.license_ok:
        issues.append(f"несвободная/неясная лицензия: {candidate.license_short or 'не указана'}")
    if not candidate.download_url:
        issues.append("нет прямой ссылки на файл")
    return issues
