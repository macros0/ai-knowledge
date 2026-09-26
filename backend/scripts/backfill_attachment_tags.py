# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Бэкфилл программного тега «attachment» для уже загруженных документов.

Пайплайн (начиная с 2026-09-07) помечает тегом `attachment` ВСЕ концепты чанка,
если доля символов этого чанка, порождённая блоками распарсованных вложений,
>= okf_attachment_tag_threshold (см. okf_generator.attachment_shares). Для
документов, обработанных ДО этой возможности, скрипт размечает концепты задним
числом БЕЗ вызова LLM:

  1. re-parse uploads/<doc_id><ext> во ВРЕМЕННУЮ папку (бинарники вложений не
     дублируются в uploads; маркер рендерится «attachments/<basename>» — байт-в-
     байт как в пайплайне благодаря фиксу утечки saved_path 2026-09-07);
  2. строгое сравнение re-parsed чанков с document_chunks.content/source_id
     (по-порядку, all-or-nothing): НЕ совпало → skipped_parser_drift (парсер
     менялся между исходной обработкой и сейчас; лечение — regenerate, после
     которого тег проставит пайплайн нативно). НИКАКОГО нормализованного/частичного
     матчинга — ложный тег хуже отсутствия тега;
  3. доли чанков → для чанков >= порога дописать ATTACHMENT_TAG в
     okf_concepts.tags (без дублей);
  4. Qdrant: set_document_tags_payload(..., skip_chunks=True) — трогаем ТОЛЬКО
     concept-точки (chunk-точки не создаются и не обновляются вовсе).

Инварианты Этапа 2b соблюдены: БД (okf_concepts.tags) — единственный источник
признака; payload — проекция штатным set_payload без пере-эмбеддинга; point_id —
через vector_store.concept_point_id.

Сравнение и запись сериализованы с публикацией версии. Повторный запуск
восстанавливает Qdrant-проекцию даже при уже сохранённых SQL-тегах.

Запуск (из backend/, при доступной БД и Qdrant):
    python scripts/backfill_attachment_tags.py            # все документы
    python scripts/backfill_attachment_tags.py --doc-id <id>
    python scripts/backfill_attachment_tags.py --dry-run  # только отчёт
