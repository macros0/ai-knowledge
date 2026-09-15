"""Журнал ИБ (audit_log): append-only запись действий пользователей и системы.

Принцип (Этап 2/2а roadmap): журнал неизменяем даже для роли Admin. Поэтому
сервис предоставляет ТОЛЬКО методы append()/query() — update/delete отсутствуют
в коде. Для PostgreSQL-прода дополнительно рекомендуется выделить сервисный
аккаунт с правами только на INSERT (README, «Хардненинг audit_log»).

action_type — фиксированный перечень значений (тест test_audit.py проверяет
конкретные строки). Расширяется по мере добавления новых мутирующих операций
(загрузка, правки тегов — Этап 4a, вход/выход — отдельно).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.db.models import AuditLog
from app.db.session import session_scope

# Канонический перечень типов действий (Этап 2а, текущий объём).
DOCUMENT_UPLOAD = "document_upload"
DOCUMENT_DELETE = "document_delete"
DOCUMENT_BULK_DELETE = "document_bulk_delete"
DOCUMENT_REGENERATE = "document_regenerate"
DOCUMENT_BULK_REGENERATE = "document_bulk_regenerate"
DOCUMENT_RESUME = "document_resume"
DOCUMENT_DEVELOPMENT_SET = "document_development_set"
DOCUMENT_TAGS_UPDATE = "document_tags_update"
DOCUMENT_BULK_TAGS_UPDATE = "document_bulk_tags_update"
DOCUMENT_SOURCE_LOCALE_UPDATE = "document_source_locale_update"
JOB_APPROVE = "job_approve"
JOB_CANCEL = "job_cancel"
USER_BLOCK = "user_block"
USER_UNBLOCK = "user_unblock"
DEVELOPMENT_CREATE = "development_create"
DEVELOPMENT_UPDATE = "development_update"
DEVELOPMENT_DELETE = "development_delete"
ATTRIBUTE_CREATE = "attribute_create"
ATTRIBUTE_DELETE = "attribute_delete"
TAG_DELETE = "tag_delete"
TAG_CLEANUP = "tag_cleanup"
DOCUMENT_RESTORE = "document_restore"
DOCUMENT_BULK_RESTORE = "document_bulk_restore"
DOCUMENT_AUTO_DELETE = "document_auto_delete"
CHAT_HISTORY_VIEW = "chat_history_view"
CHAT_HISTORY_AUTO_DELETE = "chat_history_auto_delete"
DOCUMENT_EXPORT = "document_export"
DOCUMENT_BULK_EXPORT_REQUESTED = "document_bulk_export_requested"
DOCUMENT_BULK_EXPORT_COMPLETED = "document_bulk_export_completed"
DOCUMENT_BULK_EXPORT_FAILED = "document_bulk_export_failed"
DOCUMENT_BULK_EXPORT_DOWNLOAD_STARTED = "document_bulk_export_download_started"
DOCUMENT_BULK_EXPORT_EXPIRED = "document_bulk_export_expired"
DOCUMENT_BULK_EXPORT_DELETED = "document_bulk_export_deleted"
LOCALE_CREATE = "locale_create"
LOCALE_UPDATE = "locale_update"
LOCALE_ACTIVATE = "locale_activate"
LOCALE_DISABLE = "locale_disable"
STOPWORDS_IMPORT = "stopwords_import"
STOPWORDS_UPDATE = "stopwords_update"
STOPWORDS_ROLLBACK = "stopwords_rollback"
TAG_TRANSLATION_UPDATE = "tag_translation_update"
TAG_TRANSLATION_REVIEW = "tag_translation_review"
TRANSLATIONS_BACKFILL = "translations_backfill"
UI_DICTIONARY_IMPORT = "ui_dictionary_import"
UI_DICTIONARY_ROLLBACK = "ui_dictionary_rollback"
GLOSSARY_TERM_CREATE = "glossary_term_create"
GLOSSARY_TERM_UPDATE = "glossary_term_update"
GLOSSARY_TERM_MERGE = "glossary_term_merge"
GLOSSARY_SOURCE_UPDATE = "glossary_source_update"
GLOSSARY_ALIAS_CREATE = "glossary_alias_create"
GLOSSARY_ALIAS_UPDATE = "glossary_alias_update"
GLOSSARY_ALIAS_DELETE = "glossary_alias_delete"
GLOSSARY_TRANSLATION_UPDATE = "glossary_translation_update"
GLOSSARY_TRANSLATION_REVIEW = "glossary_translation_review"
GLOSSARY_TRANSLATION_BACKFILL = "glossary_translation_backfill"
GLOSSARY_RULE_CREATE = "glossary_rule_create"
GLOSSARY_RULE_UPDATE = "glossary_rule_update"
GLOSSARY_RULE_DELETE = "glossary_rule_delete"
GLOSSARY_IDENTITY_MIGRATION = "glossary_identity_migration"

ACTION_TYPES = frozenset(
    {
        DOCUMENT_UPLOAD,
        DOCUMENT_DELETE,
        DOCUMENT_BULK_DELETE,
        DOCUMENT_REGENERATE,
        DOCUMENT_BULK_REGENERATE,
        DOCUMENT_RESUME,
        DOCUMENT_DEVELOPMENT_SET,
        DOCUMENT_TAGS_UPDATE,
        DOCUMENT_BULK_TAGS_UPDATE,
        DOCUMENT_SOURCE_LOCALE_UPDATE,
        JOB_APPROVE,
        JOB_CANCEL,
        USER_BLOCK,
        USER_UNBLOCK,
        DEVELOPMENT_CREATE,
        DEVELOPMENT_UPDATE,
        DEVELOPMENT_DELETE,
        ATTRIBUTE_CREATE,
        ATTRIBUTE_DELETE,
        TAG_DELETE,
        TAG_CLEANUP,
        DOCUMENT_RESTORE,
        DOCUMENT_BULK_RESTORE,
        DOCUMENT_AUTO_DELETE,
        CHAT_HISTORY_VIEW,
        CHAT_HISTORY_AUTO_DELETE,
        DOCUMENT_EXPORT,
        DOCUMENT_BULK_EXPORT_REQUESTED,
        DOCUMENT_BULK_EXPORT_COMPLETED,
        DOCUMENT_BULK_EXPORT_FAILED,
        DOCUMENT_BULK_EXPORT_DOWNLOAD_STARTED,
        DOCUMENT_BULK_EXPORT_EXPIRED,
        DOCUMENT_BULK_EXPORT_DELETED,
        LOCALE_CREATE,
        LOCALE_UPDATE,
        LOCALE_ACTIVATE,
        LOCALE_DISABLE,
        STOPWORDS_IMPORT,
        STOPWORDS_UPDATE,
        STOPWORDS_ROLLBACK,
        TAG_TRANSLATION_UPDATE,
        TAG_TRANSLATION_REVIEW,
        TRANSLATIONS_BACKFILL,
        UI_DICTIONARY_IMPORT,
        UI_DICTIONARY_ROLLBACK,
        GLOSSARY_TERM_CREATE,
        GLOSSARY_TERM_UPDATE,
        GLOSSARY_TERM_MERGE,
        GLOSSARY_SOURCE_UPDATE,
        GLOSSARY_ALIAS_CREATE,
        GLOSSARY_ALIAS_UPDATE,
        GLOSSARY_ALIAS_DELETE,
        GLOSSARY_TRANSLATION_UPDATE,
        GLOSSARY_TRANSLATION_REVIEW,
        GLOSSARY_TRANSLATION_BACKFILL,
        GLOSSARY_RULE_CREATE,
        GLOSSARY_RULE_UPDATE,
        GLOSSARY_RULE_DELETE,
        GLOSSARY_IDENTITY_MIGRATION,
    }
)

TARGET_DOCUMENT = "document"
TARGET_JOB = "job"
TARGET_USER = "user"
TARGET_DEVELOPMENT = "development"
TARGET_ATTRIBUTE = "attribute"
TARGET_TAG = "tag"
TARGET_CHAT = "chat_session"
TARGET_LOCALE = "locale"
TARGET_GLOSSARY_TERM = "glossary_term"


class SystemUser:
    """Прокси-«пользователь» для аудита автоочистки (системное действие)."""

    user_id = "system"
    username = "system"


def iso_or_str(value) -> str | None:
    """Серийализует datetime в ISO-строку (для JSON-колонок audit_log)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _to_dict(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "created_at": entry.created_at,
        "user_id": entry.user_id,
        "username": entry.username,
        "action_type": entry.action_type,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "old_value": entry.old_value,
        "new_value": entry.new_value,
        "ip_address": entry.ip_address,
        "meta": entry.meta,
    }


