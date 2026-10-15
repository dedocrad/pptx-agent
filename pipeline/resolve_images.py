"""Подстановка подходящих изображений в слайды презентации."""

import os

from pptx_engine.spec import PresentationSpec
from tools.image_resolver import find_best_image, intent_from_slide

IMAGE_LAYOUTS = {"image", "text_image", "image_text"}


def resolve_images(spec: PresentationSpec,
                   *, debug: bool = False) -> PresentationSpec:
    """Подобрать изображения; неподходящие слайды оставить текстовыми."""
    for slide in spec.slides:
        if slide.image:
            continue                                  # уже резолвнуто
        intent = intent_from_slide(slide)
        if intent is None:
            continue

        target_layout = _layout_for_image_slide(slide)
        if target_layout is None:
            # титул / источники / две колонки картинку не рисуют — не тратим запросы
            slide.image_query = None
            continue

        image = find_best_image(intent, use_llm=_llm_judge_enabled(), debug=debug)
        if image is None:
            if slide.layout in IMAGE_LAYOUTS:
                slide.layout = "text"
            slide.image_query = None
            continue

        slide.image = image.path
        slide.image_meta = image.meta
        slide.layout = target_layout

    return spec


def _layout_for_image_slide(slide) -> str | None:
    """Каким лэйаутом рисовать слайд с картинкой. None — картинка не предусмотрена."""
    if slide.layout in IMAGE_LAYOUTS:
        return slide.layout
    if slide.layout == "text":
        return "text_image"                           # апгрейд: слайд просил иллюстрацию
    return None



def _llm_judge_enabled() -> bool:
    """LLM-судья по умолчанию включён; выключается переменной PPTX_IMAGE_LLM_JUDGE=0."""
    return os.environ.get("PPTX_IMAGE_LLM_JUDGE", "1") != "0"
