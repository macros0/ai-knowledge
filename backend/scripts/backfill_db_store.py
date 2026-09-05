# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Backfill исторического корпуса в canonical-таблицы БД (Этап 2b, Фаза 2).

До Фазы 1 обработанное знание жило в FS-бандлах и payload Qdrant; новые
canonical-таблицы (document_chunks / okf_attachments / okf_concepts.*provenance)
заполнялись только для документов, обработанных после Фазы 1. Этот скрипт
переносит исторический корпус:

  - chunk_XX.md (бандл) → document_chunks (content, section_title, hash, char_count);
  - frontmatter `attachments` + бинарники → okf_attachments (копирование в
    uploads/<id>/attachments/, потоковый sha256/size, нормализованный saved_path
    "attachments/<имя>"); is_processable/extraction_status legacy-данными
    не восстанавливаются — ставится консервативно "saved"/False;
  - frontmatter `created_at` → okf_concepts.generated_at (где NULL, TSTZ).

Источники чанков по приоритету: бандл chunks/*.md → Qdrant chunk-payload
(контент обрезан до okf_max_chunk_index_chars) → re-parse исходника без LLM.

Идемпотентен: документы с уже заполненными таблицами пропускаются (--force
перезаписывает). Per-doc устойчив: сбой одного документа логируется и не
останавливает остальные.

Запуск (из backend/, стек поднят):
    python scripts/backfill_db_store.py
    python scripts/backfill_db_store.py --doc-id <id>
    python scripts/backfill_db_store.py --force
    python scripts/backfill_db_store.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.services.attachment_store import replace_attachments
from app.services.bundle import parse_okf_file
from app.services.chunk_store import replace_chunks
from app.services.pipeline import _extract_section_title

logger = logging.getLogger("backfill_db_store")


def iter_done_docs(doc_id: str | None = None) -> list[tuple[str, str]]:
    """(doc_id, filename) активных done-документов (или одного, если задан)."""
    with session_scope() as s:
        q = select(Document.id, Document.filename).where(
            Document.deleted_at.is_(None),
            Document.status == "done",
        )
        if doc_id:
            q = q.where(Document.id == doc_id)
        rows = s.execute(q).all()
    return [(r.id, r.filename) for r in rows]


def _row_counts(doc_id: str) -> tuple[int, int]:
    with session_scope() as s:
        chunks = s.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).count()
        atts = s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).count()
    return chunks, atts


# --- Чанки ------------------------------------------------------------------

def _read_bundle_chunks(bundle_dir: Path) -> list[tuple[int, str]]:
    chunks_dir = bundle_dir / "chunks"
    if not chunks_dir.is_dir():
        return []
    result: list[tuple[int, str]] = []
    for p in sorted(chunks_dir.glob("chunk_*.md"), key=lambda p: int(p.stem.split("_")[-1])):
        result.append((int(p.stem.split("_")[-1]), p.read_text(encoding="utf-8")))
    return result


def _read_qdrant_chunks(doc_id: str, vector_store) -> list[tuple[int, str]]:
    """Fallback: chunk-payload из Qdrant (content обрезан до okf_max_chunk_index_chars)."""
    from qdrant_client.http import models as qm

    rows: list[tuple[int, str]] = []
    next_offset = None
    flt = qm.Filter(
        must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
            qm.FieldCondition(key="point_type", match=qm.MatchValue(value="chunk")),
        ]
    )
    while True:
        batch, next_offset = vector_store.client.scroll(
            collection_name=vector_store.collection,
            scroll_filter=flt,
            limit=500,
            with_payload=True,
            with_vectors=False,
            offset=next_offset,
        )
        for rec in batch:
            payload = rec.payload or {}
            ci = payload.get("chunk_index")
            if ci is not None:
                rows.append((int(ci), payload.get("content") or ""))
        if next_offset is None:
            break
    rows.sort()
    return rows


def _reparse_chunks(doc_id: str, filename: str, settings) -> list[tuple[int, str]]:
    """Fallback последней надежды: re-parse исходника без LLM."""
    ext = Path(filename).suffix.lower()
    src = settings.uploads_dir / f"{doc_id}{ext}"
    if not src.is_file():
        return []
    from docparser import blocks_to_markdown, parse_document

    from app.services.okf_generator import OKFGenerator

    blocks = parse_document(src)
    markdown = blocks_to_markdown(blocks)
    chunks = OKFGenerator().chunk_text(markdown)
    return list(enumerate(chunks))


def backfill_chunks(
    doc_id: str, filename: str, settings, force: bool = False, dry_run: bool = False
) -> int:
    """Переносит чанки документа в document_chunks. Возвращает число строк (0 = скип)."""
    chunk_count, _ = _row_counts(doc_id)
    if chunk_count and not force:
        return 0
    chunks = _read_bundle_chunks(settings.okf_dir / doc_id)
    if not chunks:
        try:
            from app.services.vector_store import VectorStore

            chunks = _read_qdrant_chunks(doc_id, VectorStore())
            if chunks:
                logger.info("[%s] чанки восстановлены из Qdrant payload (обрезка до лимита)", doc_id)
        except Exception as exc:
            logger.warning("[%s] Qdrant-fallback чанков не удался: %s", doc_id, exc)
    if not chunks:
        chunks = _reparse_chunks(doc_id, filename, settings)
        if chunks:
            logger.info("[%s] чанки перестроены из исходника (без LLM)", doc_id)
    if not chunks:
        return 0
    if dry_run:
        return len(chunks)
    rows = [
        {
            "chunk_index": idx,
            "section_title": _extract_section_title(text),
            "content": text,
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "char_count": len(text),
        }
        for idx, text in chunks
    ]
    with session_scope() as s:
        replace_chunks(s, doc_id, rows)
    return len(rows)


# --- Вложения ---------------------------------------------------------------

def _read_legacy_attachments(bundle_dir: Path) -> list[dict]:
    attachments: list[dict] = []
    for md in sorted(bundle_dir.glob("*.md")):
        meta, _ = parse_okf_file(md)
        if meta.get("attachments"):
            attachments = list(meta["attachments"])
            break
    att_dir = bundle_dir / "attachments"
    if not attachments and att_dir.is_dir():
        for f in sorted(att_dir.iterdir()):
            if f.is_file():
                attachments.append(
                    {"name": f.name, "kind": "other", "caption": "", "saved_path": f.name}
                )
    return attachments


def _normalize_attachment(att: dict, bundle_dir: Path) -> tuple[Path, dict] | None:
    """Возвращает (src_path, row) для вложения или None. Файл НЕ копируется здесь.

    Актуальное имя файла на диске — basename saved_path (парсер переименовывает
    вложения: oleObject1.bin -> embedded-0.bin); name — оригинальное имя объекта
    для отображения.
    """
    old = (att or {}).get("saved_path")
    name = (att or {}).get("name") or ""
    if not old and not name:
        return None
    file_name = Path(old).name if old else Path(name).name
    display_name = Path(name).name if name else file_name
    new_saved_path = f"attachments/{file_name}"
    src = bundle_dir / "attachments" / file_name
    row = {
        "name": display_name,
        "kind": (att or {}).get("kind", "other"),
        "caption": (att or {}).get("caption", ""),
        "saved_path": new_saved_path,
        "is_processable": False,
        "extraction_status": "saved",
    }
    return src, row


def backfill_attachments(
    doc_id: str, settings, force: bool = False, dry_run: bool = False
) -> int:
    """Переносит вложения в okf_attachments (+ бинарники в uploads). 0 = скип."""
    _, att_count = _row_counts(doc_id)
    if att_count and not force:
        return 0
    bundle_dir = settings.okf_dir / doc_id
    if not bundle_dir.is_dir():
        return 0
    raw = [
        p
        for p in (_normalize_attachment(a, bundle_dir) for a in _read_legacy_attachments(bundle_dir))
        if p
    ]
    missing = [p[1]["saved_path"] for p in raw if not p[0].is_file()]
    if missing:
        logger.warning(
            "[%s] бинарники вложений отсутствуют в бандле, пропущены: %s",
            doc_id, ", ".join(missing),
        )
    pairs = [p for p in raw if p[0].is_file()]
    if not pairs:
        return 0
    if dry_run:
        return len(pairs)
    # Бинарники копируем в uploads (copy2, не move) — бандлы остаются read-only архивом.
    for src, row in pairs:
        dst = settings.uploads_dir / doc_id / row["saved_path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.is_file():
            shutil.copy2(src, dst)
    rows = [row for _, row in pairs]
    with session_scope() as s:
        replace_attachments(s, doc_id, rows, storage_root=settings.uploads_dir / doc_id)
    return len(rows)


# --- Provеnance -------------------------------------------------------------

def _parse_created_at(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    try:
        d = date.fromisoformat(s)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            return None


def backfill_generated_at(doc_id: str, settings, force: bool = False, dry_run: bool = False) -> int:
    """Проставляет okf_concepts.generated_at из frontmatter created_at (где NULL)."""
    bundle_dir = settings.okf_dir / doc_id
    if not bundle_dir.is_dir():
        return 0
    created_map: dict[str, datetime] = {}
    for md in sorted(bundle_dir.glob("*.md")):
        meta, _ = parse_okf_file(md)
        dt = _parse_created_at(meta.get("created_at"))
        if dt is not None:
            created_map[md.stem] = dt
    if not created_map:
        return 0
    with session_scope() as s:
        rows = s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).all()
        updated = 0
        for row in rows:
            # generated_at — провенанс: заполняем ТОЛЬКО NULL. --force его не
            # перезаписывает, чтобы не затирать точный timestamp, поставленный
            # пайплайном Фазы 1 (frontmatter хранит лишь дату).
            if row.generated_at is not None:
                continue
            dt = created_map.get(row.slug)
            if dt is not None:
                updated += 1
                if not dry_run:
                    row.generated_at = dt
    return updated


# --- Оркестрация ------------------------------------------------------------

def process_doc(doc_id: str, filename: str, settings, force: bool = False, dry_run: bool = False) -> dict:
    result = {"chunks": 0, "attachments": 0, "generated_at": 0, "skipped": [], "error": None}
    try:
        chunk_count, att_count = _row_counts(doc_id)
        if chunk_count == 0 or force:
            result["chunks"] = backfill_chunks(doc_id, filename, settings, force=force, dry_run=dry_run)
        else:
            result["skipped"].append("chunks")
        if att_count == 0 or force:
            result["attachments"] = backfill_attachments(doc_id, settings, force=force, dry_run=dry_run)
        else:
            result["skipped"].append("attachments")
        result["generated_at"] = backfill_generated_at(doc_id, settings, dry_run=dry_run)
    except Exception as exc:
        logger.exception("[%s] backfill не удался", doc_id)
        result["error"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill корпуса в canonical-таблицы БД (Этап 2b).")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать один документ")
    parser.add_argument("--force", action="store_true", help="Перезаписать уже заполненные таблицы")
    parser.add_argument("--dry-run", action="store_true", help="Только отчёт, без записи")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    docs = iter_done_docs(args.doc_id)
    if args.doc_id and not docs:
        print(f"Документ {args.doc_id} не найден (или не done/в корзине)")
        sys.exit(1)

    totals = {"chunks": 0, "attachments": 0, "generated_at": 0, "skipped": 0, "errors": 0}
    for doc_id, filename in docs:
        r = process_doc(doc_id, filename, settings, force=args.force, dry_run=args.dry_run)
        if r["error"]:
            totals["errors"] += 1
            logger.error("%s: %s", doc_id, r["error"])
            continue
        if r["skipped"]:
            totals["skipped"] += 1
        totals["chunks"] += r["chunks"]
        totals["attachments"] += r["attachments"]
        totals["generated_at"] += r["generated_at"]
        logger.info(
            "%s: чанки=%d, вложения=%d, generated_at=%d%s",
            doc_id, r["chunks"], r["attachments"], r["generated_at"],
            f" (скип: {','.join(r['skipped'])})" if r["skipped"] else "",
        )

    print(
        f"Итог: документов={len(docs)}, чанки={totals['chunks']}, вложения={totals['attachments']}, "
        f"generated_at={totals['generated_at']}, скип={totals['skipped']}, ошибок={totals['errors']}"
    )


if __name__ == "__main__":
    main()
