"""Реестр документов на реляционной БД (замена data/documents.json).

Интерфейс совместим с прежним JSON-реестром: те же методы (create/get/list/
update/delete) и та же форма возвращаемых dict (id, filename, status, tags,
created_at/updated_at как datetime). Внутренности — SQLAlchemy-сессии поверх
PostgreSQL (prod) / SQLite (dev). Отличие: нет in-memory словаря и полной
перезаписи файла — каждая операция в отдельной транзакции.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import (
    Document,
    DocumentStaging,
    DocumentTag,
    OkfAttachment,
    OkfConcept,
)
from app.db.session import session_scope

# Статусы, которые на старте считаются «зависшими» (сервер перезапустили посреди
# обработки) и сбрасываются в paused для ручного возобновления.
STALE_STATUSES = {"splitting", "processing", "indexing", "uploaded"}


def _to_dict(doc: Document) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "content_type": doc.content_type,
        "size": doc.size,
        "status": doc.status,
        "error": doc.error,
        "okf_concept_count": doc.okf_concept_count,
        "total_chunks": doc.total_chunks,
        "processed_chunks": doc.processed_chunks,
        "current_chunk": doc.current_chunk,
        "tags": [t.tag for t in doc.tags_rel],
        "uploaded_by": doc.uploaded_by,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


class DocumentRegistry:
    def create(
        self,
        doc_id: str,
        filename: str,
        content_type: str,
        size: int,
        tags: list[str] | None = None,
        uploaded_by: str | None = None,
    ) -> dict:
        with session_scope() as s:
            doc = Document(
                id=doc_id,
                filename=filename,
                content_type=content_type,
                size=size,
                tags_rel=[DocumentTag(tag=t) for t in (tags or [])],
                uploaded_by=uploaded_by,
            )
            s.add(doc)
        return self.get(doc_id)

    def get(self, doc_id: str) -> dict | None:
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            return _to_dict(doc) if doc else None

    def list(self, uploaded_by: str | None = None) -> list[dict]:
        with session_scope() as s:
            stmt = select(Document).options(selectinload(Document.tags_rel))
            if uploaded_by is not None:
                stmt = stmt.where(Document.uploaded_by == uploaded_by)
            docs = s.execute(stmt).scalars().all()
            return [_to_dict(d) for d in docs]

    def update(self, doc_id: str, **fields) -> None:
        tags = fields.pop("tags", None)
        if not fields and tags is None:
            return
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                return
            for key, value in fields.items():
                setattr(doc, key, value)
            if tags is not None:
                doc.tags_rel.clear()
                for t in tags:
                    doc.tags_rel.append(DocumentTag(tag=t))

    def delete(self, doc_id: str) -> bool:
        with session_scope() as s:
            for model in (OkfConcept, OkfAttachment, DocumentStaging, DocumentTag):
                s.query(model).filter(model.doc_id == doc_id).delete(synchronize_session=False)
            doc = s.get(Document, doc_id)
            if doc is None:
                return False
            s.delete(doc)
            return True

    def reset_stale_statuses(self) -> None:
        """Переводит зависшие статусы в paused (вызывается на старте сервера)."""
        with session_scope() as s:
            docs = s.query(Document).filter(Document.status.in_(STALE_STATUSES)).all()
            for d in docs:
                d.status = "paused"
                d.error = "Сервер был перезапущен. Нажмите «Возобновить»"


_INSTANCE: DocumentRegistry | None = None


def get_registry() -> DocumentRegistry:
    """Общий для процесса реестр — один инстанс на всех потребителей."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DocumentRegistry()
    return _INSTANCE


def reset_stale_statuses() -> None:
    """Точка входа из lifespan: сброс зависших статусов после init_db()."""
    get_registry().reset_stale_statuses()