"""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docparser import parse_document
from docparser.source_model import ParseContext
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.services.okf_generator import ATTACHMENT_TAG, OKFGenerator
from app.services.generation_store import lock_document_write, lock_generation_read
from app.services.parser_supervisor import parse_document_supervised
from app.services.source_chunking import attachment_shares_by_source, chunk_blocks_by_source
from app.services.vector_store import VectorStore, concept_point_id

logger = logging.getLogger("backfill_attachment_tags")


def _iter_docs(doc_id: str | None) -> list[tuple[str, str]]:
    """(doc_id, filename) done-документов вне корзины с не-image вложениями."""
    with session_scope() as s:
        q = (
            select(Document.id, Document.filename)
            .where(Document.deleted_at.is_(None), Document.status == "done")
            .where(
                Document.id.in_(select(OkfAttachment.doc_id).where(OkfAttachment.kind != "image"))
            )
        )
        if doc_id:
            q = q.where(Document.id == doc_id)
        rows = s.execute(q).all()
    return [(r.id, r.filename) for r in rows]


def _load_stored_chunks(s, doc_id: str) -> list[DocumentChunk]:
    rows = (
        s.query(DocumentChunk)
        .filter(DocumentChunk.doc_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    return rows


def _first_divergence(a: list, b: list) -> int:
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


def _attachment_chunks(blocks: list, generator: OKFGenerator) -> list[dict]:
    """Use source boundaries for both text splitting and attachment coverage."""
    chunks = chunk_blocks_by_source(blocks, generator)
    shares = attachment_shares_by_source(blocks, generator)
    for chunk, share in zip(chunks, shares, strict=True):
        chunk["content"] = chunk["content"].replace("\r\n", "\n").replace("\r", "\n")
        chunk["share"] = share
    return chunks


def _sync_concept_tags(doc_id: str, vector_store: VectorStore) -> None:
    # The publication may have changed since the SQL edit. Project the current
    # canonical version, and keep its lock until Qdrant acknowledges the write.
    with session_scope() as session:
        generations = lock_generation_read(session, [doc_id])
        document = session.get(Document, doc_id)
        if document is None or document.deleted_at is not None:
            return
        rows = session.query(OkfConcept).filter_by(doc_id=doc_id).all()
        points = [(concept_point_id(doc_id, row.slug, generation_id=generations.get(doc_id)),
                   list(row.tags or [])) for row in rows]
        vector_store.ensure_collection()
        vector_store.set_document_tags_payload(
            doc_id, [tag.tag_rel.canonical_text for tag in document.tags_rel],
            concept_points=points, skip_chunks=True,
        )


def process_doc(
    doc_id: str,
    filename: str,
    settings,
    generator: OKFGenerator,
    vector_store: VectorStore,
    dry_run: bool = False,
) -> dict:
    ext = Path(filename).suffix.lower()
    src = settings.uploads_dir / f"{doc_id}{ext}"
    if not src.is_file():
        return {"status": "skipped_no_source"}

    # 1. re-parse во временную папку (маркеры получают saved_path → строка «файл:»)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            context = ParseContext(filename, mail_enabled=settings.mail_import_enabled)
            if settings.parser_supervisor_enabled and parse_document.__module__.startswith("docparser"):
                supervised = parse_document_supervised(
                    src, filename, attachments_dir=Path(tmp),
                    timeout_seconds=settings.parser_timeout_seconds,
                    max_memory_mb=settings.parser_max_memory_mb,
                    max_concurrent=settings.parser_max_concurrent,
                    mail_enabled=settings.mail_import_enabled,
                )
                blocks = supervised.blocks
            else:
                blocks = parse_document(str(src), filename, attachments_dir=tmp, context=context)
            reparsed = _attachment_chunks(blocks, generator)
    except Exception as exc:
        return {"status": "error", "detail": f"parse: {exc}"}

    if not any(chunk["share"] for chunk in reparsed):
        return {"status": "no_attachment_spans"}

    # 2. доли чанков и строгое сравнение с сохранёнными чанками
    with session_scope() as s:
        if not lock_document_write(s, doc_id, allow_deleted=False):
            return {"status": "skipped_missing_or_deleted"}
        stored = _load_stored_chunks(s, doc_id)
        actual = [(row.source_id or "root", row.content) for row in stored]
        expected = [(chunk["source_id"], chunk["content"]) for chunk in reparsed]
        if actual != expected:
            first = _first_divergence(actual, expected)
            return {
                "status": "skipped_parser_drift",
                "detail": f"chunk #{first} расходится (stored={len(stored)}, reparsed={len(reparsed)})",
            }
        eligible = [row.chunk_index for row, chunk in zip(stored, reparsed)
                    if chunk["share"] >= settings.okf_attachment_tag_threshold]
        if not eligible:
            return {"status": "matched_no_changes"}
        # 3. Concept tags are edited only after checking the current chunk text
        # and provenance inside the same writer transaction.
        rows = (
            s.query(OkfConcept)
            .filter(OkfConcept.doc_id == doc_id, OkfConcept.chunk_index.in_(eligible))
            .all()
        )
        untagged = [c for c in rows if ATTACHMENT_TAG not in (c.tags or [])]
        changed = len(untagged)

        if not dry_run:
            for c in untagged:
                c.tags = list(c.tags or []) + [ATTACHMENT_TAG]

    if dry_run:
        return {"status": "tagged" if changed else "matched_no_changes",
                "tagged": changed, "chunks": len(eligible), "dry_run": True}

    # 4. Qdrant: только concept-точки (skip_chunks=True — chunk-слой не трогаем)
    try:
        _sync_concept_tags(doc_id, vector_store)
    except Exception as exc:
        logger.warning("%s: Qdrant-синк не удался (%s) — БД уже обновлена, повторите прогон", doc_id, exc)
        return {"status": "error", "tagged": changed, "detail": f"Qdrant sync failed: {exc}; rerun to retry"}

    return {"status": "tagged" if changed else "matched_no_changes", "tagged": changed, "chunks": len(eligible)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл тега «attachment» концептам из вложений.")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать один документ")
    parser.add_argument("--dry-run", action="store_true", help="Только отчёт, без записи")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    generator = OKFGenerator()
    vector_store = VectorStore()

    docs = _iter_docs(args.doc_id)
    if args.doc_id and not docs:
        print(f"Документ {args.doc_id} не найден (или не done/в корзине/без вложений)")
        sys.exit(1)

    counters = {
        "tagged": 0,
        "matched_no_changes": 0,
        "skipped_parser_drift": 0,
        "no_attachment_spans": 0,
        "skipped_no_source": 0,
        "skipped_missing_or_deleted": 0,
        "errors": 0,
    }
    drift = []
    for doc_id, filename in docs:
        r = process_doc(doc_id, filename, settings, generator, vector_store, dry_run=args.dry_run)
        status = r["status"]
        if status == "tagged":
            counters["tagged"] += 1
            logger.info(
                "%s: +%d концептов с тегом attachment (%d чанков >= порога)%s",
                doc_id, r["tagged"], r["chunks"], " (dry-run)" if r.get("dry_run") else "",
            )
        elif status == "matched_no_changes":
            counters["matched_no_changes"] += 1
            logger.info("%s: чанки совпали, но тег не нужен (ни один чанк >= порога или уже размечено)", doc_id)
        elif status == "skipped_parser_drift":
            counters["skipped_parser_drift"] += 1
            drift.append((doc_id, r["detail"]))
            logger.warning("%s: parser drift (%s) — remediation: regenerate", doc_id, r["detail"])
        elif status == "no_attachment_spans":
            counters["no_attachment_spans"] += 1
            logger.info("%s: from_attachment-блоков нет (нечего размечать)", doc_id)
        elif status == "skipped_no_source":
            counters["skipped_no_source"] += 1
            logger.info("%s: исходный файл отсутствует", doc_id)
        elif status == "skipped_missing_or_deleted":
            counters[status] += 1
            logger.info("%s: документ отсутствует или в корзине", doc_id)
        else:
            counters["errors"] += 1
            logger.error("%s: %s", doc_id, r.get("detail", "неизвестная ошибка"))

    print(
        "Итог: tagged=%d, matched_no_changes=%d, skipped_parser_drift=%d, "
        "no_attachment_spans=%d, skipped_no_source=%d, skipped_missing_or_deleted=%d, errors=%d"
        % tuple(counters.values())
    )
    if drift:
        print("Требуют regenerate (parser drift):")
        for doc_id, detail in drift:
            print(f"  {doc_id}: {detail}")


if __name__ == "__main__":
    main()
