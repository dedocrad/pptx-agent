"""ReAct-агент: думает → выбирает тулзу → смотрит результат → отвечает.

Два режима, потому что локальные модели бывают очень разными:

  * NATIVE  — модель сама умеет function calling (gemma-4-e4b умеет).
              Собираем граф через LangChain/LangGraph: create_agent(...).
              Модель возвращает tool_calls, фреймворк сам их выполняет.

  * TEXT    — модель НЕ умеет function calling (маленькие модели).
              Тогда ведём классический текстовый ReAct-цикл:
              Thought → Action → Action Input → Observation → ... → Final Answer.
              Тулзы те же самые, просто схема аргументов уходит в промпт текстом.

Публичное API:
    run_agent(task, ...)      — только нативный цикл
    run_text_react(task, ...) — только текстовый цикл
    run(task, mode="auto")    — нативный, а при неудаче автоматически текстовый
"""

import json
import re
import textwrap
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, Field

from agent.llm import DEFAULT_MODEL, get_chat_model
from llm.client import chat as raw_chat
from tools.langchain_tools import ALL_TOOLS, get_tool

TOOL_OBSERVATION_LIMIT = 1500          # сколько символов наблюдения отдаём модели
DEFAULT_MAX_STEPS = 12


# ---------------------------------------------------------------------------
# структуры результата
# ---------------------------------------------------------------------------

class AgentStep(BaseModel):
    """Один шаг цикла — для логов, отладки и показа пользователю."""

    kind: Literal["reasoning", "thought", "tool_call", "observation", "final", "error"]
    text: str = ""
    tool: str = ""
    args: dict = Field(default_factory=dict)


class AgentRun(BaseModel):
    """Итог работы агента: ответ + полный след шагов."""

    task: str
    answer: str = ""
    steps: list[AgentStep] = Field(default_factory=list)
    model: str = ""
    mode: str = ""                     # "native" | "text-react"
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.answer) and not self.error


# ---------------------------------------------------------------------------
# системный промпт
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Ты — исследовательский агент, который готовит презентации PowerPoint.

Твои инструменты:
  * web_search / wiki_search / wiki_page / fetch_page — найти и прочитать текстовую информацию;
  * search_images — найти картинки в Wikimedia Commons и увидеть оценку их качества;
  * download_image — скачать выбранного кандидата;
  * validate_image — проверить уже скачанный файл;
  * resolve_slide_image — получить готовую картинку одним вызовом;
  * create_presentation — собрать готовый файл .pptx по теме;
  * render_deck — собрать .pptx из JSON-спецификации, которую ты написал сам.

Правила:
1. Сначала собери факты (минимум 1-2 источника), потом подбирай картинку.
2. Никогда не выдумывай факты, ссылки и лицензии: если не нашёл — так и напиши.
3. Для картинок: если search_images показывает слабую релевантность (< 5) или
   плохую лицензию — меняй запрос и ищи снова, не тащи мусор в презентацию.
4. Если пользователь просит ПРЕЗЕНТАЦИЮ (файл), то:
   - если факты уже собраны — напиши JSON слайдов сам и вызови render_deck:
     тогда в файл попадёт именно то, что ты нашёл в источниках;
   - если нужен быстрый черновик по теме — create_presentation;
   - без вызова одной из этих тулз презентации не будет, а в финальном ответе
     обязательно укажи путь к созданному файлу.
5. Финальный ответ по-русски:
   - путь к созданному файлу (если делали презентацию);
   - 3-6 тезисов для слайда (короткие, без воды);
   - список источников со ссылками;
   - какие картинки попали в слайды и почему (или что картинок нет).
