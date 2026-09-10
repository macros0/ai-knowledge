"""Бэкфилл `source_locale` для исторических документов (Этап 7 фаза D, фильтр).

После перехода на py3langid (фаза 1) исторические документы, загруженные до
релиза, несут `source_locale` либо от старой эвристики, либо NULL. Скрипт
досчитывает язык заново для всех (или одного, --doc-id) документов.

Текст берётся из `document_chunks` (БД — единственный источник истины после
Этапа 2b), НЕ из FS-бандлов: чанки читаются в порядке chunk_index, склеиваются и
срезаются до 100_000 символов — тот же вход, что у `pipeline._finalize`.

Защита ручной правки: документы с `source_locale_source == 'manual'`
пропускаются — значение, поправленное человеком, не перезаписывается (тот же
guard, что и в `pipeline._source_locale_fields`).

Идемпотентен: `detect_language` детерминирован, повторный прогон ставит те же
значения. `--dry-run` печатает планируемые изменения без записи.

Запуск (из backend/):
    python scripts/backfill_source_locale.py            # все документы
    python scripts/backfill_source_locale.py --doc-id <id>
    python scripts/backfill_source_locale.py --dry-run
    python scripts/backfill_source_locale.py --payload  # только синк Qdrant payload
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.language import detect_language
from app.services.registry import get_registry
from app.services.vector_store import VectorStore

logger = logging.getLogger("backfill_source_locale")


def _chunk_text(s, doc_id: str) -> str:
    rows = s.execute(
        select(DocumentChunk.content)
        .where(DocumentChunk.doc_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
    ).scalars().all()
    return "".join(rows)


def _iter_docs(doc_id: str | None):
    with session_scope() as s:
        q = select(Document.id, Document.filename, Document.source_locale,
                   Document.source_locale_source)
        if doc_id:
            q = q.where(Document.id == doc_id)
        return [(r.id, r.filename, r.source_locale, r.source_locale_source)
                for r in s.execute(q).all()]


def _iter_all_docs():
    with session_scope() as s:
        rows = s.execute(select(Document.id, Document.source_locale)).all()
    return [(r.id, r.source_locale) for r in rows]


def payload_backfill(dry_run: bool) -> None:
    """Синк payload source_locale на ВСЕ точки всех документов (включая корзинные).

    Пишет денормализованную проекцию языка в Qdrant через set_payload по doc_id —
    без пере-эмбеддинга. Нужен один раз после добавления поля в payload: точки,
    проиндексированные до релиза, несут ключ source_locale только после этого
    прогона (до него фильтр unknown находит их через is_empty).
    """
    vs = VectorStore()
    docs = _iter_all_docs()
    updated = 0
    errors = 0
    for doc_id, source_locale in docs:
        if dry_run:
            logger.info("%s: payload -> %s (dry-run)", doc_id, source_locale)
            continue
        try:
            vs.set_document_source_locale_payload(doc_id, source_locale)
            updated += 1
        except Exception as exc:
            errors += 1
            logger.error("%s: %s", doc_id, exc)
    print(f"Итог payload: synced={updated}, errors={errors} (всего документов: {len(docs)})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл source_locale документов.")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать один документ")
    parser.add_argument("--dry-run", action="store_true", help="Только показать изменения")
    parser.add_argument("--payload", action="store_true",
                        help="Только синк Qdrant payload (без детекции/БД)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.payload:
        payload_backfill(args.dry_run)
        return

    registry = get_registry()
    docs = _iter_docs(args.doc_id)
    if not docs:
        print("Документы не найдены" if args.doc_id else "Нет документов")
        sys.exit(1)

    counters = {"updated": 0, "manual": 0, "unchanged": 0, "empty": 0}
    for doc_id, filename, old_locale, old_source in docs:
        if old_source == "manual":
            counters["manual"] += 1
            logger.info("%s: manual — пропуск (язык задан вручную)", doc_id)
            continue

        with session_scope() as s:
            text = _chunk_text(s, doc_id)
        detected = detect_language(text[:100_000])
        new_source = "detected" if detected else None

        if detected == old_locale:
            counters["unchanged"] += 1
            logger.info("%s: %s — без изменений", doc_id, detected)
            continue
        if detected is None:
            counters["empty"] += 1

        if args.dry_run:
            logger.info("%s: %s -> %s (dry-run)", doc_id, old_locale, detected)
        else:
            registry.update(doc_id, source_locale=detected, source_locale_source=new_source)
            logger.info("%s: %s -> %s", doc_id, old_locale, detected)
        counters["updated"] += 1

    print(
        f"Итог: updated={counters['updated']}, unchanged={counters['unchanged']}, "
        f"manual={counters['manual']}, empty={counters['empty']} "
        f"(всего документов: {len(docs)})"
    )


if __name__ == "__main__":
    main()
