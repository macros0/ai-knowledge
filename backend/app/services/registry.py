"""Реестр документов на реляционной БД (замена data/documents.json).

Интерфейс совместим с прежним JSON-реестром: те же методы (create/get/list/
update/delete) и та же форма возвращаемых dict (id, filename, status, tags,
created_at/updated_at как datetime). Внутренности — SQLAlchemy-сессии поверх
PostgreSQL (prod) / SQLite (dev). Отличие: нет in-memory словаря и полной
перезаписи файла — каждая операция в отдельной транзакции.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.db.models import (
    Development,
    Document,
    DocumentLshBucket,
    DocumentStaging,
    DocumentTag,
    OkfAttachment,
    OkfConcept,
)
from app.db.session import session_scope

# Статусы, которые на старте считаются «зависшими» (сервер перезапустили посреди
# обработки) и сбрасываются в paused для ручного возобновления.
STALE_STATUSES = {"splitting", "processing", "indexing", "uploaded"}

# Статусы «остановившихся» документов — попали в объединённый фильтр «Проблемные».
# paused — генерация OKF остановлена (кнопка «Возобновить»), failed/error — ошибка.
PROBLEM_STATUSES = {"paused", "failed", "error"}


def _to_dict(doc: Document) -> dict:
    dev = doc.development
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
        "development_id": doc.development_id,
        "development_number": dev.number if dev else None,
        "development_name": dev.name if dev else None,
        "development_module": dev.module if dev else None,
        "development_confidence": doc.development_confidence,
        "development_confirmed_by": doc.development_confirmed_by,
        "development_suggestion": doc.development_suggestion,
        "has_duplicates": bool(doc.has_duplicates),
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

    def list(
        self,
        uploaded_by: str | None = None,
        statuses: list[str] | None = None,
        has_duplicates: bool | None = None,
        development_id: int | None = None,
        development_number: str | None = None,
        module: str | None = None,
        problem: bool | None = None,
    ) -> list[dict]:
        """Список документов с фильтрами.

        Все фильтры — простые WHERE-pushdown по хранимым колонкам/связям (без
        вычислений по времени и без LSH-поиска на лету):
          - uploaded_by — точное совпадение username;
          - statuses — status IN (...);
          - has_duplicates — булев флаг (персистентный, см. pipeline);
          - development_id / development_number — по индексированному FK либо по
            уникальному developments.number через связь;
          - module — через development.module (JOIN по development_id);
          - problem — объединённое «Проблемные»: status IN (PROBLEM_STATUSES)
            OR has_duplicates = true. Набор условий — в одном месте, новые
            «проблемные» флаги добавляются туда же.
        """
        with session_scope() as s:
            stmt = select(Document).options(
                selectinload(Document.tags_rel), selectinload(Document.development)
            )
            conditions: list = []
            if uploaded_by is not None:
                conditions.append(Document.uploaded_by == uploaded_by)
            if statuses:
                conditions.append(Document.status.in_(statuses))
            if has_duplicates is not None:
                conditions.append(Document.has_duplicates.is_(has_duplicates))
            if development_id is not None:
                conditions.append(Document.development_id == development_id)
            if development_number is not None:
                conditions.append(
                    Document.development.has(Development.number == development_number)
                )
            if module is not None:
                conditions.append(Document.development.has(Development.module == module))
            if problem:
                conditions.append(
                    or_(
                        Document.status.in_(PROBLEM_STATUSES),
                        Document.has_duplicates.is_(True),
                    )
                )
            if conditions:
                stmt = stmt.where(*conditions)
            docs = s.execute(stmt).scalars().all()
            return [_to_dict(d) for d in docs]

    def distinct_uploaders(self) -> list[str]:
        with session_scope() as s:
            stmt = (
                select(Document.uploaded_by)
                .where(Document.uploaded_by.isnot(None))
                .distinct()
            )
            names = [u for u in s.execute(stmt).scalars().all() if u]
            return sorted(names)

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
            for model in (OkfConcept, OkfAttachment, DocumentStaging, DocumentTag, DocumentLshBucket):
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