"""


# ---------------------------------------------------------------------------
# режим 1: нативный function calling через LangChain/LangGraph
# ---------------------------------------------------------------------------

def build_agent(tools: list | None = None,
                model: str | None = None,
                system_prompt: str | None = None):
    """Собрать ReAct-граф. create_agent — современный API LangChain 1.x."""
    tools = tools or ALL_TOOLS
    llm = get_chat_model(model)
    prompt = system_prompt or SYSTEM_PROMPT

    try:
        from langchain.agents import create_agent
        return create_agent(model=llm, tools=tools, system_prompt=prompt)
    except ImportError:
        # запасной путь для старых версий: тот же ReAct, но из langgraph
        from langgraph.prebuilt import create_react_agent
        return create_react_agent(model=llm, tools=tools, prompt=prompt)


def run_agent(task: str,
              *,
              model: str | None = None,
              tools: list | None = None,
              max_steps: int = DEFAULT_MAX_STEPS,
              verbose: bool = True,
              system_prompt: str | None = None) -> AgentRun:
    """Прогнать нативный ReAct-цикл. Исключения не летят наружу — уходят в run.error."""
    run = AgentRun(task=task, model=model or DEFAULT_MODEL, mode="native")
    try:
        agent = build_agent(tools=tools, model=model, system_prompt=system_prompt)
        config = {"recursion_limit": max(4, max_steps * 2)}
        seen = 0
        messages: list[BaseMessage] = []

        for state in agent.stream(
            {"messages": [{"role": "user", "content": task}]},
            config=config,
            stream_mode="values",
        ):
            messages = state["messages"]
            for message in messages[seen:]:
                for step in _message_to_steps(message):
                    run.steps.append(step)
                    _print_step(step, verbose)
            seen = len(messages)

        run.answer = _final_answer(messages)
    except Exception as exc:                      # noqa: BLE001 — агент не должен ронять пайплайн
        run.error = f"{type(exc).__name__}: {exc}"
        run.steps.append(AgentStep(kind="error", text=run.error))
        _print_step(run.steps[-1], verbose)
    return run


def _message_to_steps(message: BaseMessage) -> list[AgentStep]:
    """LangChain-сообщение → шаги нашего формата (у одного AI-сообщения их может быть несколько)."""
    steps: list[AgentStep] = []

    if isinstance(message, ToolMessage):
        name = getattr(message, "name", "") or ""
        steps.append(AgentStep(kind="observation", tool=name,
                               text=_as_text(message.content)[:TOOL_OBSERVATION_LIMIT]))
        return steps

    if isinstance(message, AIMessage):
        reasoning = (message.additional_kwargs or {}).get("reasoning_content")
        if reasoning:
            steps.append(AgentStep(kind="reasoning", text=_as_text(reasoning)[:1200]))
        content = _as_text(message.content)
        if content:
            steps.append(AgentStep(kind="final" if not message.tool_calls else "thought",
                                   text=content[:2000]))
        for call in (message.tool_calls or []):
            steps.append(AgentStep(kind="tool_call", tool=call.get("name", ""),
                                   args=call.get("args", {}) or {},
                                   text=_short_json(call.get("args", {}))))
    return steps


def _final_answer(messages: list[BaseMessage]) -> str:
    """Последний ответ модели, в котором нет вызовов инструментов."""
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            text = _as_text(message.content)
            if text.strip():
                return text.strip()
    return ""


# ---------------------------------------------------------------------------
# режим 2: текстовый ReAct (для моделей без function calling)
# ---------------------------------------------------------------------------

REACT_INSTRUCTIONS = """Отвечай строго в формате ReAct. Одна мысль — одно действие.

Формат:
Thought: коротко, что делаешь и зачем
Action: имя_тулзы
Action Input: JSON с аргументами, например {{"query": "solar system planets"}}

После каждого Action ты получишь строку с результатом:
Observation: ...

Когда данных достаточно, напиши:
Thought: я всё выяснил
Final Answer: итоговый ответ по-русски (тезисы, источники, путь к картинке)

Правила:
* Action Input — ВСЕГДА валидный JSON в одну строку;
* не выдумывай результаты, жди Observation;
* если тулза вернула ошибку, попробуй другой запрос или другую тулзу;
* не больше {max_steps} действий.

