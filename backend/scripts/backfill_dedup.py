"""Бэкфилл отпечатков дедупликации по существующим документам (Этап 4.2).

Колонки file_hash/content_hash/minhash/LSH-бакеты были добавлены в миграции
Этапа 4 ПОСЛЕ того, как текущий корпус уже был загружен — исторические документы
остались без отпечатков, поэтому дедупликация по ним не срабатывала. Скрипт
досчитывает отпечатки для всех (или одного, --doc-id) документов.

Для каждого документа:
  1. file_hash (уровень 1) — SHA-256 исходного файла из uploads_dir; пропуск с
     WARNING, если исходника нет.
  2. content_hash + minhash + LSH-бакеты (уровни 2/3) — из текста документа,
     из канонических SQL-чанков после ensure_chunks. Сохранённый отпечаток
     письма остаётся неизменным. Пустой текст пропускается (иначе все «пустые»
     документы совпали бы по content_hash="").

Второе назначение — ПАРНЫЙ прогон к `rebuild_sparse.py` при смене алфавита или
нормализации токенайзера (`sparse.TOKEN_EXTRA_LETTERS` / `normalize_for_tokens`):
`deduplication._shingles` строится тем же токенайзером, и после смены формулы
старые minhash-подписи несравнимы с новыми. Пересчёт возвращает LSH-таблицу в
согласованное с индексом состояние.

Под блокировкой документа читает опубликованный текст и в одной транзакции
обновляет отпечатки и LSH-бакеты. Удалённые документы пропускаются.

Запуск (из backend/):
    python scripts/backfill_dedup.py            # все документы
    python scripts/backfill_dedup.py --doc-id <id>   # один документ
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.deduplication import (
    _duplicate_ids,
    apply_document_signature,
    find_duplicates_for_document,
    prepare_document_signature,
    refresh_duplicate_flags,
    sha256_file,
)
from app.services.generation_store import lock_document_write
from app.services.pipeline import Pipeline

logger = logging.getLogger("backfill_dedup")


def _all_docs() -> list[tuple[str, str]]:
    with session_scope() as s:
        return [(row.id, row.filename) for row in s.execute(
            select(Document.id, Document.filename).where(Document.deleted_at.is_(None))
        ).all()]


def _one_doc(doc_id: str) -> tuple[str, str] | None:
    with session_scope() as s:
        row = s.execute(
            select(Document.id, Document.filename).where(Document.id == doc_id, Document.deleted_at.is_(None))
        ).first()
        return (row.id, row.filename) if row else None


def process_doc(doc_id: str, filename: str, pipeline: Pipeline, settings) -> dict:
    result: dict = {"file_hash": False, "content": False, "skipped": [], "error": None}

    try:
        pipeline.ensure_chunks(doc_id)
        with session_scope() as session:
            if not lock_document_write(session, doc_id, allow_deleted=False):
                result["skipped"].append("missing_or_deleted")
                return result
            document = session.get(Document, doc_id)
            previous_ids = _duplicate_ids(find_duplicates_for_document(doc_id))
            src = settings.uploads_dir / f"{doc_id}{Path(document.filename).suffix.lower()}"
            has_source = src.is_file()
            if has_source:
                document.file_hash = sha256_file(src)
            else:
                result["skipped"].append("no_source_file")
            markdown = "\n\n".join(session.scalars(
                select(DocumentChunk.content).where(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
            ).all())
            has_text = bool(markdown.strip())
            if has_text:
                signature = prepare_document_signature(markdown, document.mail_fingerprint)
                apply_document_signature(session, doc_id, signature)
            else:
                result["skipped"].append("empty_markdown")
        result["file_hash"] = has_source
        result["content"] = has_text
        if has_text:
            refresh_duplicate_flags(doc_id, previous_ids)
    except Exception as exc:
        result["error"] = str(exc)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл отпечатков дедупликации.")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать один документ")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = get_settings()
    pipeline = Pipeline()

    if args.doc_id:
        row = _one_doc(args.doc_id)
        if row is None:
            print(f"Документ {args.doc_id} не найден")
            sys.exit(1)
        docs = [row]
    else:
        docs = _all_docs()

    counters = {"file_hash": 0, "content": 0, "skipped": 0, "errors": 0}
    for doc_id, filename in docs:
        r = process_doc(doc_id, filename, pipeline, settings)
        if r["file_hash"]:
            counters["file_hash"] += 1
        if r["content"]:
            counters["content"] += 1
        if r["skipped"]:
            counters["skipped"] += 1
            logger.warning("%s: пропущено %s", doc_id, ", ".join(r["skipped"]))
        if r["error"]:
            counters["errors"] += 1
            logger.error("%s: %s", doc_id, r["error"])
        elif not r["skipped"]:
            logger.info("%s: file_hash + content/minhash", doc_id)

    print(
        f"Итог: file_hash={counters['file_hash']}, content={counters['content']}, "
        f"skipped={counters['skipped']}, errors={counters['errors']} (всего документов: {len(docs)})"
    )


if __name__ == "__main__":
    main()
