"""Блоклист пользователей (единственное активное действие роли Security).

Security может временно заблокировать пользователя/сессию — без права изменять
данные. Блокировка хранится в user_blocks и проверяется в require_user (auth):
пользователь из активного блоклиста не проходит аутентификацию, даже если его
сессия и группы валидны.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import UserBlock
from app.db.session import session_scope


def _is_active(block: UserBlock) -> bool:
    if not block.is_active:
        return False
    if block.expires_at is not None:
        return block.expires_at > datetime.now(timezone.utc)
    return True


class Blocklist:
    def is_blocked(self, external_id: str) -> bool:
        """True, если у пользователя есть активная (не истёкшая) блокировка."""
        if not external_id or external_id == "anonymous":
            return False
        with session_scope() as s:
            rows = (
                s.execute(
                    select(UserBlock).where(UserBlock.external_id == external_id)
                )
                .scalars()
                .all()
            )
            return any(_is_active(b) for b in rows)

    def block(
        self,
        external_id: str,
        *,
        blocked_by: str,
        username: str | None = None,
        reason: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict:
        with session_scope() as s:
            block = UserBlock(
                external_id=external_id,
                username=username,
                reason=reason,
                blocked_by=blocked_by,
                expires_at=expires_at,
                is_active=True,
            )
            s.add(block)
            s.flush()
            return {"id": block.id, "external_id": block.external_id}

    def unblock(self, external_id: str) -> int:
        """Снимает все активные блокировки пользователя. Возвращает число записей."""
        with session_scope() as s:
            rows = (
                s.execute(
                    select(UserBlock).where(
                        UserBlock.external_id == external_id,
                        UserBlock.is_active.is_(True),
                    )
                )
                .scalars()
                .all()
            )
            n = 0
            for b in rows:
                b.is_active = False
                n += 1
            return n

    def list_active(self) -> list[dict]:
        """Список блокировок со статусом is_active=True (для панели Security).

        Возвращает и уже истёкшие по времени записи (их is_active ещё True) —
        поле active показывает фактическое состояние с учётом expires_at.
        """
        with session_scope() as s:
            rows = (
                s.execute(
                    select(UserBlock)
                    .where(UserBlock.is_active.is_(True))
                    .order_by(UserBlock.created_at.desc())
                )
                .scalars()
                .all()
            )
            return [
                {
                    "id": b.id,
                    "external_id": b.external_id,
                    "username": b.username,
                    "reason": b.reason,
                    "blocked_by": b.blocked_by,
                    "created_at": b.created_at,
                    "expires_at": b.expires_at,
                    "active": _is_active(b),
                }
                for b in rows
            ]


_INSTANCE: Blocklist | None = None


def get_blocklist() -> Blocklist:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Blocklist()
    return _INSTANCE
