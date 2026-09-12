"""DB-side отсечение хитов поиска по видимости документа.

Defense-in-depth к Qdrant-фильтру `must_not deleted` (Этап 4a.2), единое место
для /chat и /search. Отсекаются хиты:
  - документов, удалённых в БД, но чей payload Qdrant ещё не синхронизирован
    (гонка софт-делита);
  - документов, отсутствующих в БД вовсе: восстановленных во время физической
    очистки корзины (строка БД удаляется первой — pipeline.remove_if_deleted),
    либо мусорных точек-сирот после сбоя финализации.
"""
from __future__ import annotations

from app.services.registry import get_registry


def build_doc_lookup(hits) -> dict:
    """doc_id → visibility metadata (или None) for all hits in one query."""
    reg = get_registry()
    ids = {h.payload.get("doc_id", "") for h in hits}
    ids.discard("")
    return reg.get_visibility_many(ids)


def drop_invisible_hits(hits, doc_lookup: dict) -> list:
    """Оставляет только хиты документов, видимых в активной базе."""
    return [
        h
        for h in hits
        if (doc := doc_lookup.get(h.payload.get("doc_id", ""))) is not None
        and not doc.get("deleted_at")
    ]
