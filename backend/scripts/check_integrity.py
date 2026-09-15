# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Проверка целостности корпуса между БД, FS и Qdrant (Этап 2b, Фаза 2).

Read-only. Для каждого активного done-документа сверяет:
  - document_chunks: число строк vs documents.total_chunks vs точек Qdrant (chunk);
  - okf_concepts: число строк vs documents.okf_concept_count vs точек Qdrant (concept);
  - okf_attachments: каждая строка ↔ файл в uploads/<id>/attachments/ (обоюдно);
  - (--verify-content) content чанков vs chunk_XX.md бандла (пока бандлы живы).

Qdrant-счётчики — count-запросами с фильтром (без полного scroll). При недоступном
Qdrant его проверки помечаются unavailable, а не ошибкой. Exit code 1 при расхождениях.

Запуск (из backend/, стек поднят):
    python scripts/check_integrity.py
    python scripts/check_integrity.py --doc-id <id>
    python scripts/check_integrity.py --verify-content
    python scripts/check_integrity.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_file_issues(manifest: dict, data_dir: Path) -> list[str]:
    """Validate every original/attachment checksum carried by a backup manifest.

    Paths are always portable, relative paths below the restored DATA_DIR. A
    manifest must never be able to make integrity verification read outside it.
    """
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return ["backup manifest has no files list"]

    root = data_dir.resolve()
    issues: list[str] = []
    for item in entries:
        if not isinstance(item, dict):
            issues.append("backup manifest contains an invalid file entry")
            continue
        relative = item.get("path")
        expected = item.get("sha256")
        if not isinstance(relative, str) or not isinstance(expected, str):
            issues.append("backup manifest contains an invalid file checksum")
            continue
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            issues.append(f"unsafe manifest file path: {relative}")
            continue
        path = (root / candidate).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            issues.append(f"unsafe manifest file path: {relative}")
            continue
        if not path.is_file():
            issues.append(f"missing archived file: {relative}")
        elif _sha256_file(path) != expected:
            issues.append(f"checksum mismatch: {relative}")
    return issues


def strict_runtime_issues(
    results: list[dict], *, qdrant_available: bool | None = None
) -> list[str]:
    """Return strict invariants that do not depend on a backup manifest."""
    if any(result.get("qdrant_unavailable") for result in results) or qdrant_available is False:
        return ["Qdrant is unavailable"]
    return []


def strict_issues(
    results: list[dict], manifest: dict, *, qdrant_available: bool | None = None
) -> list[str]:
    """Return non-negotiable restore invariants derived from a backup manifest."""
    expected = manifest.get("totals")
    if not isinstance(expected, dict):
        return ["backup manifest has no totals object"]

    issues = strict_runtime_issues(results, qdrant_available=qdrant_available)
    if not results and qdrant_available is None:
        issues.append("Qdrant is unavailable")

    actual = {
        "documents": len(results),
        "chunks": sum(int(result.get("db_chunks", 0)) for result in results),
        "concepts": sum(int(result.get("db_concepts", 0)) for result in results),
        "qdrant_points": sum(int(result.get("qdrant_points") or 0) for result in results),
    }
    for name, value in actual.items():
        if name not in expected:
            issues.append(f"backup manifest has no totals.{name}")
        elif value != int(expected[name]):
            issues.append(f"{name}={value} != manifest={expected[name]}")
    return issues


def iter_done_docs(doc_id: str | None = None) -> list[tuple[str, str, int, int]]:
    """(doc_id, filename, total_chunks, okf_concept_count) активных done-доков."""
    with session_scope() as s:
        q = select(
            Document.id,
            Document.filename,
            Document.total_chunks,
            Document.okf_concept_count,
        ).where(Document.deleted_at.is_(None), Document.status == "done")
        if doc_id:
            q = q.where(Document.id == doc_id)
        rows = s.execute(q).all()
    return [(r.id, r.filename, r.total_chunks, r.okf_concept_count) for r in rows]


def _qdrant_count(vector_store, doc_id: str, point_type: str) -> int | None:
    """Число точек Qdrant с фильтром (doc_id, point_type); None — Qdrant недоступен."""
    from qdrant_client.http import models as qm

    flt = qm.Filter(
        must=[
            qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id)),
            qm.FieldCondition(key="point_type", match=qm.MatchValue(value=point_type)),
        ]
    )
    res = vector_store.client.count(
        collection_name=vector_store.collection, count_filter=flt, exact=True
    )
    return int(res.count)


