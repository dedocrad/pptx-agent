"""Модели данных для поиска и оценки картинок.

Разделяем три разные сущности, чтобы код не путался:
  * ImageIntent     — ЧТО мы ищем (запрос + контекст слайда);
  * ImageCandidate  — что нашлось в Wikimedia Commons (метаданные, без скачивания);
  * TechnicalQuality / RelevanceJudgement / ScoredCandidate — результаты проверок.
"""

from pydantic import BaseModel, Field


class ImageIntent(BaseModel):
    """Осмысленный запрос на картинку: не просто строка, а контекст слайда."""

    query: str                                  # "transformer self-attention diagram"
    slide_title: str = ""                       # "Self-Attention"
    bullets: list[str] = Field(default_factory=list)
    language: str = "ru"

    def as_text(self) -> str:
        """Склеиваем всё в один текст — его скармливаем эмбеддингам и LLM-судье."""
        parts = [self.query, self.slide_title, *self.bullets[:4]]
        return " | ".join(p for p in parts if p)


class ImageCandidate(BaseModel):
    """Кандидат из Wikimedia Commons. Всё, что даёт API, без скачивания файла."""

    pageid: int = 0
    title: str = ""                             # "File:Attention Is All You Need.png"
    description: str = ""                       # ImageDescription из extmetadata (уже без HTML)
    categories: list[str] = Field(default_factory=list)
    width: int = 0
    height: int = 0
    mime: str = ""
    size_bytes: int = 0
    license_short: str = ""                     # "CC BY-SA 4.0", "Public domain", ...
    license_ok: bool = False
    author: str = ""
    credit: str = ""
    page_url: str = ""                          # страница файла в Commons
    download_url: str = ""                      # прямая ссылка на файл (или thumb)
    thumb_url: str = ""
    commons_quality: str = ""                   # "featured" | "quality" | "valued" | ""
    search_rank: int = 0                        # позиция в поисковой выдаче Commons

    @property
    def megapixels(self) -> float:
        return round((self.width * self.height) / 1_000_000, 2)

    @property
    def aspect_ratio(self) -> float:
        if not self.height:
            return 0.0
        return round(self.width / self.height, 3)

    def as_text(self) -> str:
        """Текстовое описание картинки — по нему и оцениваем релевантность.

        Это ключевая идея: Commons почти всегда содержит человеческое описание,
        категории и осмысленное имя файла, поэтому качество/уместность картинки
        можно оценивать текстом, не глядя на пиксели.
        """
        chunks = [
            f"file name: {self.title}",
            f"description: {self.description}" if self.description else "",
            f"categories: {', '.join(self.categories[:12])}" if self.categories else "",
            f"type: {self.mime}, {self.width}x{self.height}",
        ]
        return "\n".join(c for c in chunks if c)


class TechnicalQuality(BaseModel):
    """Проверка самого файла (Pillow): хватит ли его для слайда."""

    ok: bool = False
    width: int = 0
    height: int = 0
    megapixels: float = 0.0
    aspect_ratio: float = 0.0
    sharpness: float = 0.0                      # дисперсия лапласиана: больше = резче
    brightness: float = 0.0                     # средняя яркость 0..255
    contrast: float = 0.0                       # СКО яркости
    entropy: float = 0.0                        # насыщенность деталями
    issues: list[str] = Field(default_factory=list)
    score: float = 0.0                          # 0..10

    def as_text(self) -> str:
        return (f"{self.width}x{self.height}, sharpness={self.sharpness}, "
                f"brightness={self.brightness}, contrast={self.contrast}, "
                f"score={self.score}, issues={self.issues or 'нет'}")


class RelevanceJudgement(BaseModel):
    """Насколько картинка подходит слайду — по её текстовому описанию."""

    score: float = 0.0                          # 0..10
    reason: str = ""
    method: str = "lexical"                     # "embeddings" | "lexical" | "llm"


class ScoredCandidate(BaseModel):
    """Кандидат + оценки + итоговый балл. Именно это сортируем и выбираем."""

    candidate: ImageCandidate
    relevance: RelevanceJudgement
    technical: TechnicalQuality | None = None
    total: float = 0.0
    issues: list[str] = Field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """Пропускать ли кандидата дальше (жёсткие фильтры уже отсеяли явный мусор)."""
        if self.technical is not None and not self.technical.ok:
            return False
        return not self.issues
