"""Модели LangChain, подключённые к локальному LM Studio.

LM Studio отдаёт OpenAI-совместимый API на http://127.0.0.1:1234/v1,
поэтому используем обычный ChatOpenAI — никаких прокси и ключей.
"""

import os

from langchain_openai import ChatOpenAI

LM_STUDIO_BASE_URL = os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
DEFAULT_MODEL = os.environ.get("PPTX_LLM_MODEL", "google/gemma-4-e4b")


def get_chat_model(model: str | None = None,
                   temperature: float = 0.2,
                   max_tokens: int = 2048) -> ChatOpenAI:
    """Chat-модель для агента.

    temperature низкая: агент должен выбирать инструменты, а не фантазировать.
    max_tokens большой: gemma-4-e4b — reasoning-модель, часть бюджета уходит
    на «размышления» до самого ответа или вызова инструмента.
    """
    return ChatOpenAI(
        model=model or DEFAULT_MODEL,
        base_url=LM_STUDIO_BASE_URL,
        api_key=os.environ.get("LM_STUDIO_API_KEY", "lm-studio"),
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=300,
        max_retries=1,
    )


def available_models() -> list[str]:
    """Список моделей, загруженных в LM Studio (для CLI и диагностики)."""
    from tools._http import get_json
    data = get_json(f"{LM_STUDIO_BASE_URL}/models")
    if not data:
        return []
    return [item.get("id", "") for item in (data.get("data") or []) if item.get("id")]
