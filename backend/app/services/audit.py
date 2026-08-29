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

from sqlalchemy import select

from app.db.models import AuditLog
from app.db.session import session_scope

# Канонический перечень типов действий (Этап 2а, текущий объём).
DOCUMENT_DELETE = "document_delete"
DOCUMENT_BULK_DELETE = "document_bulk_delete"
DOCUMENT_REGENERATE = "document_regenerate"
DOCUMENT_BULK_REGENERATE = "document_bulk_regenerate"
JOB_APPROVE = "job_approve"
JOB_CANCEL = "job_cancel"
USER_BLOCK = "user_block"
USER_UNBLOCK = "user_unblock"

ACTION_TYPES = frozenset(
    {
        DOCUMENT_DELETE,
        DOCUMENT_BULK_DELETE,
        DOCUMENT_REGENERATE,
        DOCUMENT_BULK_REGENERATE,
        JOB_APPROVE,
        JOB_CANCEL,
        USER_BLOCK,
        USER_UNBLOCK,
    }
)

TARGET_DOCUMENT = "document"
TARGET_JOB = "job"
TARGET_USER = "user"


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
        if action_type not in ACTION_TYPES:
            raise ValueError(f"Неизвестный action_type: {action_type}")
        with session_scope() as s:
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
            s.add(entry)
            s.flush()
            return _to_dict(entry)

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
