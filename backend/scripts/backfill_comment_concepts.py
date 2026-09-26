# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Бэкфилл концептов из комментариев рецензентов для уже загруженных документов.

До включения программной экстракции (okf_comment_concepts_enabled) комментарии
попадали в концепты нестабильно: правило промпта требовало «встраивать в
соответствующий концепт», но по факту из 19 комментариев целевого документа
концептами стали только 4, а решения (например, «наибольший табельный — самый
свежий») терялись. Скрипт детерминированно создаёт концепты из тредов
«вопрос → ответы» для существующих документов БЕЗ вызова LLM.

Для завершённого DOCX перечитывает комментарии в приватном каталоге,
сопоставляет уникальные якоря только внутри своего source_id в SQL-чанках и
заменяет концепты комментариев через новую generation. Остальные концепты,
source_spans и provenance сохраняются из БД. Bundle, обе ветки индекса и
canonical rows публикуются общим repair-сервисом; сбой не меняет старую версию.
Глобальный пользовательский тег review не превращает все концепты в комментарии.
Повторный запуск без содержательных изменений не создаёт новую generation.
Bulk maintenance выполняется при остановленных application writers.

Запуск (из backend/, при доступной БД и Qdrant):
    python scripts/backfill_comment_concepts.py            # все документы
    python scripts/backfill_comment_concepts.py --doc-id <id>
    python scripts/backfill_comment_concepts.py --dry-run  # только отчёт
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docparser import ParseContext, blocks_to_markdown, parse_document
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document
from app.db.session import session_scope
from app.services.comment_concepts import extract_comment_concepts
from app.services.okf_generator import ATTACHMENT_TAG, OKFGenerator, _slugify
from app.services.canonical_repair import repair_published_document
from app.services.generation_artifacts import file_digest
from app.services.parser_supervisor import parse_document_supervised
from app.services.source_evidence import resolve_source_spans
from app.models.schemas import Concept

logger = logging.getLogger("backfill_comment_concepts")

# Сколько символов вопроса использовать как якорь для поиска chunk_index.
_ANCHOR_CHARS = 80


def _iter_docs(doc_id: str | None) -> list[tuple[str, str, int | None]]:
    """(doc_id, filename, development_id) для done-документов вне корзины."""
    with session_scope() as s:
        q = select(Document.id, Document.filename, Document.development_id).where(
            Document.deleted_at.is_(None),
            Document.status == "done",
        )
        if doc_id:
            q = q.where(Document.id == doc_id)
        rows = s.execute(q).all()
    return [(r.id, r.filename, r.development_id) for r in rows]


def _question_of(concept: Concept) -> str:
    """Текст вопроса из content концепта-треда (первая строка после метки;
    двоеточие внутри жирного — формат comment_concepts._build_concept)."""
    for line in concept.content.split("\n"):
        line = line.strip()
        if line.startswith("**Комментарий рецензента"):
            _, _, rest = line.partition(":** ")
            return rest
    return ""


def _read_comment_concepts(src, filename, settings):
    digest = file_digest(src)
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="comment-repair-", dir=settings.staging_dir) as temporary:
        context = ParseContext(filename, mail_enabled=settings.mail_import_enabled)
        if settings.parser_supervisor_enabled and parse_document.__module__.startswith("docparser"):
            result = parse_document_supervised(
                src, filename, attachments_dir=Path(temporary),
                timeout_seconds=settings.parser_timeout_seconds,
                max_memory_mb=settings.parser_max_memory_mb,
                max_concurrent=settings.parser_max_concurrent, mail_enabled=settings.mail_import_enabled,
            )
            blocks = result.blocks
        else:
            blocks = parse_document(src, filename, attachments_dir=Path(temporary), context=context)
        concepts = []
        for block in blocks:
            if block.type != "comment":
                continue
            extracted, _ = extract_comment_concepts(blocks_to_markdown([block]))
            concepts.extend(((block.meta or {}).get("source_id") or "root", concept) for concept in extracted)
    if file_digest(src) != digest:
        raise ValueError("Source file changed during comment parsing")
    return concepts, digest


