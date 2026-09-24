"""Реестр документов на реляционной БД (замена data/documents.json).

Интерфейс совместим с прежним JSON-реестром: те же методы (create/get/list/
update/delete) и та же форма возвращаемых dict (id, filename, status, tags,
created_at/updated_at как datetime). Внутренности — SQLAlchemy-сессии поверх
PostgreSQL (prod) / SQLite (dev). Отличие: нет in-memory словаря и полной
перезаписи файла — каждая операция в отдельной транзакции.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.db.models import (
    Development,
    Document,
    DocumentChunk,
    DocumentLshBucket,
    DocumentStaging,
    DocumentTag,
    OkfAttachment,
    OkfConcept,
    Tag,
)
from app.db.session import session_scope
from app import error_codes as codes
from app.services.storage import (
    clear_transient_storage_failure,
    storage_failure_lock,
    transient_storage_failure,
)

# Статусы, которые на старте считаются «зависшими» (сервер перезапустили посреди
# обработки) и сбрасываются в paused для ручного возобновления.
STALE_STATUSES = {"splitting", "processing", "indexing", "uploaded", "queued"}
SERVER_RESTARTED_MESSAGE = "Сервер был перезапущен. Нажмите «Возобновить»"

# Статусы «остановившихся» документов — попали в объединённый фильтр «Проблемные».
# paused — генерация OKF остановлена (кнопка «Возобновить»), failed/error — ошибка.
PROBLEM_STATUSES = {"paused", "failed", "error"}

# Сортировка списка документов: ключ дропдауна (совпадает с SORT_OPTIONS на
# фронте) → (колонка, направление). Дефолт — новые сначала (как прежний
# docs.sort(created_at, reverse=True) в api/documents.py).
SORT_COLUMNS = {
    "date_desc": (Document.created_at, "desc"),
    "date_asc": (Document.created_at, "asc"),
    "name_asc": (Document.filename, "asc"),
    "name_desc": (Document.filename, "desc"),
    "uploader_asc": (Document.uploaded_by, "asc"),
    "uploader_desc": (Document.uploaded_by, "desc"),
}


def _glob_to_like(pattern: str) -> str:
    """Переводит glob-маску (* → %, ? → _) в LIKE-паттерн с полным совпадением.

    Литеральные %/_/\\ экранируются через ESCAPE '\\' — в точности воспроизводит
    семантику globToRegExp на фронте (^...$, регистронезависимо через ilike).
    """
    out: list[str] = []
    for ch in pattern:
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        elif ch in ("%", "_", "\\"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _like_escape(text: str) -> str:
    """Экранирует %/_/\\ для подстрокового LIKE-поиска (без glob-семантики)."""
    out: list[str] = []
    for ch in text:
        if ch in ("%", "_", "\\"):
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _search_conditions(search: str | None) -> list:
    """Условия текстового поиска (OR по полям).

    Подстрока по умолчанию; при наличии * или ? — glob (полное совпадение)
    применяется ТОЛЬКО к filename (обратная совместимость с прежним клиентским
    фильтром), остальные поля (tags / development / uploaded_by) всегда ищутся
    по литеральной подстроке запроса.
    """
    q = (search or "").strip()
    if not q:
        return []
    has_glob = "*" in q or "?" in q
    literal = _like_escape(q)
    conditions: list = []
    if has_glob:
        conditions.append(Document.filename.ilike(_glob_to_like(q), escape="\\"))
    else:
        conditions.append(Document.filename.ilike(f"%{literal}%", escape="\\"))
    conditions.append(Document.uploaded_by.ilike(f"%{literal}%", escape="\\"))
    # EXISTS-подзапросы (не JOIN) — чтобы не размножать строки документа.
    conditions.append(Document.tags_rel.any(DocumentTag.tag_rel.has(Tag.canonical_text.ilike(f"%{literal}%", escape="\\"))))
    conditions.append(
        Document.development.has(
            or_(
                Development.number.ilike(f"%{literal}%", escape="\\"),
                Development.name.ilike(f"%{literal}%", escape="\\"),
                Development.module.ilike(f"%{literal}%", escape="\\"),
            )
        )
    )
    return conditions


def _to_dict(doc: Document, concepts_generated_at: datetime | None = None) -> dict:
    dev = doc.development
    result = {
        "id": doc.id,
        "filename": doc.filename,
        "content_type": doc.content_type,
        "size": doc.size,
        "status": doc.status,
        "error": doc.error,
        "error_code": doc.error_code or (
            codes.SERVER_RESTARTED
            if doc.status == "paused" and doc.error == SERVER_RESTARTED_MESSAGE else None
        ),
        "problem": doc.problem,
        "okf_concept_count": doc.okf_concept_count,
        "concepts_generated_at": concepts_generated_at,
        "total_chunks": doc.total_chunks,
        "processed_chunks": doc.processed_chunks,
        "current_chunk": doc.current_chunk,
        "tags": [t.tag_rel.canonical_text for t in doc.tags_rel],
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
        "deleted_at": doc.deleted_at,
        "deleted_by": doc.deleted_by,
        "source_locale": doc.source_locale,
        "source_locale_source": doc.source_locale_source,
    }
    # PostgreSQL/SQLite могут отвергнуть UPDATE статуса из-за заполненного
    # своего тома. До повторной успешной записи UI обязан видеть pause, а не
    # вечное «обрабатывается» из старой строки.
    if transient := transient_storage_failure(doc.id):
        result.update(transient)
    return result


def _document_dicts(session, docs: list[Document]) -> list[dict]:
    """Read generation provenance in one batch for the selected documents only."""
    if not docs:
        return []
    generated = dict(session.execute(
        select(OkfConcept.doc_id, func.max(OkfConcept.generated_at))
        .where(OkfConcept.doc_id.in_([doc.id for doc in docs]))
        .group_by(OkfConcept.doc_id)
    ).all())
    from app.services.gen_quality import partial_chunk_indices

    partial = {
        doc_id: partial_chunk_indices(chunks_data or {})
        for doc_id, chunks_data in session.execute(
            select(DocumentStaging.doc_id, DocumentStaging.chunks_data)
            .where(DocumentStaging.doc_id.in_([doc.id for doc in docs]))
        ).all()
    }
    result = []
    for doc in docs:
        timestamp = generated.get(doc.id)
        # SQLite drops timezone info; provenance is written in UTC.
        if timestamp is not None and timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        item = _to_dict(doc, timestamp)
        item["partial_chunks"] = partial.get(doc.id, [])
        result.append(item)
    return result


class DocumentRegistry:
    def create(
        self,
        doc_id: str,
        filename: str,
        content_type: str,
        size: int,
        tags: list[str] | None = None,
        uploaded_by: str | None = None,
        canonical_locale: str = "und",
    ) -> dict:
        from app.services.tag_registry import TagRegistry

        tag_ids = TagRegistry().get_or_create_ids(tags, created_by=uploaded_by, canonical_locale=canonical_locale)
        with session_scope() as s:
            doc = Document(
                id=doc_id,
                filename=filename,
                content_type=content_type,
                size=size,
                tags_rel=[DocumentTag(tag_id=tid) for tid in tag_ids],
                uploaded_by=uploaded_by,
            )
            s.add(doc)
        return self.get(doc_id)

    def get(self, doc_id: str) -> dict | None:
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            return _document_dicts(s, [doc])[0] if doc else None

    def get_many(self, doc_ids: Iterable[str]) -> dict[str, dict | None]:
        """Load document visibility metadata in one session/query batch."""
        ids = {str(doc_id) for doc_id in doc_ids if doc_id}
        if not ids:
            return {}
        with session_scope() as s:
            rows = s.execute(
                select(Document)
                .where(Document.id.in_(ids))
                .options(
                    joinedload(Document.development),
                    selectinload(Document.tags_rel).selectinload(DocumentTag.tag_rel),
                )
            ).scalars().all()
            values = {doc["id"]: doc for doc in _document_dicts(s, rows)}
        return {doc_id: values.get(doc_id) for doc_id in ids}

    def get_visibility_many(self, doc_ids: Iterable[str]) -> dict[str, dict | None]:
        """Load only fields needed to filter search hits by visibility."""
        ids = {str(doc_id) for doc_id in doc_ids if doc_id}
        if not ids:
            return {}
        with session_scope() as s:
            rows = s.execute(
                select(Document.id, Document.filename, Document.deleted_at)
                .where(Document.id.in_(ids))
            ).all()
        values = {
            doc_id: {
                "id": doc_id,
                "filename": filename,
                "deleted_at": deleted_at,
            }
            for doc_id, filename, deleted_at in rows
        }
        return {doc_id: values.get(doc_id) for doc_id in ids}

    def get_export_metadata_many(self, doc_ids: Iterable[str]) -> dict[str, dict | None]:
        """Load the small, stable document snapshot needed by export admission."""
        ids = {str(doc_id) for doc_id in doc_ids if doc_id}
        if not ids:
            return {}
        with session_scope() as s:
            rows = s.execute(
                select(Document.id, Document.filename, Document.size, Document.deleted_at)
                .where(Document.id.in_(ids))
            ).all()
        values = {
            doc_id: {
                "id": doc_id,
                "filename": filename,
                "size": size,
                "deleted_at": deleted_at,
            }
            for doc_id, filename, size, deleted_at in rows
        }
        return {doc_id: values.get(doc_id) for doc_id in ids}

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
        """Список документов с фильтрами (без пагинации — обратная совместимость).

        Все фильтры — простые WHERE-pushdown по хранимым колонкам/связям (без
        вычислений по времени и без LSH-поиска на лету):
          - uploaded_by — точное совпадение username;
          - statuses — status IN (...);
          - has_duplicates — булев флаг (персистентный, см. pipeline);
          - development_id / development_number — по индексированному FK либо по
            уникальному developments.number через связь;
          - module — через development.module (JOIN по development_id);
          - problem — объединённое «Проблемные»: status IN (PROBLEM_STATUSES)
            OR has_duplicates = true OR (status='done' AND development_id IS NULL,
            «черновик — требует разметки»). Набор условий — в одном месте, новые
            «проблемные» флаги добавляются туда же.
        """
        docs, _ = self.list_page(
            uploaded_by=uploaded_by,
            statuses=statuses,
            has_duplicates=has_duplicates,
            development_id=development_id,
            development_number=development_number,
            module=module,
            problem=problem,
        )
        return docs

    def _conditions(
        self,
        uploaded_by: str | None = None,
        statuses: list[str] | None = None,
        has_duplicates: bool | None = None,
        development_id: int | None = None,
        development_number: str | None = None,
        module: str | None = None,
        problem: bool | None = None,
        search: str | None = None,
        tag: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        source_locales: list[str] | None = None,
        source_locale_unknown: bool = False,
        active_only: bool = True,
    ) -> list:
        conditions: list = []
        if active_only:
            conditions.append(Document.deleted_at.is_(None))
        if uploaded_by is not None:
            conditions.append(Document.uploaded_by == uploaded_by)
        if statuses:
            conditions.append(Document.status.in_(statuses))
        if has_duplicates is not None:
            conditions.append(Document.has_duplicates.is_(has_duplicates))
        if development_id is not None:
            conditions.append(Document.development_id == development_id)
        if development_number is not None:
            conditions.append(Document.development.has(Development.number == development_number))
        if module is not None:
            conditions.append(Document.development.has(Development.module == module))
        if tag is not None:
            from app.services.tag_registry import TagRegistry

            tag_id = TagRegistry().resolve(tag)
            if tag_id is None:
                conditions.append(false())
            else:
                conditions.append(Document.tags_rel.any(DocumentTag.tag_id == tag_id))
        if date_from is not None:
            conditions.append(Document.created_at >= date_from)
        if date_to is not None:
            conditions.append(Document.created_at <= date_to)
        if source_locales is not None or source_locale_unknown:
            # OR-семантика: коды из списка ИЛИ «не определён» (NULL).
            locale_conds: list = []
            if source_locales:
                locale_conds.append(Document.source_locale.in_(source_locales))
            if source_locale_unknown:
                locale_conds.append(Document.source_locale.is_(None))
            conditions.append(or_(*locale_conds))
        if problem:
            conditions.append(
                or_(
                    Document.status.in_(PROBLEM_STATUSES),
                    Document.has_duplicates.is_(True),
                    # Диагностический problem-код при зелёном done (инцидент
                    # 03.09.2026): неполнота без исключения — тоже «Проблемные».
                    Document.problem.isnot(None),
                    # Готовый документ без привязанной разработки — «черновик,
                    # требует разметки» (без suggestion) либо «требует уточнения»
                    # (с development_suggestion). Оба — «не размечен», в «Проблемные».
                    and_(
                        Document.status == "done",
                        Document.development_id.is_(None),
                    ),
                )
            )
        search_conditions = _search_conditions(search)
        if search_conditions:
            conditions.append(or_(*search_conditions))
        return conditions

    def list_page(
        self,
        uploaded_by: str | None = None,
        statuses: list[str] | None = None,
        has_duplicates: bool | None = None,
        development_id: int | None = None,
        development_number: str | None = None,
        module: str | None = None,
        problem: bool | None = None,
        search: str | None = None,
        tag: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        source_locales: list[str] | None = None,
        source_locale_unknown: bool = False,
        sort: str = "date_desc",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Список документов с поиском, серверной сортировкой и пагинацией.

        Возвращает (docs, total). total считается по тому же набору условий,
        что и выборка (без limit/offset), чтобы фронт мог рисовать пагинацию.
        sort — ключ из SORT_COLUMNS (совпадает с SORT_OPTIONS фронта);
        limit=None — вернуть всё (обратная совместимость с прежним list()).

        По умолчанию возвращает ТОЛЬКО активные документы (deleted_at IS NULL);
        удалённые (корзина) — через list_trash (Этап 4a.2).
        """
        conditions = self._conditions(
            uploaded_by=uploaded_by,
            statuses=statuses,
            has_duplicates=has_duplicates,
            development_id=development_id,
            development_number=development_number,
            module=module,
            problem=problem,
            search=search,
            tag=tag,
            date_from=date_from,
            date_to=date_to,
            source_locales=source_locales,
            source_locale_unknown=source_locale_unknown,
            active_only=True,
        )
        with session_scope() as s:
            total_stmt = select(func.count()).select_from(Document)
            if conditions:
                total_stmt = total_stmt.where(*conditions)
            total = s.execute(total_stmt).scalar_one()

            stmt = select(Document).options(
                selectinload(Document.tags_rel).selectinload(DocumentTag.tag_rel),
                selectinload(Document.development),
            )
            if conditions:
                stmt = stmt.where(*conditions)
            column, direction = SORT_COLUMNS.get(sort, SORT_COLUMNS["date_desc"])
            order_expr = column.asc() if direction == "asc" else column.desc()
            # null/пустые значения — всегда в конец (как в клиентском sortDocuments),
            # tie-breaker — по дате, новые сначала.
            stmt = stmt.order_by(order_expr.nulls_last(), Document.created_at.desc())
            if offset:
                stmt = stmt.offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)
            docs = s.execute(stmt).scalars().all()
            return _document_dicts(s, docs), total

    def source_locale_facets(self, uploaded_by: str | None = None) -> list[dict]:
        """Счётчики языков документа для фасетов (Этап 7 фаза D, фильтр).

        Visibility-ограничения те же, что у списка: только активные документы
        (`deleted_at IS NULL`) + опционально scope по uploader. Прочие фильтры
        списка (tag/status/module/q) НЕ учитываются — фасеты отвечают «какие
        языки есть в видимом корпусе вообще», счётчики стабильны.

        Возвращает [{"code": str | None, "count": int}]; code=None = «не определён».
        """
        with session_scope() as s:
            stmt = (
                select(Document.source_locale, func.count())
                .select_from(Document)
                .where(Document.deleted_at.is_(None))
            )
            if uploaded_by is not None:
                stmt = stmt.where(Document.uploaded_by == uploaded_by)
            stmt = stmt.group_by(Document.source_locale).order_by(func.count().desc())
            rows = s.execute(stmt).all()
        return [{"code": r[0], "count": r[1]} for r in rows]

    def distinct_uploaders(self) -> list[str]:
        with session_scope() as s:
            stmt = (
                select(Document.uploaded_by)
                .where(Document.uploaded_by.isnot(None), Document.deleted_at.is_(None))
                .distinct()
            )
            names = [u for u in s.execute(stmt).scalars().all() if u]
            return sorted(names)

    def markup_stats(self) -> dict:
        """Прогресс разметки по активной базе (Этап 4.1, отложен в Этап 5).

        Возвращает {total, with_development}: долю активных документов с непустым
        development_id. Считается по ВСЕЙ активной базе (deleted_at IS NULL), а не
        по текущему фильтру — это общий индикатор здоровья разметки, он не должен
        «скакать» при изменении фильтра списка.
        """
        with session_scope() as s:
            total = s.execute(
                select(func.count()).select_from(Document).where(Document.deleted_at.is_(None))
            ).scalar_one()
            with_development = s.execute(
                select(func.count())
                .select_from(Document)
                .where(Document.deleted_at.is_(None), Document.development_id.isnot(None))
            ).scalar_one()
        return {"total": total, "with_development": with_development}

    def update(self, doc_id: str, **fields) -> None:
        # Resume обязан быть атомарен относительно фоновой попытки сохранить
        # старый storage_full, иначе поздний retry снова поставит paused.
        with storage_failure_lock():
            tags = fields.pop("tags", None)
            canonical_locale = fields.pop("canonical_locale", "und")
            if not fields and tags is None:
                return
            tag_ids = None
            if tags is not None:
                from app.services.tag_registry import TagRegistry

                tag_ids = TagRegistry().get_or_create_ids(tags, canonical_locale=canonical_locale)
            with session_scope() as s:
                doc = s.get(Document, doc_id)
                if doc is None:
                    return
                for key, value in fields.items():
                    setattr(doc, key, value)
                if tag_ids is not None:
                    doc.tags_rel.clear()
                    for tid in tag_ids:
                        doc.tags_rel.append(DocumentTag(tag_id=tid))
            if fields.get("status") and not (
                fields.get("status") == "paused" and fields.get("error_code") == codes.STORAGE_FULL
            ):
                clear_transient_storage_failure(doc_id)

    def delete(self, doc_id: str) -> bool:
        with session_scope() as s:
            for model in (OkfConcept, DocumentChunk, OkfAttachment, DocumentStaging, DocumentTag, DocumentLshBucket):
                s.query(model).filter(model.doc_id == doc_id).delete(synchronize_session=False)
            doc = s.get(Document, doc_id)
            if doc is None:
                return False
            s.delete(doc)
            return True

    def delete_if_deleted(self, doc_id: str) -> bool:
        """Физически удаляет документ из БД ТОЛЬКО если он в корзине.

        Атомарная защита restore-vs-purge (Этап 4a.2): строка лочится
        (FOR UPDATE на Postgres) и проверяется внутри той же транзакции,
        поэтому восстановление, случившееся между `purge_expired()` и этим
        вызовом, детектится здесь — документ переживает очистку (возврат
        False). На SQLite FOR UPDATE нет, но пишет она сериализованно.
        """
        with session_scope() as s:
            stmt = select(Document).where(Document.id == doc_id)
            if s.bind.dialect.name == "postgresql":
                stmt = stmt.with_for_update()
            doc = s.execute(stmt).scalar_one_or_none()
            if doc is None or doc.deleted_at is None:
                return False
            for model in (OkfConcept, DocumentChunk, OkfAttachment, DocumentStaging, DocumentTag, DocumentLshBucket):
                s.query(model).filter(model.doc_id == doc_id).delete(synchronize_session=False)
            s.delete(doc)
            return True

    def soft_delete(self, doc_id: str, deleted_by: str | None = None) -> bool:
        """Помечает документ удалённым (корзина, Этап 4a.2), не удаляя данные."""
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                return False
            doc.deleted_at = datetime.now(timezone.utc)
            doc.deleted_by = deleted_by
        from app.services.deduplication import refresh_duplicate_flags

        refresh_duplicate_flags(doc_id)
        return True

    def restore(self, doc_id: str) -> bool:
        """Снимает флаг удаления (восстановление из корзины)."""
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc is None:
                return False
            doc.deleted_at = None
            doc.deleted_by = None
        from app.services.deduplication import refresh_duplicate_flags

        refresh_duplicate_flags(doc_id)
        return True

    def list_trash(
        self,
        uploaded_by: str | None = None,
        search: str | None = None,
        sort: str = "date_desc",
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Список удалённых документов (корзина) с поиском/пагинацией.

        Только документы с deleted_at IS NOT NULL; uploader/search — те же
        фильтры, что и в активном списке. Сортировка по deleted_at desc
        (свежие удаления первыми) поверх обычного sort-ключа.
        """
        conditions: list = [Document.deleted_at.isnot(None)]
        if uploaded_by is not None:
            conditions.append(Document.uploaded_by == uploaded_by)
        search_conditions = _search_conditions(search)
        if search_conditions:
            conditions.append(or_(*search_conditions))

        with session_scope() as s:
            total_stmt = select(func.count()).select_from(Document).where(*conditions)
            total = s.execute(total_stmt).scalar_one()

            stmt = select(Document).options(
                selectinload(Document.tags_rel).selectinload(DocumentTag.tag_rel),
                selectinload(Document.development),
            ).where(*conditions)
            column, direction = SORT_COLUMNS.get(sort, SORT_COLUMNS["date_desc"])
            order_expr = column.asc() if direction == "asc" else column.desc()
            stmt = stmt.order_by(Document.deleted_at.desc(), order_expr.nulls_last())
            if offset:
                stmt = stmt.offset(offset)
            if limit is not None:
                stmt = stmt.limit(limit)
            docs = s.execute(stmt).scalars().all()
            return _document_dicts(s, docs), total

    def purge_expired(self, cutoff: datetime) -> list[str]:
        """Возвращает doc_id документов, чей срок корзины истёк (deleted_at <= cutoff).

        Само физическое удаление выполняет вызывающий (trash-сервис через
        Pipeline.remove), чтобы объединить БД + Qdrant + файлы в одном месте.
        """
        with session_scope() as s:
            rows = s.execute(
                select(Document.id).where(
                    Document.deleted_at.isnot(None), Document.deleted_at <= cutoff
                )
            ).scalars().all()
            return list(rows)

    def reset_stale_statuses(self) -> None:
        """Переводит зависшие статусы в paused (вызывается на старте сервера)."""
        with session_scope() as s:
            docs = (
                s.query(Document)
                .filter(Document.status.in_(STALE_STATUSES), Document.deleted_at.is_(None))
                .all()
            )
            for d in docs:
                d.status = "paused"
                d.error = SERVER_RESTARTED_MESSAGE
                d.error_code = codes.SERVER_RESTARTED


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
