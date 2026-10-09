#!/usr/bin/env python
"""Собрать .pptx без агента: тема → готовая презентация.

Это детерминированный путь (LLM планирует слайды → валидатор ищет картинки → рендер).
Агентный путь — scripts/agent_demo.py --out deck.pptx.

Примеры:
    python scripts/make_deck.py "Космос: Солнечная система, 6 слайдов"
    python scripts/make_deck.py "Квантовые вычисления" --out quantum.pptx --slides 8
    python scripts/make_deck.py "История Рима" --no-images --no-llm-judge
"""

import argparse
import logging
import os
import pathlib
import sys
import warnings

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

logging.disable(logging.WARNING)
warnings.filterwarnings("ignore")

from agent.llm import DEFAULT_MODEL                          # noqa: E402
from llm.generator import generate_spec                       # noqa: E402
from pipeline.resolve_images import resolve_images            # noqa: E402
from pptx_engine import save                                  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Тема → .pptx")
    parser.add_argument("topic", help="тема презентации")
    parser.add_argument("--slides", type=int, default=0, help="сколько слайдов (0 — на усмотрение модели)")
    parser.add_argument("--out", default="out_deck.pptx", help="имя файла .pptx")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="модель в LM Studio")
    parser.add_argument("--no-images", action="store_true", help="не искать картинки")
    parser.add_argument("--no-llm-judge", action="store_true",
                        help="оценивать картинки только эмбеддингами (быстрее)")
    parser.add_argument("--debug", action="store_true", help="печатать кандидатов картинок")
    args = parser.parse_args()

    if args.no_llm_judge:
        os.environ["PPTX_IMAGE_LLM_JUDGE"] = "0"

    request = args.topic if not args.slides else f"{args.topic}. Слайдов: {args.slides}."
    print(f"модель: {args.model}\nзапрос: {request}\n")

    spec = generate_spec(request, model=args.model)
    print(f"слайдов в спецификации: {len(spec.slides)}")

    if not args.no_images:
        spec = resolve_images(spec, debug=args.debug)

    print()
    for slide in spec.slides:
        meta = slide.image_meta
        tail = f" | {meta.license} | score={meta.score}" if meta else ""
        print(f"{slide.layout:12s} | {slide.title[:38]:40s} | {(slide.image or '-')[:46]}{tail}")

    path = save(spec, args.out)
    images = sum(1 for slide in spec.slides if slide.image)
    print(f"\nготово: {path} (слайдов {len(spec.slides)}, картинок {images})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
