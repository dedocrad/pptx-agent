#!/usr/bin/env python
"""Живой прогон всех тулзов агента: python scripts/smoke_tools.py

Скрипт ходит в интернет (Wikimedia, Википедия, поиск) и в локальный LM Studio,
поэтому нужен запущенный LM Studio с моделью для эмбеддингов/судьи.
"""

import json
import logging
import pathlib
import sys
import warnings

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

logging.disable(logging.WARNING)
warnings.filterwarnings("ignore")

from tools.langchain_tools import ALL_TOOLS, get_tool          # noqa: E402


def _short(value: str, limit: int = 320) -> str:
    value = " ".join(str(value).split())
    return value if len(value) <= limit else value[:limit] + "…"


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def call(name: str, **kwargs) -> str:
        tool = get_tool(name)
        print(f"\n=== {name}({json.dumps(kwargs, ensure_ascii=False)})")
        try:
            output = str(tool.invoke(kwargs))
        except Exception as exc:                                  # noqa: BLE001
            output = f"ERROR {type(exc).__name__}: {exc}"
        ok = not output.startswith("ERROR") and '"found": 0' not in output
        results.append((name, ok, _short(output, 120)))
        print(_short(output))
        return output

    call("web_search", query="transformer neural network architecture", limit=3)
    call("wiki_search", query="Солнечная система", limit=3)
    call("wiki_page", title="Solar System")
    call("fetch_page", url="https://en.wikipedia.org/wiki/Transformer_(deep_learning)")

    images_raw = call("search_images", query="solar system planets",
                      slide_title="Солнечная система", max_candidates=4)

    pageid = _first_pageid(images_raw)
    downloaded_raw = ""
    if pageid is not None:
        downloaded_raw = call("download_image", pageid=pageid, query="solar system planets")

    path = _json_value(downloaded_raw, "path")
    if path:
        call("validate_image", path=path)

    call("resolve_slide_image", query="quantum computer chip",
         slide_title="Квантовые вычисления")

    print("\n" + "=" * 60)
    for name, ok, note in results:
        print(f"{'OK  ' if ok else 'FAIL'} {name:22s} {note}")
    failures = [name for name, ok, _ in results if not ok]
    print(f"\nитого: {len(results) - len(failures)}/{len(results)} успешно")
    if failures:
        print("не сработало:", ", ".join(failures))
    return 1 if failures else 0


def _first_pageid(raw: str):
    try:
        candidates = json.loads(raw).get("candidates") or []
        return candidates[0]["pageid"] if candidates else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _json_value(raw: str, key: str):
    try:
        return json.loads(raw).get(key)
    except (json.JSONDecodeError, TypeError):
        return None


if __name__ == "__main__":
    print("Тулзы в реестре:", ", ".join(t.name for t in ALL_TOOLS))
    raise SystemExit(main())
