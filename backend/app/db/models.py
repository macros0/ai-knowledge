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
    ForeignKey,
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
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    processed_chunks: Mapped[int] = mapped_column(Integer, default=0)
    current_chunk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    okf_concept_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    tags_rel: Mapped[list["DocumentTag"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentTag(Base):
    __tablename__ = "document_tags"

    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(255), primary_key=True)

    document: Mapped[Document] = relationship(back_populates="tags_rel")


class Tag(Base):
    __tablename__ = "tags"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)


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

    __table_args__ = (UniqueConstraint("doc_id", "slug", name="uq_okf_concepts_doc_slug"),)


class OkfAttachment(Base):
    __tablename__ = "okf_attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(512), default="")
    kind: Mapped[str] = mapped_column(String(64), default="other")
    caption: Mapped[str] = mapped_column(Text, default="")
    saved_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


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
