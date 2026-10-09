#!/usr/bin/env python
"""CLI для ReAct-агента.

Примеры:
    # справка по теме (без файла)
    python scripts/agent_demo.py "Собери материал и картинку про Солнечную систему"

    # агент делает ГОТОВУЮ презентацию
    python scripts/agent_demo.py --out solar.pptx "Сделай презентацию про Солнечную систему, 6 слайдов"

    python scripts/agent_demo.py --mode text --model google/gemma-3-1b "Что такое блокчейн?"
    python scripts/agent_demo.py --list-models
    python scripts/agent_demo.py --tools wiki_search,wiki_page "История Рима"
"""

import argparse
import json
import logging
import pathlib
import sys
import warnings

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

logging.disable(logging.WARNING)
warnings.filterwarnings("ignore")

from agent.llm import DEFAULT_MODEL, available_models     # noqa: E402
from agent.react import run                              # noqa: E402
from tools.langchain_tools import TOOL_NAMES, get_tools  # noqa: E402

DEFAULT_TASK = "Собери 4 тезиса про Солнечную систему и подбери картинку для слайда."
DECK_TOOLS = {"create_presentation", "render_deck"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ReAct-агент для презентаций")
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK, help="задача агенту")
    parser.add_argument("--mode", choices=["auto", "native", "text"], default="auto",
                        help="auto: function calling, при неудаче текстовый ReAct")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="модель в LM Studio")
    parser.add_argument("--max-steps", type=int, default=12, help="лимит шагов")
    parser.add_argument("--tools", default="", help="через запятую: " + ",".join(TOOL_NAMES))
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

    tools = get_tools([t.strip() for t in args.tools.split(",") if t.strip()])
    print(f"модель: {args.model} | режим: {args.mode} | тулз: {len(tools)} | шагов: {args.max_steps}")
    print(f"задача: {task}\n")

    result = run(task, mode=args.mode, model=args.model, tools=tools,
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
        print(f"\nфайл {args.out} агент так и не собрал — можно сделать без агента:")
        print(f'  .venv/bin/python scripts/make_deck.py "{args.task}" --out {args.out}')

    if args.trace:
        pathlib.Path(args.trace).write_text(
            json.dumps(result.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nтрейс сохранён: {args.trace}")

    return 0 if result.ok else 1


def _deck_paths(result) -> list[str]:
    """Вытащить пути к .pptx из наблюдений агента (create_presentation / render_deck)."""
    paths: list[str] = []
    for step in result.steps:
        if step.kind != "observation" or step.tool not in DECK_TOOLS:
            continue
        try:
            payload = json.loads(step.text)
        except (json.JSONDecodeError, TypeError):
            continue
        path = payload.get("path")
        if path and path not in paths:
            paths.append(path)
    return paths


if __name__ == "__main__":
    raise SystemExit(main())
