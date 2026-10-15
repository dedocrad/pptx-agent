"""LangChain-агент для работы с инструментами проекта."""

import json
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from pydantic import BaseModel, Field

from llm.client import DEFAULT_AGENT_MODEL as DEFAULT_MODEL, get_chat_model
from agent.tools import ALL_TOOLS

DEFAULT_MAX_STEPS = 12
TOOL_OBSERVATION_LIMIT = 1500

SYSTEM_PROMPT = """Ты — исследовательский агент, который готовит презентации PowerPoint.

Не превращай задачу в интервью и не задавай уточняющих вопросов о теме, аудитории,
количестве слайдов или деталях. Если этих требований нет, выбери сам понятный вариант
для широкой аудитории и продолжай работу.

Если пользователь просит презентацию, не обещай сделать её позже: вызови инструмент
создания прямо сейчас и используй тему из запроса.
Если число слайдов не указано, сделай 5.

Если пользователь не просит презентацию, ответь кратко на вопрос. В этом режиме у тебя
нет инструментов для веб-поиска, поэтому не утверждай, что проверял информацию в интернете.
Не выдумывай ссылки и лицензии. Для картинок используй только автоматический подбор.
Если LM Studio сообщает, что не хватает ресурсов для загрузки модели, не утверждай,
что презентация создана. Объясни ошибку и предложи выбрать в интерфейсе модель меньшего
размера или квантования.
После выполнения кратко ответь по-русски и укажи созданный файл, если он есть.
"""


class AgentStep(BaseModel):
    """Один шаг агента."""

    kind: Literal["reasoning", "thought", "tool_call", "observation", "final", "error"]
    text: str = ""
    tool: str = ""
    args: dict = Field(default_factory=dict)


class AgentRun(BaseModel):
    """Ответ агента и список выполненных шагов."""

    task: str
    answer: str = ""
    steps: list[AgentStep] = Field(default_factory=list)
    model: str = ""
    mode: str = "native"
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.answer) and not self.error


def build_agent(
    tools: list | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
):
    """Создать LangChain-агента с инструментами."""
    from langchain.agents import create_agent

    return create_agent(
        model=get_chat_model(model),
        tools=ALL_TOOLS if tools is None else tools,
        system_prompt=system_prompt or SYSTEM_PROMPT,
    )


def run_agent(
    task: str,
    *,
    model: str | None = None,
    tools: list | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    verbose: bool = True,
    system_prompt: str | None = None,
) -> AgentRun:
    """Запустить агента и вернуть ответ вместе с шагами."""
    result = AgentRun(task=task, model=model or DEFAULT_MODEL)

    try:
        agent = build_agent(tools=tools, model=model, system_prompt=system_prompt)
        state = agent.invoke(
            {"messages": [{"role": "user", "content": task}]},
            config={
                "recursion_limit": max(4, max_steps * 2),
                "configurable": {"model": model or DEFAULT_MODEL},
            },
        )
        messages = state.get("messages", [])

        for message in messages:
            for step in _message_to_steps(message):
                result.steps.append(step)
                _print_step(step, verbose)

        result.answer = _final_answer(messages)
        if not result.answer:
            result.error = "агент не вернул финальный ответ"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        error_step = AgentStep(kind="error", text=result.error)
        result.steps.append(error_step)
        _print_step(error_step, verbose)

    return result


def run(task: str, **kwargs) -> AgentRun:
    """Запустить LangChain-агента."""
    return run_agent(task, **kwargs)


def _message_to_steps(message: BaseMessage) -> list[AgentStep]:
    if isinstance(message, ToolMessage):
        return [
            AgentStep(
                kind="observation",
                tool=message.name or "",
                text=_as_text(message.content)[:TOOL_OBSERVATION_LIMIT],
            )
        ]

    if not isinstance(message, AIMessage):
        return []

    steps = []
    reasoning = (message.additional_kwargs or {}).get("reasoning_content")
    if reasoning:
        steps.append(AgentStep(kind="reasoning", text=_as_text(reasoning)[:1200]))

    content = _as_text(message.content)
    if content:
        kind = "thought" if message.tool_calls else "final"
        steps.append(AgentStep(kind=kind, text=content[:2000]))

    for call in message.tool_calls:
        args = call.get("args") or {}
        steps.append(
            AgentStep(
                kind="tool_call",
                tool=call.get("name", ""),
                args=args,
                text=_short_json(args),
            )
        )

    return steps


def _final_answer(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not message.tool_calls:
            answer = _as_text(message.content).strip()
            if answer:
                return answer
    return ""


def _as_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return " ".join(part for part in parts if part)
    return "" if content is None else str(content)


def _short_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)[:400]


def _print_step(step: AgentStep, verbose: bool) -> None:
    if not verbose:
        return

    if step.kind == "tool_call":
        print(f"🔧 {step.tool}({step.text})")
        return

    prefix = {
        "reasoning": "💭",
        "thought": "🤔",
        "observation": "👀",
        "final": "✅",
        "error": "❌",
    }.get(step.kind, step.kind)
    suffix = f" [{step.tool}]" if step.kind == "observation" and step.tool else ""
    text = step.text.replace("\n", " ")
    print(f"{prefix}{suffix}: {text[:400]}")
