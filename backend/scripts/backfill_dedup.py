"""Бэкфилл отпечатков дедупликации по существующим документам (Этап 4.2).

Колонки file_hash/content_hash/minhash/LSH-бакеты были добавлены в миграции
Этапа 4 ПОСЛЕ того, как текущий корпус уже был загружен — исторические документы
остались без отпечатков, поэтому дедупликация по ним не срабатывала. Скрипт
досчитывает отпечатки для всех (или одного, --doc-id) документов.

Для каждого документа:
  1. file_hash (уровень 1) — SHA-256 исходного файла из uploads_dir; пропуск с
     WARNING, если исходника нет.
  2. content_hash + minhash + LSH-бакеты (уровни 2/3) — из текста документа,
     с приоритетом готовых чанков (ensure_chunks: бандл → staging → ленивый
     backfill из исходника). Пустой текст пропускается (иначе все «пустые»
     документы совпали бы по content_hash="").

Второе назначение — ПАРНЫЙ прогон к `rebuild_sparse.py` при смене алфавита или
нормализации токенайзера (`sparse.TOKEN_EXTRA_LETTERS` / `normalize_for_tokens`):
`deduplication._shingles` строится тем же токенайзером, и после смены формулы
старые minhash-подписи несравнимы с новыми. Пересчёт возвращает LSH-таблицу в
согласованное с индексом состояние.

Идемпотентен: set_file_hash/index_document перезаписывают значения; index_document
в одной транзакции очищает и пересоздаёт LSH-бакеты — повторный запуск безопасен.

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
from app.db.models import Document
from app.db.session import session_scope
from app.services.deduplication import index_document, set_file_hash, sha256_file
from app.services.pipeline import Pipeline

logger = logging.getLogger("backfill_dedup")


def _all_docs() -> list[tuple[str, str]]:
    with session_scope() as s:
        return [(row.id, row.filename) for row in s.execute(select(Document.id, Document.filename)).all()]


def _one_doc(doc_id: str) -> tuple[str, str] | None:
    with session_scope() as s:
        row = s.execute(
            select(Document.id, Document.filename).where(Document.id == doc_id)
        ).first()
        return (row.id, row.filename) if row else None


def _chunk_markdown(chunks_dir: Path) -> str:
    files = sorted(
        chunks_dir.glob("chunk_*.md"),
        key=lambda p: int(p.stem.split("_")[-1]),
    )
    return "\n\n".join(f.read_text(encoding="utf-8") for f in files)


def process_doc(doc_id: str, filename: str, pipeline: Pipeline, settings) -> dict:
    result: dict = {"file_hash": False, "content": False, "skipped": [], "error": None}

    ext = Path(filename).suffix.lower()
    src = settings.uploads_dir / f"{doc_id}{ext}"
    if src.is_file():
        set_file_hash(doc_id, sha256_file(src))
        result["file_hash"] = True
    else:
        result["skipped"].append("no_source_file")

    try:
        pipeline.ensure_chunks(doc_id)
        markdown = _chunk_markdown(settings.okf_dir / doc_id / "chunks")
        if markdown.strip():
            index_document(doc_id, markdown)
            result["content"] = True
            from app.services.deduplication import find_duplicates_for_document
            from app.services.registry import get_registry

            dup = find_duplicates_for_document(doc_id)
            if dup["level2"] or dup["level3"]:
                get_registry().update(doc_id, has_duplicates=True)
        else:
            result["skipped"].append("empty_markdown")
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
