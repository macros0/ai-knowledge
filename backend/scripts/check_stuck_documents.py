"""Read-only диагностика зависших в обработке документов.

Находит документы, которые «зависли» в активном статусе дольше порога.
UI покажет их как вечный BUSY-статус (processing/splitting/indexing) без явной
ошибки — это рантайм-залипание (например, зависший LLM-вызов), которое текущий
механизм сброса (reset_stale_statuses) ловит только при рестарте сервера.

Только SELECT — никаких мутаций БД. Инструмент для ручной проверки админом;
exit code 1 при наличии зависших (готов для будущего cron), без какой-либо
логики уведомлений/алертов.

Порог считается в Python (datetime.now(timezone.utc) - timedelta), а не через
диалект-специфичный NOW() - INTERVAL, чтобы скрипт работал и на SQLite (dev),
и на PostgreSQL (prod).

Запуск (из backend/):
    python scripts/check_stuck_documents.py            # порог 1 час
    python scripts/check_stuck_documents.py --hours 2
    python scripts/check_stuck_documents.py --statuses processing,splitting
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope

DEFAULT_STATUSES = ("processing", "splitting", "indexing")


def _as_utc(dt: datetime) -> datetime:
    """Приводит к aware-UTC (SQLite может вернуть naive datetime)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age(now: datetime, dt: datetime) -> str:
    td = now - _as_utc(dt)
    total_min = int(td.total_seconds() // 60)
    return f"{total_min // 60}ч {total_min % 60}м"


def main() -> int:
    parser = argparse.ArgumentParser(description="Поиск зависших в обработке документов.")
    parser.add_argument("--hours", type=float, default=1.0,
                        help="Порог «зависания» в часах (default: 1.0)")
    parser.add_argument("--statuses", type=str, default=",".join(DEFAULT_STATUSES),
                        help="Статусы для проверки, через запятую (default: processing,splitting,indexing)")
    args = parser.parse_args()

    statuses = [s.strip() for s in args.statuses.split(",") if s.strip()]
    if not statuses:
        print("Нет статусов для проверки", file=sys.stderr)
        return 2

    threshold = datetime.now(timezone.utc) - timedelta(hours=args.hours)

    with session_scope() as s:
        rows = s.execute(
            select(Document)
            .where(Document.status.in_(statuses), Document.updated_at < threshold)
            .order_by(Document.updated_at)
        ).scalars().all()

    now = datetime.now(timezone.utc)
    if not rows:
        print(f"Зависших документов нет (статусы {', '.join(statuses)}, старше {args.hours}ч).")
        return 0

    print(f"Найдено зависших: {len(rows)} (статусы {', '.join(statuses)}, старше {args.hours}ч):")
    for d in rows:
        print(f"  {d.id} | {d.filename} | {d.status} | {d.updated_at} | возраст {_age(now, d.updated_at)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
