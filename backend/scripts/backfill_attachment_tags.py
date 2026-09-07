# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Бэкфилл программного тега «attachment» для уже загруженных документов.

Пайплайн (начиная с 2026-09-07) помечает тегом `attachment` ВСЕ концепты чанка,
если доля символов этого чанка, порождённая блоками распарсованных вложений,
>= okf_attachment_tag_threshold (см. okf_generator.attachment_shares). Для
документов, обработанных ДО этой возможности, скрипт размечает концепты задним
числом БЕЗ вызова LLM:

  1. re-parse uploads/<doc_id><ext> во ВРЕМЕННУЮ папку (бинарники вложений не
     дублируются в uploads; маркер рендерится «attachments/<basename>» — байт-в-
     байт как в пайплайне благодаря фиксу утечки saved_path 2026-09-07);
  2. строгое сравнение re-parsed чанков со списком document_chunks.content
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

Идемпотентен: повторный запуск ничего не меняет (тег уже стоит — нет записей и
Qdrant-вызовов).

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

from docparser import markdown_attachment_spans, parse_document
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.services.okf_generator import ATTACHMENT_TAG, OKFGenerator
from app.services.registry import get_registry
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


def _load_stored_chunks(s, doc_id: str) -> list[str]:
    rows = (
        s.query(DocumentChunk)
        .filter(DocumentChunk.doc_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    return [c.content for c in rows]


def _first_divergence(a: list[str], b: list[str]) -> int:
    limit = min(len(a), len(b))
    for i in range(limit):
        if a[i] != b[i]:
            return i
    return limit


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
            blocks = parse_document(str(src), filename, attachments_dir=tmp)
            markdown, spans = markdown_attachment_spans(blocks)
    except Exception as exc:
        return {"status": "error", "detail": f"parse: {exc}"}

    if not spans:
        return {"status": "no_attachment_spans"}

    # 2. доли чанков и строгое сравнение с сохранёнными чанками
    reparsed = generator.chunk_text(markdown)
    with session_scope() as s:
        stored = _load_stored_chunks(s, doc_id)
    if len(reparsed) != len(stored) or any(a != b for a, b in zip(reparsed, stored)):
        first = _first_divergence(stored, reparsed)
        return {
            "status": "skipped_parser_drift",
            "detail": f"chunk #{first} расходится (stored={len(stored)}, reparsed={len(reparsed)})",
        }

    shares = generator.attachment_shares(markdown, spans)
    eligible = [i for i, share in enumerate(shares) if share >= settings.okf_attachment_tag_threshold]
    if not eligible:
        return {"status": "matched_no_changes"}

    # 3. концепты чанков >= порога, ещё без тега
    with session_scope() as s:
        rows = (
            s.query(OkfConcept)
            .filter(OkfConcept.doc_id == doc_id, OkfConcept.chunk_index.in_(eligible))
            .all()
        )
        untagged = [c for c in rows if ATTACHMENT_TAG not in (c.tags or [])]
        if not untagged:
            return {"status": "matched_no_changes"}

        if not dry_run:
            for c in untagged:
                c.tags = list(c.tags or []) + [ATTACHMENT_TAG]

    if dry_run:
        return {"status": "tagged", "tagged": len(untagged), "chunks": len(eligible), "dry_run": True}

    # 4. Qdrant: только concept-точки (skip_chunks=True — chunk-слой не трогаем)
    global_tags = list((get_registry().get(doc_id) or {}).get("tags") or [])
    concept_points = [
        (concept_point_id(doc_id, c.slug), list(c.tags or []))
        for c in untagged
    ]
    try:
        vector_store.ensure_collection()
        vector_store.set_document_tags_payload(
            doc_id, global_tags, concept_points=concept_points, skip_chunks=True
        )
    except Exception as exc:
        logger.warning("%s: Qdrant-синк не удался (%s) — БД уже обновлена, повторите прогон", doc_id, exc)

    return {"status": "tagged", "tagged": len(untagged), "chunks": len(eligible)}


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
        else:
            counters["errors"] += 1
            logger.error("%s: %s", doc_id, r.get("detail", "неизвестная ошибка"))

    print(
        "Итог: tagged=%d, matched_no_changes=%d, skipped_parser_drift=%d, "
        "no_attachment_spans=%d, skipped_no_source=%d, errors=%d" % tuple(counters.values())
    )
    if drift:
        print("Требуют regenerate (parser drift):")
        for doc_id, detail in drift:
            print(f"  {doc_id}: {detail}")


if __name__ == "__main__":
    main()