def _replace_comment_snapshot(snapshot, comments, digest, settings, result):
    if not snapshot["chunks"]:
        result["skipped"].append("no_chunks")
        return
    if snapshot.get("file_hash") and snapshot["file_hash"] != digest:
        raise ValueError("Source file changed since document publication; regenerate it")
    global_tags = snapshot["global_tags"]
    existing = {row["slug"]: row for row in snapshot["concepts"]}

    def is_comment(row):
        tags = row.get("tags") or []
        return "review" in tags and ("comment" in tags or "review" not in global_tags)

    kept = [row for row in snapshot["concepts"] if not is_comment(row)]
    result["removed"] = len(snapshot["concepts"]) - len(kept)
    used = {row["slug"] for row in kept}
    for source_id, concept in comments:
        question = _question_of(concept)
        anchor = question.split("\n", 1)[0].strip()[:_ANCHOR_CHARS]
        matches = []
        for chunk in snapshot["chunks"]:
            if (chunk.get("source_id") or "root") != source_id or not anchor:
                continue
            start = chunk["content"].find(anchor)
            if start >= 0:
                if chunk["content"].find(anchor, start + 1) >= 0:
                    raise ValueError("Ambiguous comment anchor in canonical source")
                matches.append(chunk)
        if len(matches) != 1:
            raise ValueError("Comment anchor must occur once in its canonical source; regenerate the document")
        chunk = matches[0]
        base = _slugify(concept.title) or "review"
        slug, suffix = base, 1
        while slug in used:
            slug = f"{base}-{suffix}"
            suffix += 1
        used.add(slug)
        tags = list(concept.tags)
        if source_id != "root" and settings.okf_attachment_tag_enabled:
            tags.append(ATTACHMENT_TAG)
        tags = list(dict.fromkeys([*tags, *global_tags]))
        spans = resolve_source_spans(chunk["content"], concept.content, [question])
        row = {
            "slug": slug, "title": concept.title, "type": concept.type, "content": concept.content,
            "tags": tags, "relations": list(concept.relations), "chunk_index": chunk["chunk_index"],
            "source_id": chunk.get("source_id"), "source_spans": [span.model_dump() for span in spans] or None,
            "model_id": None, "prompt_version": None,
        }
        previous = existing.get(slug)
        row["generated_at"] = (previous.get("generated_at") if previous and all(
            previous.get(key) == value for key, value in row.items()
        ) else datetime.now(timezone.utc))
        kept.append(row)
    snapshot["concepts"] = sorted(kept, key=lambda row: row["slug"])


def process_doc(doc_id: str, filename: str, settings, generator: OKFGenerator,
                embedder, vector_store, development_id: int | None = None, dry_run: bool = False) -> dict:
    result = {"threads": 0, "removed": 0, "skipped": [], "error": None}
    if Path(filename).suffix.lower() != ".docx":
        result["skipped"].append("not_docx")
        return result
    src = settings.uploads_dir / f"{doc_id}.docx"
    if not src.is_file():
        result["skipped"].append("no_source_file")
        return result
    try:
        comments, digest = _read_comment_concepts(src, filename, settings)
        if not comments:
            result["skipped"].append("no_comments")
            return result
        result["threads"] = len(comments)
        outcome = repair_published_document(
            doc_id, settings, generator, embedder, vector_store,
            lambda snapshot: _replace_comment_snapshot(snapshot, comments, digest, settings, result),
            dry_run=dry_run,
        )
        result.update(outcome)
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл концептов из комментариев рецензентов.")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать один документ")
    parser.add_argument("--dry-run", action="store_true", help="Только отчёт, без записи")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    docs = _iter_docs(args.doc_id)
    if args.doc_id and not docs:
        print(f"Документ {args.doc_id} не найден (или не done/в корзине)")
        sys.exit(1)

    generator = embedder = vector_store = None
    if docs and not args.dry_run:
        from app.services.embedder import Embedder
        from app.services.vector_store import VectorStore

        generator = OKFGenerator()
        embedder = Embedder()
        vector_store = VectorStore()

    counters = {"docs": 0, "threads": 0, "removed": 0, "skipped": 0, "errors": 0}
    for doc_id, filename, development_id in docs:
        r = process_doc(doc_id, filename, settings, generator, embedder, vector_store,
                        development_id=development_id, dry_run=args.dry_run)
        if r["error"]:
            counters["errors"] += 1
            logger.error("%s: %s", doc_id, r["error"])
            continue
        if r["skipped"]:
            counters["skipped"] += 1
            logger.info("%s: пропущено (%s)", doc_id, ", ".join(r["skipped"]))
            continue
        counters["docs"] += 1
        counters["threads"] += r["threads"]
        counters["removed"] += r["removed"]
        logger.info(
            "%s: +%d тредов-комментариев, удалено старых концептов-комментариев: %d%s",
            doc_id, r["threads"], r["removed"], " (dry-run)" if args.dry_run else "",
        )

    print(
        f"Итог: документов={counters['docs']}, тредов={counters['threads']}, "
        f"удалено старых={counters['removed']}, пропущено={counters['skipped']}, "
        f"ошибок={counters['errors']}"
    )
    if counters["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
