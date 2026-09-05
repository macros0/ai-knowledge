"""Хранилище финальных чанков в реляционной БД (Этап 2b: PostgreSQL SSOT).

Полный текст чанка, заголовок секции, порядок и хэш — канонически в БД
(document_chunks); Qdrant хранит только slim-точку и гидрирует content отсюда
по (doc_id, chunk_index). Сырые chunk_XX.md остаются в FS лишь как артефакт
бандла/экспорта, а не как рабочее состояние поиска.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, tuple_

from app.db.models import DocumentChunk
from app.db.session import session_scope

logger = logging.getLogger(__name__)

# Идентификатор точки-чанка в payload Qdrant (поиск гидрирует content отсюда).
CHUNK_POINT_TYPE = "chunk"


def replace_chunks(session, doc_id: str, rows: list[dict]) -> None:
    """Заменяет чанки документа целиком (delete + insert) в переданной сессии.

    rows: [{chunk_index, section_title, content, content_hash, char_count}, ...].
    Commit выполняет вызывающий — финализация собирает чанки + концепты +
    вложения в одну транзакцию (session_scope), затем идёт Qdrant.
    """
    session.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).delete(
        synchronize_session=False
    )
    for r in rows:
        session.add(
            DocumentChunk(
                doc_id=doc_id,
                chunk_index=int(r["chunk_index"]),
                section_title=r.get("section_title"),
                content=r.get("content") or "",
                content_hash=r.get("content_hash"),
                char_count=int(r.get("char_count", 0)),
            )
        )


def fetch_chunk_contents(pairs: list[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    """Батч-загрузка полного content + section_title чанков по (doc_id, chunk_index).

    Возвращает {(doc_id, chunk_index): {"content": ..., "section_title": ...}}.
    Канонический ключ гидрации — natural key (doc_id, chunk_index), стабильный
    при replace (surrogate id в payload Qdrant не хранится).
    """
    if not pairs:
        return {}
    with session_scope() as s:
        rows = s.execute(
            select(
                DocumentChunk.doc_id,
                DocumentChunk.chunk_index,
                DocumentChunk.content,
                DocumentChunk.section_title,
            ).where(tuple_(DocumentChunk.doc_id, DocumentChunk.chunk_index).in_(pairs))
        ).all()
    return {
        (doc_id, int(chunk_index)): {
            "content": content,
            "section_title": section_title or "",
        }
        for doc_id, chunk_index, content, section_title in rows
    }


def enrich_chunk_hits(hits: list) -> list:
    """Подставляет полный content/section_title чанков в payload хитов (по natural key).

    Зеркалит concept_store.enrich_concept_hits: после векторного поиска payload
    чанк-точки slim (без content) — полный текст достаётся из document_chunks.
    Транзиентный fallback: если строки в БД нет (недогруженный корпус), оставляем
    прежний content из payload + WARNING-лог — удаляется в Фазе 5, чтобы не
    маскировать рассинхрон БД и Qdrant. Мутирует payload на месте, возвращает hits.
    """
    pairs: list[tuple[str, int]] = []
    for h in hits:
        if h.payload.get("point_type") == CHUNK_POINT_TYPE:
            doc_id = h.payload.get("doc_id", "")
            ci = h.payload.get("chunk_index")
            if doc_id and ci is not None:
                pairs.append((doc_id, int(ci)))
    contents = fetch_chunk_contents(pairs)
    for h in hits:
        if h.payload.get("point_type") != CHUNK_POINT_TYPE:
            continue
        doc_id = h.payload.get("doc_id", "")
        ci = h.payload.get("chunk_index")
        key = (doc_id, int(ci)) if ci is not None else None
        if key in contents:
            h.payload["content"] = contents[key]["content"]
            h.payload["section_title"] = contents[key]["section_title"]
        else:
            # Фаза 5: fallback'а на payload больше нет (slim). Расхождение БД↔Qdrant
            # логируется, но не маскируется — дефект данных, а не путь выдачи.
            logger.warning(
                "Чанк (%s, %s) не найден в document_chunks — рассинхрон БД и Qdrant",
                doc_id, ci,
            )
    return hits
