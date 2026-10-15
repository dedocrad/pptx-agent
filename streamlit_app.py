"""Простой веб-интерфейс для LangChain-агента."""

import json
from pathlib import Path

import streamlit as st

from agent.react import run
from llm.client import DEFAULT_AGENT_MODEL


def _presentation_paths(result: dict) -> list[str]:
    paths: list[str] = []
    for step in result.get("steps", []):
        if step.get("kind") != "observation":
            continue
        if step.get("tool") != "create_presentation":
            continue

        try:
            payload = json.loads(step.get("text", ""))
        except json.JSONDecodeError:
            continue

        path = payload.get("path")
        if payload.get("ok") and path and path not in paths:
            paths.append(path)
    return paths


def _model_resource_error(result: dict) -> bool:
    for step in result.get("steps", []):
        if step.get("kind") != "observation":
            continue
        if "insufficient system resources" in step.get("text", "").lower():
            return True
    return False


def main() -> None:
    st.set_page_config(page_title="PPTX Agent", page_icon="📊")
    st.title("PPTX Agent")
    st.write(
        "Попросите агента собрать презентацию или найти информацию. "
        "Если не указать аудиторию или число слайдов, он выберет их сам."
    )

    with st.form("agent_form"):
        task = st.text_area(
            "Задача",
            placeholder="Например: Сделай презентацию про Марс на 5 слайдов",
            height=120,
        )
        model = st.text_input("Модель LM Studio", value=DEFAULT_AGENT_MODEL)
        max_steps = st.slider("Максимум шагов агента", min_value=1, max_value=30, value=12)
        submitted = st.form_submit_button("Запустить агента", type="primary")

    if submitted:
        if not task.strip():
            st.session_state.pop("agent_result", None)
            st.error("Введите задачу для агента.")
        else:
            with st.spinner("Агент работает. Это может занять несколько минут..."):
                result = run(
                    task.strip(),
                    model=model.strip() or None,
                    max_steps=max_steps,
                    verbose=False,
                )
            st.session_state["agent_result"] = result.model_dump()

    result = st.session_state.get("agent_result")
    if not result:
        return

    st.subheader("Ответ")
    if result.get("error"):
        st.error(result["error"])
    if _model_resource_error(result):
        st.warning(
            "LM Studio не смог загрузить выбранную модель из-за нехватки ресурсов. "
            "Выберите модель меньшего размера или квантования и запустите задачу снова."
        )
    if result.get("answer"):
        st.markdown(result["answer"])

    for path in _presentation_paths(result):
        file_path = Path(path)
        if not file_path.is_file() or file_path.suffix.lower() != ".pptx":
            st.warning(f"Файл презентации не найден: {path}")
            continue

        st.download_button(
            label=f"Скачать {file_path.name}",
            data=file_path.read_bytes(),
            file_name=file_path.name,
            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )

    tool_steps = [
        step for step in result.get("steps", [])
        if step.get("kind") in {"tool_call", "observation"}
    ]
    if tool_steps:
        with st.expander("Действия агента"):
            for step in tool_steps:
                if step["kind"] == "tool_call":
                    st.markdown(f"**Инструмент:** `{step.get('tool', '')}`")
                    if step.get("args"):
                        st.json(step["args"])
                else:
                    tool_name = step.get("tool", "")
                    st.markdown(f"**Результат:** `{tool_name}`")
                    try:
                        payload = json.loads(step.get("text", ""))
                    except json.JSONDecodeError:
                        payload = None

                    if tool_name == "create_presentation" and payload:
                        for stage in payload.get("stages", []):
                            status = stage.get("status")
                            icon = {
                                "completed": "✅",
                                "failed": "❌",
                                "skipped": "⏭️",
                            }.get(status, "•")
                            st.markdown(f"{icon} **{stage.get('name', 'Этап')}**")
                            if stage.get("detail"):
                                st.caption(stage["detail"])
                        st.caption(
                            f"Итог: {'успешно' if payload.get('ok') else 'ошибка'}"
                        )
                    else:
                        st.code(step.get("text", ""), language="json")


if __name__ == "__main__":
    main()
