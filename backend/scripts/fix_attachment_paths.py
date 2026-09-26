# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Одноразовая чистка абсолютных локальных путей из маркер-блоков вложений (2026-09-07).

Инцидент: blocks_to_markdown рендерил маркер-блок вложения со значением
meta['saved_path'] «как есть», а парсеры кладут туда АБСОЛЮТНЫЙ путь машины
обработки (C:\\Users\\<user>\\...\\uploads\\<doc_id>\\attachments\\<файл>). Путь
утекал в текст чанков, в концепты (LLM копировал маркер) и в индекс Qdrant.

Каждый документ ремонтируется через новое поколение: SQL snapshot → private
bundle/attachments → concept+chunk embeddings → проверенная SQL-публикация.
До commit старые данные остаются доступны; сбой сохраняет прежнюю версию.
Меняются только абсолютные пути в известных маркерах и file-ссылках.
Проверенные source_spans сдвигаются по точным заменам; неоднозначные частичные
пересечения с заменяемым маркером отбрасываются. hash/char_count пересчитываются.
Повторный запуск без изменений не создаёт новую версию. Для bulk maintenance
остановите application writers; документы с незаконченной генерацией отклоняются.

Пример:
    python scripts/fix_attachment_paths.py            # публикация исправленных версий
    python scripts/fix_attachment_paths.py --dry-run  # только показать изменения

Требует доступные Qdrant и embedding-сервер (или EMBEDDING_PROVIDER=fake).
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_, select

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfConcept
from app.models.schemas import SourceSpan
from app.db.session import session_scope

from app.services.embedder import Embedder
from app.services.vector_store import VectorStore
from app.services.okf_generator import OKFGenerator
from app.services.canonical_repair import repair_published_document
from app.services.source_evidence import chunk_digest, span_from_offsets

# Абсолютный путь: Windows-диск (C:\) или корень (/ или \\ в начале).
_ABS_PATH = re.compile(r"^[A-Za-z]:[\\/]|^[\\/]")
# Маркер-строка вложения: «(файл: <путь>)» (в чанке обрамлена курсивом: *(файл: ...)*).
_MARKER = re.compile(r"\(файл:\s*([^()\r\n]*?)\s*\)")
# Устаревшая ссылка на вложение: «[подпись](file:<абсолютный путь>)» (доканноновая эра).
_FILE_LINK = re.compile(r"\]\(file:([^()\r\n]*?)\)")


def _relativize_abs_path(token: str) -> str | None:
    """attachments/<имя файла> для абсолютного пути, иначе None (не трогаем)."""
    if not _ABS_PATH.match(token):
        return None
    name = token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return f"attachments/{name}"


def _relativize_marker(text: str) -> tuple[str, bool]:
    """Заменяет абсолютный путь в маркер-строках на attachments/<имя файла>.

    Относительные/прочие значения между скобками не трогает. Возвращает
    (новый текст, было ли изменение).
    """

    def _repl(m: re.Match) -> str:
        rel = _relativize_abs_path(m.group(1).strip())
        return f"(файл: {rel})" if rel else m.group(0)

    source = text or ""
    new = _MARKER.sub(_repl, source)
    return new, new != source


def _relativize_file_links(text: str) -> tuple[str, bool]:
    """Заменяет цель ссылок [..](file:<абсолютный путь>) на attachments/<имя файла>."""

    def _repl(m: re.Match) -> str:
        rel = _relativize_abs_path(m.group(1).strip())
        return f"]({rel})" if rel else m.group(0)

    source = text or ""
    new = _FILE_LINK.sub(_repl, source)
    return new, new != source


