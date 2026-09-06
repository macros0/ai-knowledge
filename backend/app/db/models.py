"""Модели SQLAlchemy реляционной БД (схема из MIGRATION_PLAN.md §5).

Портативность SQLite (dev) / PostgreSQL (prod): массивы и отношения хранятся
в JSON-колонках (list[int]/list[str]/dict), а не в нативных Postgres ARRAY —
иначе dev-режим на SQLite и юнит-тесты были бы несовместимы с продом.

Отклонения от MIGRATION_PLAN.md, зафиксированные при реализации:
  - `tags.count` не хранится: счётчик вычисляется на чтение агрегатом по
    `document_tags` (нет триггера и дрейфа).
  - `okf_concepts.tags/relations` и staging-массивы — JSON, не Postgres ARRAY.
  - `documents.uploaded_by` — строка (username из сессии), без FK; `owner_id`/
    `org_id` — nullable FK (наполняются на Этапе 3/при авторизации в БД).
  - `users` дополнены `username`/`external_id` (nullable) под SSO (sub).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Development(Base):
    """Справочник номеров разработки (Этап 4 roadmap).

    `module` — строковое значение, мягко валидируемое против attribute_values
    (attribute_key='module'), а НЕ enum/CHECK: module-подобные поля живут как
    данные, а не как схема (см. AGENTS.md / OKF_Knowledge_Service_Roadmap.md).
    """

    __tablename__ = "developments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    module: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Оптимистическая блокировка совместного редактирования справочника:
    # клиент шлёт version при PATCH/DELETE, бэкенд сверяет и отдаёт 409 при
    # расхождении (version_conflict). Инкрементируется при каждой правке.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AttributeValue(Base):
    """Generic мини-справочник строковых атрибутов (module, component, ...).

    Значения хранятся как данные (attribute_key + value), а не как CHECK/enum в
    схеме. org_id=NULL — общее/системное значение; иначе — привязано к организации.
    Уникальность (key, value, org_id) — в БД; мягкое дублирование (NULL org) гасится
    идемпотентностью сервиса (attribute_registry.py).
    """

    __tablename__ = "attribute_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attribute_key: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("attribute_key", "value", "org_id", name="uq_attribute_values_key_value_org"),
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    org_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    permissions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class UserRole(Base):
    __tablename__ = "user_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)
    scope_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    scope_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Диагностический код неполноты при зелёном done (services/problem_codes.py):
    # no_concepts / no_text_layer / llm_partial_result / index_partial_failure.
    # None — терминальных проблем не зафиксировано. «done + problem» — документ
    # завершён без исключения, но может быть неполным/неищемым (UI: бейдж).
    problem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    current_chunk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    okf_concept_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    development_id: Mapped[int | None] = mapped_column(
        ForeignKey("developments.id"), nullable=True, index=True
    )
    development_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    development_confirmed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Кандидат автоопределения (number/name/module) для UI «требует уточнения»,
    # когда в справочнике не нашлось совпадения. Хранится, чтобы не гонять LLM повторно.
    development_suggestion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Дедупликация (Этап 4.2): SHA-256 байтов файла и нормализованного текста.
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # MinHash-подпись (k=128) документа, список uint32.
    minhash: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Есть ли почти-дубликаты (уровень 2/3): выставляется пайплайном после
    # расчёта сигнатуры. Бейдж «Дубликат» в UI.
    has_duplicates: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # Корзина / soft delete (Этап 4a.2): удалённый документ помечается, но не
    # удаляется физически до истечения окна хранения. `deleted_at = NULL` —
    # документ активен. `deleted_by` — username, инициировавший удаление.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    tags_rel: Mapped[list["DocumentTag"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    development: Mapped["Development | None"] = relationship()


class DocumentTag(Base):
    __tablename__ = "document_tags"

    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    document: Mapped[Document] = relationship(back_populates="tags_rel")
    tag_rel: Mapped["Tag"] = relationship()


class DocumentLshBucket(Base):
    """LSH-бакеты MinHash-подписи документа (Этап 4.2 дедупликация).

    Одна подпись (k=128) бандируется двумя схемами:
      - strict (b=8, r=16)  — порог Jaccard ~0.88 (почти идентичные);
      - loose  (b=16, r=8)  — порог Jaccard ~0.71 (ревизии/похожие).
    Каждая полоса (band) хэшируется в bucket_hash; поиск кандидатов — по
    (variant, band_index, bucket_hash). Уникальность (doc_id, variant, band_index)
    гарантирует, что повторная индексация не плодит дубликаты.
    """

    __tablename__ = "document_lsh_buckets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    variant: Mapped[str] = mapped_column(String(16), nullable=False)  # strict | loose
    band_index: Mapped[int] = mapped_column(Integer, nullable=False)
    bucket_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("doc_id", "variant", "band_index", name="uq_lsh_doc_variant_band"),
        Index("ix_lsh_lookup", "variant", "band_index", "bucket_hash"),
    )


class Tag(Base):
    """Тег (Этап 7 фаза B): суррогатный id + канонический текст + переводы.

    `canonical_text` — стабильный текстовый идентификатор тега (по нему идёт wire
    и payload Qdrant), `canonical_locale` — язык канонического текста (ru).
    Локализованные отображаемые имена — `translations` (tag_translations) для
    не-канонических локалей. Денормализованный `canonical_text` вместо «текст
    только в translations» — осознанное упрощение: канонический текст читается в
    ~10 местах (registry._to_dict/_search/_conditions, vector_store, tag_registry),
    уникальный индекс держит единственный источник; переводы — отдельно.
    `merged_into_id` — задел объединения дубликатов (merge-операция вне этапа).
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_text: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    canonical_locale: Mapped[str] = mapped_column(String(16), default="ru", nullable=False)
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("tags.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Soft-delete из «пула»: удаление/чистка не трогают document_tags (корзинный
    # документ сохраняет связь), а лишь помечают тег удалённым из автодополнения.
    # Возрождается при повторном использовании (get_or_create_ids) или показе
    # через активные document_tags (all()).
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    translations: Mapped[list["TagTranslation"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class TagTranslation(Base):
    """Перевод имени тега для локали (Этап 7 фаза B)."""

    __tablename__ = "tag_translations"

    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(String(255), nullable=False)
    is_machine_translated: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    translated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tag: Mapped[Tag] = relationship(back_populates="translations")


class DevelopmentTranslation(Base):
    """Перевод названия разработки (Этап 7 фаза B).

    `developments.name` остаётся каноническим названием (dev_tags-проекция и
    отображение без JOIN); переводы — для не-канонических локалей.
    """

    __tablename__ = "development_translations"

    development_id: Mapped[int] = mapped_column(
        ForeignKey("developments.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_machine_translated: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    translated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AttributeValueTranslation(Base):
    """Перевод label значения generic-атрибута (module и т.п., Этап 7 фаза B)."""

    __tablename__ = "attribute_value_translations"

    attribute_value_id: Mapped[int] = mapped_column(
        ForeignKey("attribute_values.id", ondelete="CASCADE"), primary_key=True
    )
    locale: Mapped[str] = mapped_column(String(16), primary_key=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    is_machine_translated: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    translated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OkfConcept(Base):
    __tablename__ = "okf_concepts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), default="")
    type: Mapped[str] = mapped_column(String(32), default="concept")
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    relations: Mapped[list | None] = mapped_column(JSON, nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Провенанс генерации (Этап 2b, PostgreSQL SSOT): отличает момент/модель/промпт
    # создания конкретной версии концепта от времени SQL INSERT (`created_at`).
    # Не трогаются при правке тегов, смене разработки или синке Qdrant payload.
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (UniqueConstraint("doc_id", "slug", name="uq_okf_concepts_doc_slug"),)


class OkfAttachment(Base):
    """Вложение документа (Этап 2b: активация из «мёртвой» таблицы).

    `saved_path` — относительный путь от корня хранилища документа
    (`uploads/<doc_id>/`), например `attachments/appendix-001.docx`; байты —
    в локальной FS, описание/принадлежность/хэш/статус — здесь. `sha256` и
    `size` считаются потоково при финализации, файл целиком в память не грузится.
    """

    __tablename__ = "okf_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(512), default="")
    kind: Mapped[str] = mapped_column(String(64), default="other")
    caption: Mapped[str] = mapped_column(Text, default="")
    saved_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Рекурсивно разобранный текстоизвлекаемый вкладыш (docx/xlsx/pdf) против
    # бинарного (image/opaque) или пропущенного по лимитам.
    is_processable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # parsed | saved | unsupported | skipped_depth | skipped_size
    extraction_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Колонка зарезервирована под трекинг происхождения блоков (Этап 2c);
    # в плоской модели заполняется NULL.
    extracted_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("doc_id", "saved_path", name="uq_okf_attachments_doc_saved_path"),
    )


class DocumentChunk(Base):
    """Финальный чанк документа (Этап 2b: PostgreSQL — единственный источник текста).

    Полный текст чанка, заголовок секции, порядок и хэш — канонически в БД; Qdrant
    хранит только slim-точку и гидрирует `content` отсюда по `(doc_id, chunk_index)`.
    Сырые `chunk_XX.md` остаются в FS лишь как артефакт бандла/экспорта, а не как
    рабочее состояние поиска.
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uq_document_chunks_doc_index"),
    )


class DocumentStaging(Base):
    __tablename__ = "document_staging"

    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    processed_chunks: Mapped[list | None] = mapped_column(JSON, nullable=True)
    used_slugs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    global_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    chunks_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="in_progress")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class AuditLog(Base):
    """Журнал ИБ (append-only): действия пользователей и системы.

    Неизменяемость обеспечивается на уровне приложения: сервис audit.py
    предоставляет только append()/query(), методов update/delete нет. Для
    Postgres-прода дополнительно рекомендуется выделить сервисный аккаунт с
    правами только на INSERT (см. README, «Хардненинг audit_log»).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    user_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    old_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Job(Base):
    """Системная (массовая) операция администратора.

    Массовые операции ставятся в очередь (job_queue.py), а не выполняются
    синхронно. Операции выше порога four-eyes переходят в awaiting_approval
    и требуют одобрения вторым администратором.
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    created_by_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserBlock(Base):
    """Блокировка пользователя (единственное активное действие роли Security).

    Проверяется в require_user: пользователь из блоклиста не проходит
    аутентификацию, даже если его сессия/группы валидны.
    """

    __tablename__ = "user_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChatSession(Base):
    """Тред чата (история, Этап 6).

    `id` — UUID, генерируемый бэкендом при создании треда (первая реплика без
    `session_id`) и возвращаемый клиенту; фронт хранит его и передаёт в следующих
    запросах. Бэкенд валидирует формат и привязывает сессию к текущему
    аутентифицированному `user_id` — а не к тому, что клиент мог подставить.
    Soft delete через `deleted_at`/`deleted_by` (окно хранения — аналог корзины
    документов, 4a.2); физическая очистка — services/chat_history.py.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    messages_rel: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """Одно сообщение треда (роль user/assistant).

    `sources` — снапшот источников (ChatSource[]) на момент ответа: хранится у
    assistant-сообщений и не «протухает» при последующих переиндексациях.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    session: Mapped[ChatSession] = relationship(back_populates="messages_rel")


class Locale(Base):
    """Язык UI/поиска (Этап 7 roadmap, «Поддержка языков»).

    `code` — BCP 47 / ISO 639 код (`ru`, `en`, ...). `status` управляет:
      - draft / active / disabled — активные участвуют в объединении стоп-слов
        для query-токенизации BM25 и доступны в LocaleToggle;
      - активация требует наличия UI-словаря (в статическом манифесте фронтенда,
        фаза A) и хотя бы одного набора stopwords.
    `ui_dictionary_version` — указатель актуальной runtime-версии UI-словаря
    (наполняется в фазе C; здесь заводится как задел).
    """

    __tablename__ = "locales"

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | active | disabled
    ui_dictionary_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Stopword(Base):
    """Стоп-слово поиска (Этап 7 roadmap).

    Хранятся как данные, а не как константы — два потребителя используют единый
    сервис services/stopwords.py:
      - kind='bm25'   — фильтр лексической ветки (BM25 query-токенизация);
      - kind='marker' — фильтр маркеров Matched terms / Title match (context_builder).
    Индексная формула sparse.py заморожена на константах и НЕ читает эту таблицу:
    динамический набор применяется только на стороне запроса (см. AGENTS.md).
    """

    __tablename__ = "stopwords"

    locale: Mapped[str] = mapped_column(
        ForeignKey("locales.code", ondelete="CASCADE"), primary_key=True
    )
    word: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)  # bm25 | marker
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