def record_in_session(
    session,
    *,
    action_type: str,
    user_id: str | None = None,
    username: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    ip_address: str | None = None,
    meta: dict | None = None,
) -> dict:
    """Append an audit entry to an existing transaction without committing it."""
    if action_type not in ACTION_TYPES:
        raise ValueError(f"Неизвестный action_type: {action_type}")
    entry = AuditLog(
        action_type=action_type,
        user_id=user_id,
        username=username,
        target_type=target_type,
        target_id=target_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
        meta=meta,
    )
    session.add(entry)
    session.flush()
    return _to_dict(entry)


class AuditService:
    def append(
        self,
        *,
        action_type: str,
        user_id: str | None = None,
        username: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        ip_address: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        """Добавляет запись в журнал. Единственный путь записи — без update/delete."""
        with session_scope() as s:
            return record_in_session(
                s,
                action_type=action_type,
                user_id=user_id,
                username=username,
                target_type=target_type,
                target_id=target_id,
                old_value=old_value,
                new_value=new_value,
                ip_address=ip_address,
                meta=meta,
            )

    def query(
        self,
        *,
        action_type: str | None = None,
        user_id: str | None = None,
        target_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Read-only выборка журнала с фильтрами (для роли Security)."""
        with session_scope() as s:
            stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
            if action_type is not None:
                stmt = stmt.where(AuditLog.action_type == action_type)
            if user_id is not None:
                stmt = stmt.where(AuditLog.user_id == user_id)
            if target_id is not None:
                stmt = stmt.where(AuditLog.target_id == target_id)
            if since is not None:
                stmt = stmt.where(AuditLog.created_at >= since)
            if until is not None:
                stmt = stmt.where(AuditLog.created_at <= until)
            stmt = stmt.limit(limit).offset(offset)
            return [_to_dict(e) for e in s.execute(stmt).scalars().all()]

    def distinct_users(self) -> list[dict]:
        """Distinct пользователей из журнала (user_id, username) — справочник фильтра."""
        with session_scope() as s:
            rows = s.execute(
                select(
                    AuditLog.user_id,
                    AuditLog.username,
                    func.count(AuditLog.id).label("cnt"),
                )
                .group_by(AuditLog.user_id, AuditLog.username)
                .order_by(func.count(AuditLog.id).desc())
            ).all()
            return [
                {"user_id": r.user_id, "username": r.username, "count": r.cnt}
                for r in rows
            ]


_INSTANCE: AuditService | None = None


def get_audit() -> AuditService:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = AuditService()
    return _INSTANCE


def record(
    user,
    action_type: str,
    target_type: str,
    target_id: str | None = None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    ip_address: str | None = None,
    meta: dict | None = None,
) -> dict:
    """Удобная обёртка поверх AuditService.append для эндпоинтов.

    user — доменная модель User (app.auth.models.User) или любой объект с
    атрибутами user_id/username (в disabled-режиме — public_user()).
    """
    return get_audit().append(
        action_type=action_type,
        user_id=getattr(user, "user_id", None) or "anonymous",
        username=getattr(user, "username", None) or "anonymous",
        target_type=target_type,
        target_id=target_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address,
        meta=meta,
    )
