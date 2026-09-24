"""Хранилище OKF-концептов в реляционной БД (canonical, полный текст).

Раньше полный текст концепта терялся: и `.md`-бандл, и payload Qdrant обрезали
content до okf_max_concept_chars. Теперь canonical-копия (без обрезки) — таблица
okf_concepts; `.md`-бандлы остаются как backup/inspect (MIGRATION_PLAN.md §3.2),
payload Qdrant становится slim (без content) — полный текст достаётся отсюда
по (doc_id, slug) после поиска.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from sqlalchemy import select, tuple_

from app.db.models import OkfConcept
from app.db.session import session_scope


def _parse_iso(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def replace_concepts(session, doc_id: str, okf_docs: list) -> None:
    """Заменяет концепты документа целиком (delete + insert), сохраняя полный текст.

    Работает в ПЕРЕДАННОЙ сессии без commit — финализация пайплайна собирает
    чанки + концепты + вложения в одну транзакцию (session_scope у вызывающего).
    slug берётся из имени файла без .md (stem) — совпадает со staging-слагами и
    полем slug в payload Qdrant. Provenance (generated_at/model_id/prompt_version)
    читается из metadata, куда финализация кладёт её из staging chunks_data.
    """
    session.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).delete(synchronize_session=False)
    for d in okf_docs:
        meta = d.metadata or {}
        session.add(
            OkfConcept(
                doc_id=doc_id,
                slug=Path(d.filepath).stem,
                title=meta.get("title", ""),
                type=meta.get("type", "concept"),
                tags=list(meta.get("tags", []) or []),
                content=d.content or "",
                relations=list(meta.get("relations", []) or []),
                chunk_index=meta.get("chunk_index"),
                source_spans=list(meta.get("source_spans") or []) or None,
                generated_at=_parse_iso(meta.get("generated_at")),
                model_id=meta.get("model_id"),
                prompt_version=meta.get("prompt_version"),
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


def _slug_of(hit) -> str:
    """slug концепта из payload: новое поле `slug` (Фаза 4) с fallback на
    stem filepath (legacy-точки старой коллекции)."""
    slug = hit.payload.get("slug")
    if slug:
        return str(slug)
    return Path(hit.payload.get("filepath", "")).stem


def enrich_concept_hits(hits: list) -> list:
    """Подставляет полный content концептов в payload хитов (по doc_id+slug).

    Фаза 4: payload концепта больше не несёт `filepath` — slug берётся из поля
    `slug` (fallback на stem filepath для legacy-точек), а `filepath`
    синтезируется для downstream (context_builder/chat/search). Чанковые точки
    (point_type="chunk") не трогаем — их content гидрирует chunk_store.
    Возвращает тот же список hits (мутация payload на месте).
    """
    pairs: list[tuple[str, str]] = []
    for h in hits:
        if h.payload.get("point_type") == "concept":
            doc_id = h.payload.get("doc_id", "")
            slug = _slug_of(h)
            if doc_id and slug:
                pairs.append((doc_id, slug))
    contents = fetch_contents(pairs)
    for h in hits:
        if h.payload.get("point_type") != "concept":
            continue
        doc_id = h.payload.get("doc_id", "")
        slug = _slug_of(h)
        key = (doc_id, slug)
        if key in contents:
            h.payload["content"] = contents[key]
        if not h.payload.get("filepath"):
            h.payload["filepath"] = f"{doc_id}/{slug}.md"
    return hits
