"""Модели данных для генерации и рендеринга презентаций."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


Layout = Literal[
    "title", "text", "image", "text_image",
    "image_text", "two_columns", "references",
]


class ImageMeta(BaseModel):
    """Происхождение картинки: источник, лицензия, автор, размер, качество."""

    source_url: str
    license: str = "unknown"          # "cc0", "cc-by", "fair-use", ...
    author: str = ""
    width: int = 0
    height: int = 0
    provider: str = ""                # "wikimedia", "openverse", "pexels", ...

    # --- заполняет валидатор картинок (tools/image_quality.py) ---
    description: str = ""             # текстовое описание из Commons
    page_url: str = ""                # страница файла (для слайда «Источники»)
    score: float = 0.0                # итоговый балл валидатора 0..10


class SlideSpec(BaseModel):
    layout: Layout
    title: str = ""
    subtitle: str = ""

    # --- intent: что хочет LLM ---
    image_query: Optional[str] = None      # "transformer architecture diagram"

    # --- resolved: что подставил tool ---
    image: Optional[str] = None            # "assets/cache/abc123.png"
    image_meta: Optional[ImageMeta] = None # источник, лицензия, размер

    bullets: list[str] = Field(default_factory=list)
    column_left: list[str] = Field(default_factory=list)
    column_right: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)

    @field_validator("bullets", "column_left", "column_right", "references")
    @classmethod
    def limit_items(cls, v: list[str]) -> list[str]:
        return v[:6]

    @field_validator("bullets")
    @classmethod
    def bullet_length(cls, v: list[str]) -> list[str]:
        return [b[:200] for b in v]


class PresentationSpec(BaseModel):
    title: str
    author: str = ""
    theme: str = "default"
    slides: list[SlideSpec] = Field(default_factory=list)

    @field_validator("slides")
    @classmethod
    def check_slides(cls, v: list[SlideSpec]) -> list[SlideSpec]:
        if not (1 <= len(v) <= 30):
            raise ValueError("slides must be 1..30")
        return v
