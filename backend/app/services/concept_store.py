"""Хранилище OKF-концептов в реляционной БД (canonical, полный текст).

Раньше полный текст концепта терялся: и `.md`-бандл, и payload Qdrant обрезали
content до okf_max_concept_chars. Теперь canonical-копия (без обрезки) — таблица
okf_concepts; `.md`-бандлы остаются как backup/inspect (MIGRATION_PLAN.md §3.2),
payload Qdrant становится slim (без content) — полный текст достаётся отсюда
по (doc_id, slug) после поиска.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select, tuple_

from app.db.models import OkfConcept
from app.db.session import session_scope


def replace_concepts(doc_id: str, okf_docs: list) -> None:
    """Заменяет концепты документа целиком (delete + insert), сохраняя полный текст.

    Вызывается при финализации пайплайна. slug берётся из имени файла без .md
    (stem) — совпадает со staging-слагами и полем slug в payload Qdrant.
    """
    with session_scope() as s:
        s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).delete(synchronize_session=False)
        for d in okf_docs:
            meta = d.metadata or {}
            s.add(
                OkfConcept(
                    doc_id=doc_id,
                    slug=Path(d.filepath).stem,
                    title=meta.get("title", ""),
                    type=meta.get("type", "concept"),
                    tags=list(meta.get("tags", []) or []),
                    content=d.content or "",
                    relations=list(meta.get("relations", []) or []),
                    chunk_index=meta.get("chunk_index"),
                )
            )


def fetch_contents(doc_slug_pairs: list[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """Батч-загрузка полного content концептов по (doc_id, slug).

    Возвращает {(doc_id, slug): content}. Используется после векторного поиска,
    чтобы подставить полный текст концепта вместо slim payload.
    """
    if not doc_slug_pairs:
        return {}
    with session_scope() as s:
        rows = s.execute(
            select(OkfConcept.doc_id, OkfConcept.slug, OkfConcept.content).where(
                tuple_(OkfConcept.doc_id, OkfConcept.slug).in_(doc_slug_pairs)
            )
        ).all()
    return {(doc_id, slug): content for doc_id, slug, content in rows}


def enrich_concept_hits(hits: list) -> list:
    """Подставляет полный content концептов в payload хитов (по doc_id+slug).

    slug берётся из filepath (stem), чтобы не зависеть от формата поля slug в
    payload (у старых точек мог быть filename с .md). Чанковые точки
    (point_type="chunk") сохраняют content в payload — их не трогаем.
    Возвращает тот же список hits (мутация payload на месте).
    """
    pairs: list[tuple[str, str]] = []
    for h in hits:
        if h.payload.get("point_type") == "concept":
            doc_id = h.payload.get("doc_id", "")
            slug = Path(h.payload.get("filepath", "")).stem
            if doc_id and slug:
                pairs.append((doc_id, slug))
    contents = fetch_contents(pairs)
    for h in hits:
        if h.payload.get("point_type") == "concept":
            doc_id = h.payload.get("doc_id", "")
            slug = Path(h.payload.get("filepath", "")).stem
            key = (doc_id, slug)
            if key in contents:
                h.payload["content"] = contents[key]
    return hits
