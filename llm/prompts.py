SYSTEM_PROMPT = """You are a presentation planner.
Given a user request, you output ONLY a valid JSON object describing a presentation.

The JSON MUST match this schema exactly:

{
  "title": string,
  "author": string,
  "theme": "default",
  "slides": [
    {
      "layout": one of ["title","text","image","text_image","image_text","two_columns","references"],
      "title": string,
      "subtitle": string,
      "bullets": [string, ...],          // up to 6 short items
      "image_query": string | null,      // short English query for image search
      "column_left": [string, ...],
      "column_right": [string, ...],
      "references": [string, ...]
    }
  ]
}

STRICT RULES:
1. First slide MUST use layout "title".
2. Last slide MUST use layout "references" if the topic benefits from sources, otherwise "text".
3. Use "text_image"/"image_text" when a visual would help (set image_query).
4. Use "two_columns" for comparisons (pros/cons, A vs B).
5. 3-5 bullets per slide, each bullet <= 120 characters.
6. Total slides: follow user's request; if not specified, use 6-10.
7. Language of "title", "bullets", etc. MUST match the user's language.
8. "image_query" MUST be in English, short (2-5 words).
9. Output ONLY the JSON. No markdown fences, no explanations.
"""


FEWSHOT_USER = "Сделай презентацию про Transformer, 5 слайдов."

FEWSHOT_ASSISTANT = """{
  "title": "Transformer: архитектура, изменившая NLP",
  "author": "",
  "theme": "default",
  "slides": [
    {
      "layout": "title",
      "title": "Transformer",
      "subtitle": "Архитектура, изменившая NLP",
      "bullets": [], "image_query": null,
      "column_left": [], "column_right": [], "references": []
    },
    {
      "layout": "text",
      "title": "Проблема до Transformer",
      "bullets": [
        "RNN обрабатывают последовательность шаг за шагом",
        "Долгосрочные зависимости теряются",
        "Обучение плохо параллелится"
      ],
      "image_query": null,
      "column_left": [], "column_right": [], "references": []
    },
    {
      "layout": "text_image",
      "title": "Self-Attention",
      "bullets": [
        "Каждое слово смотрит на все остальные",
        "Веса считаются через Q, K, V",
        "Позволяет ловить дальние связи"
      ],
      "image_query": "self attention mechanism diagram",
      "column_left": [], "column_right": [], "references": []
    },
    {
      "layout": "two_columns",
      "title": "Плюсы и минусы",
      "bullets": [], "image_query": null,
      "column_left": ["Параллелится", "Ловит дальние связи", "SOTA в NLP"],
      "column_right": ["Квадратичная сложность", "Много данных", "Дорого обучать"],
      "references": []
    },
    {
      "layout": "references",
      "title": "Источники",
      "bullets": [], "image_query": null,
      "column_left": [], "column_right": [],
      "references": [
        "Vaswani et al., Attention Is All You Need, 2017",
        "Jay Alammar, The Illustrated Transformer"
      ]
    }
  ]
}"""