Доступные тулзы:
{tools}
"""


def run_text_react(task: str,
                   *,
                   model: str | None = None,
                   tools: list | None = None,
                   max_steps: int = 8,
                   verbose: bool = True) -> AgentRun:
    """Классический текстовый ReAct-цикл поверх тех же LangChain-тулз."""
    tools = tools or ALL_TOOLS
    run = AgentRun(task=task, model=model or DEFAULT_MODEL, mode="text-react")

    messages = [
        {"role": "system",
         "content": REACT_INSTRUCTIONS.format(max_steps=max_steps, tools=_tool_manual(tools))},
        {"role": "user", "content": task},
    ]

    nudges = 0
    for _ in range(max_steps):
        try:
            reply = raw_chat(messages, model=model or DEFAULT_MODEL,
                             temperature=0.2, max_tokens=1500)
        except Exception as exc:                  # noqa: BLE001
            run.error = f"{type(exc).__name__}: {exc}"
            run.steps.append(AgentStep(kind="error", text=run.error))
            _print_step(run.steps[-1], verbose)
            return run

        final = _parse_final_answer(reply)
        if final:
            step = AgentStep(kind="final", text=final)
            run.steps.append(step)
            _print_step(step, verbose)
            run.answer = final
            return run

        action, action_input = _parse_action(reply)
        if not action:
            nudges += 1
            if nudges > 2:
                run.error = "модель не соблюдает формат ReAct (нет ни Action, ни Final Answer)"
                return run
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content":
                             "Не понял ответ. Напиши ровно 'Action: <тулза>' и "
                             "'Action Input: <JSON>' или 'Final Answer: <текст>'."})
            continue

        thought = _parse_thought(reply)
        if thought:
            step = AgentStep(kind="thought", text=thought)
            run.steps.append(step)
            _print_step(step, verbose)

        tool = get_tool(action)
        args = _parse_action_args(action_input, tool)
        call_step = AgentStep(kind="tool_call", tool=action, args=args, text=_short_json(args))
        run.steps.append(call_step)
        _print_step(call_step, verbose)

        observation = _invoke_tool(action, args)
        obs_step = AgentStep(kind="observation", tool=action,
                             text=observation[:TOOL_OBSERVATION_LIMIT])
        run.steps.append(obs_step)
        _print_step(obs_step, verbose)

        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "user",
                         "content": f"Observation: {observation[:TOOL_OBSERVATION_LIMIT]}"})

    run.error = run.error or f"не уложился в {max_steps} шагов"
    return run


def _invoke_tool(name: str, args: dict) -> str:
    """Выполнить тулзу и вернуть строку-наблюдение (ошибки тоже строками)."""
    tool = get_tool(name)
    if tool is None:
        return f"ERROR: нет тулзы с именем {name!r}. Доступны: {[t.name for t in ALL_TOOLS]}"
    try:
        return str(tool.invoke(args))
    except Exception as exc:                      # noqa: BLE001
        return f"ERROR при вызове {name}: {type(exc).__name__}: {exc}"


def _tool_manual(tools: list) -> str:
    """Описание тулз текстом: имя, первые строки docstring, аргументы."""
    blocks = []
    for tool in tools:
        description = textwrap.dedent(tool.description or "").strip()
        first_lines = " ".join(description.split("\n")[:2])
        blocks.append(f"- {tool.name}({_args_help(tool)}): {first_lines}")
    return "\n".join(blocks)


def _args_help(tool) -> str:
    schema = getattr(tool, "args_schema", None)
    if schema is None:
        return ""
    try:
        properties = schema.model_json_schema().get("properties", {})
    except Exception:                             # noqa: BLE001
        return ""
    return ", ".join(properties.keys())


ACTION_RE = re.compile(r"Action\s*:\s*([A-Za-z_][A-Za-z0-9_]*)")
INPUT_RE = re.compile(
    r"Action\s*Input\s*:\s*(.+?)(?=\n\s*(?:Thought|Action|Observation|Final Answer)\s*:|\Z)",
    re.DOTALL,
)
FINAL_RE = re.compile(r"Final\s*Answer\s*:\s*(.+)", re.DOTALL)
THOUGHT_RE = re.compile(r"Thought\s*:\s*(.+?)(?=\n\s*(?:Action|Final Answer)\s*:|\Z)", re.DOTALL)


def _parse_final_answer(reply: str) -> str:
    match = FINAL_RE.search(reply or "")
    return match.group(1).strip() if match else ""


def _parse_thought(reply: str) -> str:
    match = THOUGHT_RE.search(reply or "")
    return match.group(1).strip()[:600] if match else ""


def _parse_action(reply: str) -> tuple[str, str]:
    match = ACTION_RE.search(reply or "")
    if not match:
        return "", ""
    input_match = INPUT_RE.search(reply or "")
    return match.group(1), (input_match.group(1).strip() if input_match else "")


def _parse_action_args(raw: str, tool) -> dict:
    """JSON → dict. Если модель написала просто текст, подставляем его в единственный аргумент."""
    raw = (raw or "").strip().strip("`")
    if raw.startswith("json"):
        raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return {"query": parsed}
    except json.JSONDecodeError:
        pass

    if tool is not None and getattr(tool, "args_schema", None) is not None:
        properties = list(tool.args_schema.model_json_schema().get("properties", {}))
        if "query" in properties:
            return {"query": raw}
        if len(properties) == 1:
            return {properties[0]: raw}
    return {"query": raw}


# ---------------------------------------------------------------------------
# удобный вход: сам выбирает режим
# ---------------------------------------------------------------------------

def run(task: str, *, mode: str = "auto", **kwargs) -> AgentRun:
    """Запустить агента.

    mode="native" — только function calling;
    mode="text"   — только текстовый ReAct;
    mode="auto"   — нативный, а если он упал или не дал ответа, повторить текстовым.
    """
    if mode == "text":
        return run_text_react(task, **kwargs)
    if mode == "native":
        return run_agent(task, **kwargs)

    run_result = run_agent(task, **kwargs)
    if run_result.ok:
        return run_result

    print(f"[agent] нативный режим не сработал ({run_result.error or 'пустой ответ'}) "
          f"→ переключаюсь на текстовый ReAct")
    text_kwargs = {k: v for k, v in kwargs.items() if k in ("model", "tools", "verbose")}
    return run_text_react(task, **text_kwargs)


# ---------------------------------------------------------------------------
# мелочи
# ---------------------------------------------------------------------------

def _as_text(content: Any) -> str:
    """content у LangChain бывает строкой или списком блоков."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return " ".join(p for p in parts if p)
    return "" if content is None else str(content)


def _short_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)[:400]


_ICONS = {
    "reasoning": "💭 reasoning",
    "thought": "🤔 thought",
    "tool_call": "🔧 tool_call",
    "observation": "👀 observation",
    "final": "✅ final",
    "error": "❌ error",
}


def _print_step(step: AgentStep, verbose: bool) -> None:
    if not verbose:
        return
    header = _ICONS.get(step.kind, step.kind)
    if step.kind == "tool_call":
        print(f"{header}: {step.tool}({step.text})")
    else:
        text = step.text.replace("\n", " ")
        suffix = f" [{step.tool}]" if step.tool and step.kind == "observation" else ""
        print(f"{header}{suffix}: {text[:400]}")
