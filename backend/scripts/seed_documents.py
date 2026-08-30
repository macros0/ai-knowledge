"""Синтетический сидер документов для нагрузочного теста списка.

Создаёт N документов напрямую в той же БД, что использует поднятый backend
(читается .env -> DATABASE_URL через app.config.get_settings — НЕ dev-SQLite).
Вставка идёт bulk-ом в одну транзакцию (не registry.create, который делает
транзакцию + get на каждый документ).

Запуск (из backend/, требуется поднятый Postgres):
    python scripts/seed_documents.py --count 1000
    python scripts/seed_documents.py --count 1000 --owner demo.user
    python scripts/seed_documents.py --clean    # удалить только сиднутые

Сиднутые документы помечаются префиксом имени `loadtest_` — `--clean`
удаляет только их, не трогая реальные данные.
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.db.models import (
    Document,
    DocumentStaging,
    DocumentTag,
    OkfAttachment,
    OkfConcept,
)
from app.db.session import get_session_factory

SEED_PREFIX = "loadtest_"
SEED_LIKE = f"{SEED_PREFIX}%"

# ~2/3 «готовых», остальные — живые статусы (поллинг/ошибки) для реалистичности.
STATUSES = ["done"] * 8 + ["processing", "paused", "failed", "uploaded"]
OTHER_UPLOADERS = ["demo.editor", "demo.admin", "demo.viewer"]
TAGS_POOL = [["net"], ["soc"], ["pro"], ["net", "pro"], ["doc"], []]
CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def clean() -> int:
    """Удаляет только сиднутые документы (по префиксу имени) и их дочерние строки."""
    factory = get_session_factory()
    with factory() as s:
        sub = select(Document.id).where(Document.filename.like(SEED_LIKE))
        for model in (OkfConcept, OkfAttachment, DocumentStaging, DocumentTag):
            s.execute(delete(model).where(model.doc_id.in_(sub)))
        result = s.execute(delete(Document).where(Document.filename.like(SEED_LIKE)))
        s.commit()
        return result.rowcount or 0


def seed(count: int, owner: str, buckets: int = 0) -> int:
    """Вставляет count синтетических документов bulk-ом в одну транзакцию.

    При buckets > 0 имена разносятся по buckets префикс-группам
    (`loadtest_<bucket:03d>_<i:05d>.docx`), чтобы маской можно было получить
    контролируемую долю совпадений для теста фильтра: при buckets=100 —
    `loadtest_*` → 100%, `loadtest_00*` → 10%, `loadtest_003_*` → 1%.
    """
    now = datetime.now(timezone.utc)
    docs: list[Document] = []
    for i in range(count):
        uploaded_by = owner if (i % 10) < 9 else OTHER_UPLOADERS[i % len(OTHER_UPLOADERS)]
        status = STATUSES[i % len(STATUSES)]
        created = now - timedelta(days=i % 30, hours=i % 24)
        tags = TAGS_POOL[i % len(TAGS_POOL)]
        if buckets and buckets > 0:
            filename = f"{SEED_PREFIX}{i % buckets:03d}_{i:05d}.docx"
        else:
            filename = f"{SEED_PREFIX}{i:05d}_{uploaded_by.split('.')[-1]}.docx"
        docs.append(
            Document(
                id=secrets.token_hex(8),
                filename=filename,
                content_type=CONTENT_TYPE,
                size=(i % 500 + 1) * 1024,
                status=status,
                error="синтетический сбой" if status == "failed" else None,
                total_chunks=(i % 20) + 1,
                processed_chunks=(i % 20) + 1 if status == "done" else i % 20,
                current_chunk=None if status == "done" else (i % 20) + 1,
                okf_concept_count=i % 30,
                created_at=created,
                updated_at=created,
                uploaded_by=uploaded_by,
                tags_rel=[DocumentTag(tag=t) for t in tags],
            )
        )
    factory = get_session_factory()
    with factory() as s:
        s.add_all(docs)
        s.commit()
    return len(docs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Синтетический сидер документов.")
    parser.add_argument("--count", type=int, default=1000, help="Кол-во документов")
    parser.add_argument(
        "--owner",
        type=str,
        default="loadtest.owner",
        help="uploaded_by владельца большинства документов (для scope=mine)",
    )
    parser.add_argument(
        "--buckets",
        type=int,
        default=0,
        help="Разнести имена по N префикс-группам для теста фильтра (0 = обычные имена)",
    )
    parser.add_argument(
        "--clean", action="store_true", help="Удалить сиднутые документы и выйти"
    )
    args = parser.parse_args()

    if args.clean:
        removed = clean()
        print(f"Удалено сиднутых документов: {removed}")
        return

    removed = clean()
    created = seed(args.count, args.owner, args.buckets)
    print(f"Удалено перед сидом: {removed}; создано: {created} (owner={args.owner})")


if __name__ == "__main__":
    main()
