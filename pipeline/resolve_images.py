"""Подстановка картинок в спецификацию презентации.
Если достойной картинки нет — слайд деградирует до текстового, а не получает мусор.
"""

import os

from pptx_engine.spec import PresentationSpec
from tools.image_resolver import find_best_image, intent_from_slide

IMAGE_LAYOUTS = {"image", "text_image", "image_text"}


def resolve_images(spec: PresentationSpec,
                   *,
                   use_validation: bool = True,
                   debug: bool = False) -> PresentationSpec:
    """Пройти по слайдам и подставить картинки.

    use_validation=True  — полный конвейер (описание + LLM-судья + проверка файла);
    use_validation=False — быстрый режим: первая картинка с подходящей лицензией.
    """
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

        path, meta = _pick_image(intent, use_validation=use_validation, debug=debug)

        if path is None:
            if slide.layout in IMAGE_LAYOUTS:
                slide.layout = "text"                 # лучше текст, чем пустая рамка
            slide.image_query = None
            continue

        slide.image = path
        slide.image_meta = meta
        slide.layout = target_layout                  # "text" + картинка → "text_image"

    return spec


def _layout_for_image_slide(slide) -> str | None:
    """Каким лэйаутом рисовать слайд с картинкой. None — картинка не предусмотрена."""
    if slide.layout in IMAGE_LAYOUTS:
        return slide.layout
    if slide.layout == "text":
        return "text_image"                           # апгрейд: слайд просил иллюстрацию
    return None



def _pick_image(intent, *, use_validation: bool, debug: bool):
    """Вернуть (путь, метаданные) или (None, None)."""
    if use_validation:
        pick = find_best_image(intent, use_llm=_llm_judge_enabled(), debug=debug)
        if pick is None:
            return None, None
        return pick.path, pick.meta

    from tools.image_search import image_search          # быстрый режим без оценки описаний
    legacy = image_search(intent.query, debug=debug)
    if legacy is None:
        return None, None
    return legacy.path, legacy.meta


def _llm_judge_enabled() -> bool:
    """LLM-судья по умолчанию включён; выключается переменной PPTX_IMAGE_LLM_JUDGE=0."""
    return os.environ.get("PPTX_IMAGE_LLM_JUDGE", "1") != "0"
