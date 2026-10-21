"""Frozen revision-066 metadata. Never import mutable application models here."""
from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

def _utcnow():
    return datetime.now(timezone.utc)

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

    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True, index=True)
    schema_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_type: Mapped[str | None] = mapped_column(String(12), nullable=True)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ip_source: Mapped[str | None] = mapped_column(String(12), nullable=True)


class AuditOutbox(Base):
    """Immutable payload plus independently mutable local delivery state."""
    __tablename__ = "audit_outbox"
    __table_args__ = (
        CheckConstraint("status IN ('pending','held','stdout_written')", name="ck_audit_outbox_status"),
        CheckConstraint("attempt_count >= 0", name="ck_audit_outbox_attempt_count"),
        Index("ix_audit_outbox_eligible", "status", "next_attempt_at", "audit_id"),
    )
    audit_id: Mapped[int] = mapped_column(ForeignKey("audit_log.id"), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    last_error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stdout_written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