def _rewrite_with_spans(text: str, stored_spans: list | None = None) -> tuple[str, list[dict]]:
    ranges = []
    for raw in stored_spans or []:
        try:
            span = SourceSpan.model_validate(raw, strict=True)
        except ValueError:
            continue
        if span.chunk_hash == chunk_digest(text) and 0 <= span.start < span.end <= len(text):
            ranges.append((span.start, span.end))
    for pattern, render in (
        (_MARKER, lambda value: f"(файл: {value})"),
        (_FILE_LINK, lambda value: f"]({value})"),
    ):
        edits = []

        def replace(match):
            relative = _relativize_abs_path(match.group(1).strip())
            if not relative:
                return match.group(0)
            replacement = render(relative)
            edits.append((match.start(), match.end(), len(replacement) - len(match.group(0))))
            return replacement

        text = pattern.sub(replace, text)
        shifted = []
        for start, end in ranges:
            if any(left < start < right or left < end < right for left, right, _delta in edits):
                continue
            shifted.append((start + sum(delta for _left, right, delta in edits if right <= start),
                            end + sum(delta for _left, right, delta in edits if right <= end)))
        ranges = shifted
    return text, [span.model_dump() for start, end in ranges if (span := span_from_offsets(text, start, end))]


def _repair_snapshot(snapshot: dict, changed_chunks: list[int], changed_concepts: list[str]) -> None:
    old_chunks = {row["chunk_index"]: dict(row) for row in snapshot["chunks"]}
    for row in snapshot["chunks"]:
        new, _ = _rewrite_with_spans(row["content"] or "")
        if new != row["content"]:
            changed_chunks.append(row["chunk_index"])
            row["content"] = new
    for row in snapshot["concepts"]:
        new, _ = _rewrite_with_spans(row["content"] or "")
        changed = new != row["content"]
        row["content"] = new
        if row["chunk_index"] in changed_chunks and row.get("source_spans"):
            chunk = old_chunks[row["chunk_index"]]
            if (row.get("source_id") or "root") == (chunk.get("source_id") or "root"):
                _, spans = _rewrite_with_spans(chunk["content"], row["source_spans"])
            else:
                spans = []
            row["source_spans"] = spans or None
            changed = True
        if changed:
            changed_concepts.append(row["slug"])


def fix_rows(dry_run: bool, *, doc_id=None, settings=None, generator=None, embedder=None, vector_store=None) -> dict:
    """Publish path repairs per document; failed documents remain unchanged."""
    settings = settings or get_settings()
    with session_scope() as session:
        candidates = []
        for model in (DocumentChunk, OkfConcept):
            candidates.append(select(model.doc_id).where(
                model.doc_id == Document.id,
                or_(model.content.like("%файл:%"), model.content.like("%](file:%")),
            ).exists())
        query = select(Document.id).where(Document.deleted_at.is_(None), or_(*candidates)).order_by(Document.id)
        if doc_id:
            query = query.where(Document.id == doc_id)
        doc_ids = list(session.scalars(query))
    if not dry_run and doc_ids:
        generator = generator or OKFGenerator()
        embedder = embedder or Embedder()
        vector_store = vector_store or VectorStore()
    result = {"chunks": [], "concepts": [], "errors": [], "generations": {}}
    for current_id in doc_ids:
        chunks, concepts = [], []
        try:
            outcome = repair_published_document(
                current_id, settings, generator, embedder, vector_store,
                lambda snapshot: _repair_snapshot(snapshot, chunks, concepts), dry_run=dry_run,
            )
        except Exception as exc:
            result["errors"].append({"doc_id": current_id, "detail": str(exc)})
            continue
        result["chunks"].extend((current_id, index) for index in chunks)
        result["concepts"].extend((current_id, slug) for slug in concepts)
        if outcome["generation_id"]:
            result["generations"][current_id] = outcome["generation_id"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Ремонт путей вложений через новую версию документа.")
    parser.add_argument("--dry-run", action="store_true", help="Показать изменения без записи")
    parser.add_argument("--doc-id", help="Обработать только выбранный документ")
    args = parser.parse_args()
    stats = fix_rows(dry_run=args.dry_run, doc_id=args.doc_id)
    print(f"Изменений: чанков {len(stats['chunks'])}, концептов {len(stats['concepts'])}")
    for doc_id, index in stats["chunks"]:
        print(f"  chunk [{doc_id}] #{index}")
    for doc_id, slug in stats["concepts"]:
        print(f"  concept [{doc_id}] {slug}")
    for error in stats["errors"]:
        print(f"ОШИБКА [{error['doc_id']}]: {error['detail']}")
    if args.dry_run:
        print("dry-run: БД, файлы и Qdrant не изменены.")
    else:
        print(f"Опубликовано ремонтных версий: {len(stats['generations'])}")
    if stats["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
