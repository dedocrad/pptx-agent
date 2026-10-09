import json
from pydantic import ValidationError

from llm.client import chat
from pptx_engine.spec import PresentationSpec
from llm.prompts import SYSTEM_PROMPT, FEWSHOT_USER, FEWSHOT_ASSISTANT


def _messages(user_request: str, prev_error: str | None = None) -> list[dict]:
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": FEWSHOT_USER},
        {"role": "assistant", "content": FEWSHOT_ASSISTANT},
        {"role": "user", "content": user_request},
    ]
    if prev_error:
        msgs.append({
            "role": "user",
            "content": (
                "Your previous answer failed validation with error:\n"
                f"{prev_error}\n"
                "Return ONLY corrected JSON that matches the schema."
            ),
        })
    return msgs


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # убираем ```json ... ```
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def generate_spec(user_request: str,
                  model: str = "google/gemma-4-e4b",
                  max_retries: int = 2) -> PresentationSpec:
    last_error = None
    for attempt in range(max_retries + 1):
        raw = chat(_messages(user_request, last_error), model=model)
        raw = _strip_code_fences(raw)
        try:
            data = json.loads(raw)
            return PresentationSpec.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            last_error = str(e)[:500]
            print(f"[attempt {attempt}] validation failed: {last_error}")
    raise RuntimeError(f"Gemma failed to produce valid spec: {last_error}")