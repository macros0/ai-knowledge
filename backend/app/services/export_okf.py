"""Экспорт OKF-бандла из PostgreSQL (Этап 2b, Фаза 5).

Бандл (YAML/Markdown + `_files.json` + `chunks/` + `attachments/`) — производная
проекция БД: генерируется по явному запросу (эндпоинт export-okf / CLI), а не
хранится как рабочее состояние. Единственный источник истины — PostgreSQL.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import func

from app.config import get_settings
from app.db.models import DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.json_atomic import write_json_atomic
from app.services.okf_generator import OKFGenerator
from app.services.registry import get_registry
from app import error_codes as codes
from app.services.errors import ConflictError, DomainError, NotFoundError
from docparser import portable_name


def export_okf_bundle(doc_id: str, dest_dir: Path) -> list[str]:
    """Генерирует структуру OKF-бандла в dest_dir из БД.

    Возвращает список относительных путей созданных файлов (для zip-упаковки).
    Бросает ValueError при отсутствии документа.
    """
    settings = get_settings()
    generator = OKFGenerator()
    doc = get_registry().get(doc_id)
    if doc is None:
        raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
    filename = doc.get("filename", "")
    global_tags = list(doc.get("tags") or [])

    with session_scope() as s:
        concept_rows = (
            s.query(OkfConcept)
            .filter(OkfConcept.doc_id == doc_id)
            .order_by(OkfConcept.slug)
            .all()
        )
        chunk_rows = (
            s.query(DocumentChunk)
            .filter(DocumentChunk.doc_id == doc_id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        att_rows = s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).all()
        concept_counts = dict(
            s.query(OkfConcept.chunk_index, func.count())
            .filter(OkfConcept.doc_id == doc_id, OkfConcept.chunk_index.isnot(None))
            .group_by(OkfConcept.chunk_index)
            .all()
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []

    # Вложения: бинарники копируются из uploads/<doc_id>/attachments/.
    attachments_meta: list[dict] = []
    if att_rows:
        att_dir = dest_dir / "attachments"
        att_dir.mkdir(parents=True, exist_ok=True)
        for a in att_rows:
            saved = a.saved_path or ""
            src = settings.uploads_dir / doc_id / saved
            if src.is_file():
                dst = att_dir / portable_name(saved)
                shutil.copy2(src, dst)
                files.append(f"attachments/{dst.name}")
            attachments_meta.append(
                {
                    "name": a.name,
                    "kind": a.kind,
                    "caption": a.caption,
                    # portable_name, а не Path().name: в легаси-строках БД
                    # saved_path может быть windows-путём, и на Linux
                    # Path().name вернул бы его целиком (инцидент 2026-09-07).
                    "saved_path": portable_name(saved) if saved else None,
                }
            )

    # Чанки.
    if chunk_rows:
        chunks_dir = dest_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        chunks_manifest: list[dict] = []
        for c in chunk_rows:
            f = chunks_dir / f"chunk_{c.chunk_index:02d}.md"
            f.write_text(c.content or "", encoding="utf-8")
            chunks_manifest.append(
                {
                    "index": c.chunk_index,
                    "size": len((c.content or "").encode("utf-8")),
                    "concepts_count": concept_counts.get(c.chunk_index, 0),
                }
            )
            files.append(f"chunks/{f.name}")
        write_json_atomic(chunks_dir / "manifest.json", chunks_manifest)
        files.append("chunks/manifest.json")

    # Концепты (.md) + _files.json.
    concepts = [
        Concept(
            id="",
            title=c.title,
            type=c.type,
            tags=list(c.tags or []),
            content=c.content or "",
            relations=list(c.relations or []),
        )
        for c in concept_rows
    ]
    slugs = [c.slug for c in concept_rows]
    chunk_of_slug = {c.slug: c.chunk_index for c in concept_rows if c.chunk_index is not None}
    okf_docs, manifest = generator.build_okf_docs(
        doc_id,
        filename,
        concepts,
        attachments=attachments_meta,
        global_tags=global_tags,
        slugs=slugs,
        chunk_of_slug=chunk_of_slug,
        bundle_dir=dest_dir,
    )
    for d in okf_docs:
        Path(d.filepath).write_text(d.markdown, encoding="utf-8")
        files.append(Path(d.filepath).name)
    write_json_atomic(dest_dir / "_files.json", manifest)
    files.append("_files.json")
    return files
