"""Реэкспорт спецификации презентации.

Единственный источник правды — `pptx_engine/spec.py`. Этот модуль оставлен, чтобы:

  * не переписывать импорты в `llm/`, `tools/` и `pipeline/` (они исторически
    берут модель отсюда);
  * у кода, который уже импортирует `llm.schema`, ничего не сломалось.

Это ровно те же объекты классов, а не копии: `llm.schema.SlideSpec is pptx_engine.spec.SlideSpec`.
Новый код может импортировать напрямую: `from pptx_engine.spec import SlideSpec`.
"""

from pptx_engine.spec import ImageMeta, Layout, PresentationSpec, SlideSpec

__all__ = ["ImageMeta", "Layout", "PresentationSpec", "SlideSpec"]
