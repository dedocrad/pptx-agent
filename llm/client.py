import json
import os
import re

import requests
from langchain_openai import ChatOpenAI

LM_STUDIO_BASE_URL = os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
LM_STUDIO_URL = f"{LM_STUDIO_BASE_URL}/chat/completions"
DEFAULT_MODEL = "google/gemma-4-e2b"
DEFAULT_AGENT_MODEL = os.environ.get("PPTX_LLM_MODEL", "google/gemma-4-e4b")


def get_chat_model(
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> ChatOpenAI:
    """Создать модель LangChain для LM Studio."""
    return ChatOpenAI(
        model=model or DEFAULT_AGENT_MODEL,
        base_url=LM_STUDIO_BASE_URL,
        api_key=os.environ.get("LM_STUDIO_API_KEY", "lm-studio"),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=300,
        max_retries=1,
    )


def available_models() -> list[str]:
    """Вернуть модели, доступные в LM Studio."""
    from tools._http import get_json

    data = get_json(f"{LM_STUDIO_BASE_URL}/models")
    if not data:
        return []
    return [item["id"] for item in data.get("data", []) if item.get("id")]


def chat(
    messages: list[dict],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.4,
    json_mode: bool = False,
    max_tokens: int = 2048,
    response_format: dict | None = None,
) -> str:
    """Один вызов локальной модели в LM Studio (OpenAI-совместимый API).

    json_mode=True — просьба отвечать JSON. В LM Studio это `{"type": "text"}`:
    значения `json_object` он не принимает (400), а строгую схему задают
    через response_format={"type": "json_schema", ...}.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if response_format is not None:
        payload["response_format"] = response_format
    elif json_mode:
        payload["response_format"] = {"type": "text"}

    r = requests.post(LM_STUDIO_URL, json=payload, timeout=300)

    if r.status_code != 200:
        raise RuntimeError(f"LM Studio {r.status_code}: {r.text}")

    return r.json()["choices"][0]["message"]["content"] or ""


def extract_json_object(text: str) -> dict | None:
    """Достать JSON-объект из ответа модели.

    Локальные модели любят обрамлять JSON в ```-заборы или болтать вокруг него,
    поэтому сначала пробуем распарсить всё, потом ищем первый {...}.
    """
    text = (text or "").strip()
    if not text:
        return None

    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]

    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)      # жадный поиск первой скобки
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def chat_json(messages: list[dict],
              model: str = DEFAULT_MODEL,
              temperature: float = 0.2,
              retries: int = 1,
              max_tokens: int = 1024,
              schema: dict | None = None) -> dict | None:
    """chat() + разбор JSON. None, если модель так и не выдала валидный объект.

    Если передана JSON-схема, просим LM Studio о структурированном выводе —
    тогда ответ почти гарантированно парсится.
    """
    response_format = None
    if schema is not None:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "result", "schema": schema},
        }

    for _ in range(retries + 1):
        try:
            raw = chat(messages, model=model, temperature=temperature,
                       json_mode=schema is None, max_tokens=max_tokens,
                       response_format=response_format)
        except RuntimeError:
            return None
        data = extract_json_object(raw)
        if data is not None:
            return data
        # один повтор с более прямой подсказкой
        messages = messages + [{
            "role": "user",
            "content": "Ответ должен быть ТОЛЬКО валидным JSON-объектом, без пояснений.",
        }]
    return None
