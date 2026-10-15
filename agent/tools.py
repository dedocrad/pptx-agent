"""Инструменты, которые может вызывать LangChain-агент."""

import json

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from llm.generator import generate_spec
from pipeline.resolve_images import resolve_images
from pptx_engine import save as save_presentation
from pptx_engine.spec import PresentationSpec


@tool
def create_presentation(
    topic: str,
    slide_count: int = 5,
    out_path: str = "out_agent.pptx",
    with_images: bool = True,
    *,
    config: RunnableConfig,
) -> str:
    """Сразу создать презентацию; не запрашивай необязательные уточнения.

    Args:
        topic: тема презентации.
        slide_count: число слайдов, по умолчанию 5.
        out_path: путь к файлу .pptx.
        with_images: автоматически подобрать изображения.
    """
    stages = []
    current_stage = "Генерация слайдов"

    try:
        count = max(3, min(slide_count, 20))
        request = f"{topic}. Слайдов: {count}."
        model = config.get("configurable", {}).get("model")
        if model:
            spec = generate_spec(request, model=model)
        else:
            spec = generate_spec(request)

        stages.append({
            "name": current_stage,
            "status": "completed",
            "detail": f"Подготовлено слайдов: {len(spec.slides)}",
        })

        if with_images:
            current_stage = "Поиск изображений"
            image_queries = sum(bool(slide.image_query) for slide in spec.slides)
            spec = resolve_images(spec)
            image_count = sum(bool(slide.image) for slide in spec.slides)
            stages.append({
                "name": current_stage,
                "status": "completed",
                "detail": f"Запросов: {image_queries}; добавлено: {image_count}",
            })
        else:
            stages.append({
                "name": "Поиск изображений",
                "status": "skipped",
                "detail": "Поиск отключён.",
            })

        current_stage = "Сборка файла"
        path = save_presentation(spec, out_path)
        stages.append({
            "name": current_stage,
            "status": "completed",
            "detail": f"Файл сохранён: {path}",
        })
        return _json({"stages": stages, **_deck_report(spec, path)})
    except Exception as exc:
        stages.append({
            "name": current_stage,
            "status": "failed",
            "detail": f"{type(exc).__name__}: {exc}",
        })
        return _json({
            "ok": False,
            "note": f"Не удалось собрать презентацию: {type(exc).__name__}: {exc}",
            "stages": stages,
        })


ALL_TOOLS = [create_presentation]


def _deck_report(spec: PresentationSpec, path: str) -> dict:
    return {
        "ok": True,
        "path": path,
        "title": spec.title,
        "slides": [
            {
                "layout": slide.layout,
                "title": slide.title,
                "image": slide.image,
                "license": slide.image_meta.license if slide.image_meta else None,
            }
            for slide in spec.slides
        ],
        "slides_total": len(spec.slides),
        "images_total": sum(bool(slide.image) for slide in spec.slides),
    }


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)
