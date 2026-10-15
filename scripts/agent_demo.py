#!/usr/bin/env python
"""CLI для агента презентаций.

Примеры:
    # агент делает ГОТОВУЮ презентацию
    python scripts/agent_demo.py --out solar.pptx "Сделай презентацию про Солнечную систему, 6 слайдов"

    python scripts/agent_demo.py --list-models
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from llm.client import DEFAULT_AGENT_MODEL as DEFAULT_MODEL, available_models  # noqa: E402
from agent.react import run                              # noqa: E402
from agent.tools import ALL_TOOLS                            # noqa: E402

DEFAULT_TASK = "Сделай презентацию про Солнечную систему на 5 слайдов."
DECK_TOOL = "create_presentation"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LangChain-агент для презентаций")
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK, help="задача агенту")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="модель в LM Studio")
    parser.add_argument("--max-steps", type=int, default=12, help="лимит шагов")
    parser.add_argument("--out", default="", help="имя файла .pptx: агент попросит сохранить именно его")
    parser.add_argument("--quiet", action="store_true", help="не печатать шаги")
    parser.add_argument("--trace", default="", help="куда сохранить JSON с шагами")
    parser.add_argument("--list-models", action="store_true", help="показать модели LM Studio")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.list_models:
        models = available_models()
        print("Модели в LM Studio:" if models else "LM Studio недоступен или пуст")
        for name in models:
            print(" -", name)
        return 0

    task = args.task
    if args.out:
        task = f"{task}\nГотовую презентацию сохрани в файл {args.out}."

    print(f"модель: {args.model} | инструментов: {len(ALL_TOOLS)} | шагов: {args.max_steps}")
    print(f"задача: {task}\n")

    result = run(task, model=args.model,
                 max_steps=args.max_steps, verbose=not args.quiet)

    print("\n" + "=" * 60)
    print("РЕЖИМ:", result.mode, "| ШАГОВ:", len(result.steps), "| ОШИБКА:", result.error or "нет")
    print("=" * 60)
    print(result.answer or "(агент не дал финального ответа)")

    deck_paths = _deck_paths(result)
    if deck_paths:
        print("\nСОЗДАННЫЕ ФАЙЛЫ:")
        for path in deck_paths:
            exists = "есть на диске" if pathlib.Path(path).exists() else "ФАЙЛА НЕТ"
            print(f"  {path}  [{exists}]")
    elif args.out:
        print(f"\nагент не создал файл {args.out}")

    if args.trace:
        pathlib.Path(args.trace).write_text(
            json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nтрейс сохранён: {args.trace}")

    return 0 if result.ok else 1


def _deck_paths(result) -> list[str]:
    """Вытащить пути к презентациям из результата агента."""
    paths: list[str] = []
    for step in result.steps:
        if step.kind != "observation" or step.tool != DECK_TOOL:
            continue
        try:
            payload = json.loads(step.text)
        except (json.JSONDecodeError, TypeError):
            continue
        path = payload.get("path") if payload.get("ok") else None
        if path and path not in paths:
            paths.append(path)
    return paths


if __name__ == "__main__":
    raise SystemExit(main())