def check_doc(
    doc_id: str,
    total_chunks: int,
    okf_concept_count: int,
    settings,
    vector_store=None,
    verify_content: bool = False,
) -> dict:
    issues: list[str] = []
    qdrant_unavailable = False
    qdrant_points: int | None = None

    with session_scope() as s:
        db_chunks = s.query(DocumentChunk).filter(DocumentChunk.doc_id == doc_id).count()
        db_concepts = s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).count()
        att_rows = s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).all()
        chunk_rows = (
            s.query(DocumentChunk.chunk_index, DocumentChunk.content)
            .filter(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )

    # БД vs метаданные документа
    if total_chunks and db_chunks != total_chunks:
        issues.append(f"document_chunks={db_chunks} != documents.total_chunks={total_chunks}")
    if okf_concept_count and db_concepts != okf_concept_count:
        issues.append(f"okf_concepts={db_concepts} != documents.okf_concept_count={okf_concept_count}")

    # Qdrant
    if vector_store is not None:
        try:
            q_chunks = _qdrant_count(vector_store, doc_id, "chunk")
            qdrant_points = q_chunks
            if db_chunks != q_chunks:
                issues.append(f"document_chunks={db_chunks} != Qdrant chunk-точек={q_chunks}")
        except Exception:
            qdrant_unavailable = True
        try:
            q_concepts = _qdrant_count(vector_store, doc_id, "concept")
            qdrant_points = (qdrant_points or 0) + q_concepts
            if db_concepts != q_concepts:
                issues.append(f"okf_concepts={db_concepts} != Qdrant concept-точек={q_concepts}")
        except Exception:
            qdrant_unavailable = True

    # Вложения: строка ↔ файл (обоюдно)
    storage_root = settings.uploads_dir / doc_id
    att_dir = storage_root / "attachments"
    row_paths: set[str] = set()
    for a in att_rows:
        sp = a.saved_path or ""
        row_paths.add(sp)
        if not sp or not (storage_root / sp).is_file():
            issues.append(f"okf_attachments.saved_path={sp!r}: файл отсутствует")
    if att_dir.is_dir():
        for f in att_dir.iterdir():
            if f.is_file():
                rel = f"attachments/{f.name}"
                if rel not in row_paths:
                    issues.append(f"файл {rel!r} без строки okf_attachments")

    # Контент чанков vs бандл (пока бандлы живы)
    if verify_content:
        chunks_dir = settings.okf_dir / doc_id / "chunks"
        for chunk_index, content in chunk_rows:
            p = chunks_dir / f"chunk_{chunk_index:02d}.md"
            if p.is_file() and p.read_text(encoding="utf-8") != content:
                issues.append(f"chunk_{chunk_index:02d}.md расходится с document_chunks.content")

    return {
        "doc_id": doc_id,
        "issues": issues,
        "ok": not issues,
        "qdrant_unavailable": qdrant_unavailable,
        "db_chunks": db_chunks,
        "db_concepts": db_concepts,
        "qdrant_points": qdrant_points,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка целостности корпуса (Этап 2b).")
    parser.add_argument("--doc-id", type=str, default=None, help="Проверить один документ")
    parser.add_argument("--verify-content", action="store_true", help="Сверить content чанков с бандлом")
    parser.add_argument("--json", action="store_true", help="Машиночитаемый вывод")
    parser.add_argument("--strict", action="store_true", help="Требовать доступный Qdrant и сверку manifest")
    parser.add_argument("--expected-manifest", type=Path, help="manifest.json ожидаемого backup")
    args = parser.parse_args()
    if args.expected_manifest and not args.strict:
        parser.error("--expected-manifest requires --strict")

    settings = get_settings()
    vector_store = None
    try:
        from app.services.vector_store import VectorStore

        vector_store = VectorStore()
    except Exception:
        vector_store = None

    qdrant_available = False
    if vector_store is not None:
        try:
            qdrant_available = bool(vector_store.ping())
        except Exception:
            qdrant_available = False

    docs = iter_done_docs(args.doc_id)
    if args.doc_id and not docs:
        print(f"Документ {args.doc_id} не найден (или не done/в корзине)")
        sys.exit(1)

    results = []
    for doc_id, filename, total_chunks, okf_concept_count in docs:
        r = check_doc(
            doc_id,
            total_chunks,
            okf_concept_count,
            settings,
            vector_store=vector_store,
            verify_content=args.verify_content,
        )
        results.append(r)

    strict_failures: list[str] = []
    if args.strict:
        if args.expected_manifest:
            try:
                manifest = json.loads(args.expected_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                strict_failures.append(f"cannot read backup manifest: {exc}")
            else:
                strict_failures = strict_issues(
                    results, manifest, qdrant_available=qdrant_available
                )
                strict_failures.extend(manifest_file_issues(manifest, settings.data_dir))
        else:
            strict_failures = strict_runtime_issues(
                results, qdrant_available=qdrant_available
            )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        bad = 0
        for r in results:
            if r["issues"]:
                bad += 1
                print(f"[{r['doc_id']}] ПРОБЛЕМЫ:")
                for issue in r["issues"]:
                    print(f"   - {issue}")
            elif r["qdrant_unavailable"]:
                print(f"[{r['doc_id']}] ok (Qdrant недоступен — сверка точек пропущена)")
        total = len(results)
        print(f"\nПроверено: {total} документов, с проблемами: {bad}")
        if vector_store is None:
            print("Внимание: Qdrant недоступен, сверка точек пропущена для всех документов.")

    for issue in strict_failures:
        print(f"STRICT: {issue}")

    if any(not r["ok"] for r in results) or strict_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
