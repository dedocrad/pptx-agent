"""Рендер .pptx: собранная презентация из спецификации.

Публичное API пакета:
    from pptx_engine import save
    from pptx_engine.spec import PresentationSpec, SlideSpec, ImageMeta
"""

from pptx_engine.engine import build_presentation, save
from pptx_engine.layouts import SLIDE_H, SLIDE_W
from pptx_engine.spec import ImageMeta, Layout, PresentationSpec, SlideSpec
from pptx_engine.theme import THEMES, Theme

__all__ = [
    "ImageMeta",
    "Layout",
    "PresentationSpec",
    "SLIDE_H",
    "SLIDE_W",
    "SlideSpec",
    "THEMES",
    "Theme",
    "build_presentation",
    "save",
]
