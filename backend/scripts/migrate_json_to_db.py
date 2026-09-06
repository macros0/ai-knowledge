"""Одноразовая миграция данных из JSON/FS в реляционную БД (MIGRATION_PLAN.md §8).

Переносит:
  data/documents.json           -> documents + document_tags
  data/tags.json                -> tags (только имена; count считается на чтение)
  data/okf_bundles/{id}/*.md    -> okf_concepts (slug=stem, content=тело без
                                   обрезки сверх того, что уже в .md)
  data/okf_bundles/{id}/attachments/ -> okf_attachments
  data/staging/{id}/manifest.json     -> document_staging (JSONB)

Бинарники (data/uploads/), .md-бандлы и сырые chunk_*.md/json остаются в FS —
переносятся только метаданные (см. MIGRATION_PLAN.md §1, §3).

Запуск (при остановленном сервисе, из каталога backend):
    python scripts/migrate_json_to_db.py
    python scripts/migrate_json_to_db.py --data-dir /path/to/data
    python scripts/migrate_json_to_db.py --reset          # очистить и перечитать

Идемпотентность: по умолчанию пропускает уже существующие документы (по id);
--reset очищает все таблицы приложения и читает данные заново. Перед записью
data/*.json копируются в *.bak.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (
    Document,
    DocumentStaging,
    DocumentTag,
    OkfAttachment,
    OkfConcept,
    Tag,
)
from app.db.session import init_db, session_scope
from app.services.bundle import parse_okf_file


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _backup_json(data_dir: Path) -> None:
    for name in ("documents.json", "tags.json"):
        src = data_dir / name
        if src.is_file():
            shutil.copy2(src, data_dir / f"{name}.bak")
            print(f"  бэкап: {src} -> {src}.bak")


def _reset(session) -> None:
    for model in (OkfConcept, OkfAttachment, DocumentStaging, DocumentTag, Document, Tag):
        session.query(model).delete(synchronize_session=False)
    print("  таблицы приложения очищены")


def _migrate_documents(data_dir: Path) -> int:
    docs = _read_json(data_dir / "documents.json")
    n = 0
    from app.services.tag_registry import TagRegistry, normalize_tags

    tag_reg = TagRegistry()
    all_tags = normalize_tags(
        [
            t
            for doc in docs.values()
            if isinstance(doc, dict)
            for t in (doc.get("tags") or [])
        ]
    )
    name_to_id = dict(zip(all_tags, tag_reg.get_or_create_ids(all_tags)))
    with session_scope() as s:
        existing = {doc_id for (doc_id,) in s.query(Document.id).all()}
        for doc_id, doc in docs.items():
            if not isinstance(doc, dict):
                continue
            if doc_id in existing:
                continue
            tags = normalize_tags(doc.get("tags") or [])
            s.add(
                Document(
                    id=doc_id,
                    filename=doc.get("filename", "unknown"),
                    content_type=doc.get("content_type", ""),
                    size=int(doc.get("size", 0) or 0),
                    status=doc.get("status", "uploaded"),
                    error=doc.get("error"),
                    total_chunks=int(doc.get("total_chunks", 0) or 0),
                    processed_chunks=int(doc.get("processed_chunks", 0) or 0),
                    current_chunk=doc.get("current_chunk"),
                    okf_concept_count=int(doc.get("okf_concept_count", 0) or 0),
                    created_at=_parse_dt(doc.get("created_at")),
                    updated_at=_parse_dt(doc.get("updated_at")),
                    tags_rel=[DocumentTag(tag_id=name_to_id[t]) for t in tags],
                )
            )
            n += 1
    return n


def _migrate_concepts(data_dir: Path) -> int:
    bundles = data_dir / "okf_bundles"
    n = 0
    if not bundles.is_dir():
        return 0
    with session_scope() as s:
        doc_ids = {d for (d,) in s.query(Document.id).all()}
        for bundle_dir in sorted(bundles.iterdir()):
            if not bundle_dir.is_dir() or bundle_dir.name not in doc_ids:
                continue
            for md in sorted(bundle_dir.glob("*.md")):
                meta, content = parse_okf_file(md)
                if not content:
                    continue
                s.add(
                    OkfConcept(
                        doc_id=bundle_dir.name,
                        slug=md.stem,
                        title=meta.get("title", ""),
                        type=meta.get("type", "concept"),
                        tags=list(meta.get("tags", []) or []),
                        content=content,
                        relations=list(meta.get("relations", []) or []),
                        chunk_index=meta.get("chunk_index"),
                    )
                )
                n += 1
    return n


def _migrate_attachments(data_dir: Path) -> int:
    bundles = data_dir / "okf_bundles"
    n = 0
    if not bundles.is_dir():
        return 0
    with session_scope() as s:
        for bundle_dir in sorted(bundles.iterdir()):
            if not bundle_dir.is_dir():
                continue
            attach_dir = bundle_dir / "attachments"
            if not attach_dir.is_dir():
                continue
            for f in sorted(attach_dir.iterdir()):
                if not f.is_file():
                    continue
                s.add(
                    OkfAttachment(
                        doc_id=bundle_dir.name,
                        name=f.name,
                        kind="other",
                        caption="",
                        saved_path=str(f.relative_to(bundle_dir)),
                    )
                )
                n += 1
    return n


def _migrate_staging(data_dir: Path) -> int:
    staging_root = data_dir / "staging"
    n = 0
    if not staging_root.is_dir():
        return 0
    with session_scope() as s:
        for manifest_path in sorted(staging_root.glob("*/manifest.json")):
            manifest = _read_json(manifest_path)
            if not manifest:
                continue
            doc_id = manifest.get("task_id") or manifest_path.parent.name
            s.add(
                DocumentStaging(
                    doc_id=doc_id,
                    total_chunks=int(manifest.get("total_chunks", 0) or 0),
                    processed_chunks=list(manifest.get("processed_chunks", []) or []),
                    used_slugs=list(manifest.get("used_slugs", []) or []),
                    global_tags=list(manifest.get("global_tags", []) or []),
                    chunks_data=dict(manifest.get("chunks_data", {}) or {}),
                    status=manifest.get("status", "in_progress"),
                )
            )
            n += 1
    return n


def _migrate_tags(data_dir: Path) -> int:
    tags = _read_json(data_dir / "tags.json")
    names = {str(k).strip() for k in tags if str(k).strip()}
    with session_scope() as s:
        existing = {n for (n,) in s.query(Tag.canonical_text).all()}
        for name in names - existing:
            s.add(Tag(canonical_text=name))
    return len(names - existing)


def main() -> None:
    parser = argparse.ArgumentParser(description="Миграция JSON/FS -> реляционная БД.")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"), help="Каталог данных (по умолчанию ./data)")
    parser.add_argument("--reset", action="store_true", help="Очистить таблицы приложения и перечитать всё")
    args = parser.parse_args()

    init_db()

    if args.reset:
        with session_scope() as s:
            _reset(s)

    print("Бэкап JSON-файлов...")
    _backup_json(args.data_dir)

    print("Миграция documents.json...")
    print(f"  документов: {_migrate_documents(args.data_dir)}")
    print("Миграция okf_bundles -> okf_concepts...")
    print(f"  концептов: {_migrate_concepts(args.data_dir)}")
    print("Миграция attachments...")
    print(f"  вложений: {_migrate_attachments(args.data_dir)}")
    print("Миграция staging/manifest.json...")
    print(f"  staging-задач: {_migrate_staging(args.data_dir)}")
    print("Миграция tags.json...")
    print(f"  имён тегов: {_migrate_tags(args.data_dir)}")

    with session_scope() as s:
        docs = s.query(Document.id).count()
        concepts = s.query(OkfConcept.id).count()
    print(f"Готово. В БД: документов {docs}, концептов {concepts}.")


if __name__ == "__main__":
    main()
