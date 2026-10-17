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
from app.db.models import Document, DocumentChunk, DocumentSource, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.json_atomic import write_json_atomic
from app.services.okf_generator import OKFGenerator, _source_manifest
from app.services.generation_store import lock_generation_read
from app import error_codes as codes
from app.services.errors import NotFoundError
from docparser import portable_name


def export_okf_bundle(doc_id: str, dest_dir: Path) -> list[str]:
    """Генерирует структуру OKF-бандла в dest_dir из БД.

    Возвращает список относительных путей созданных файлов (для zip-упаковки).
    Бросает ValueError при отсутствии документа.
    """
    settings = get_settings()
    generator = OKFGenerator()
    with session_scope() as s:
        active_generations = lock_generation_read(s, [doc_id])
        doc = s.get(Document, doc_id)
        if doc is None:
            raise NotFoundError("Документ не найден", code=codes.DOCUMENT_NOT_FOUND)
        from app.services.chunk_projection_maintenance import generation_projection_version, verified_chunk_parts
        from app.db.models import DocumentChunkProjection
        generation_id = active_generations.get(doc_id)
        projection_version = generation_projection_version(s, doc_id, generation_id)
        filename = doc.filename or ""
        global_tags = [item.tag_rel.canonical_text for item in doc.tags_rel]
        concept_rows = (
            s.query(OkfConcept)
            .filter(OkfConcept.doc_id == doc_id)
            .order_by(OkfConcept.slug)
            .all()
        )
        chunk_rows = (
            s.query(DocumentChunk)
            .filter(DocumentChunk.doc_id == doc_id)
            .order_by(func.coalesce(DocumentChunk.display_order, DocumentChunk.chunk_index), DocumentChunk.chunk_index)
            .all()
        )
        att_rows = s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).all()
        source_rows = (
            s.query(DocumentSource)
            .filter(DocumentSource.doc_id == doc_id)
            .order_by(DocumentSource.source_id)
            .all()
        )
        concept_counts = dict(
            s.query(OkfConcept.chunk_index, func.count())
            .filter(OkfConcept.doc_id == doc_id, OkfConcept.chunk_index.isnot(None))
            .group_by(OkfConcept.chunk_index)
            .all()
        )

        projection_metadata = []
        if projection_version is not None:
            for row in chunk_rows:
                if row.table_refs:
                    projection_metadata.append({'index':row.chunk_index,'route':'table'})
                    continue
                # Validate one canonical source and bounded stored parts at a
                # time; portable reconstruction uses full title/raw text/version.
                for _part in verified_chunk_parts(s,doc_id,generation_id,row,64):
                    pass
                parent=s.get(DocumentChunkProjection,(doc_id,generation_id,row.chunk_index))
                projection_metadata.append({'index':row.chunk_index,'route':'generic',
                    'source_hash':parent.source_hash,'title_hash':parent.title_hash,
                    'projection_hash':parent.projection_hash,'fragment_count':parent.fragment_count})
        dest_dir.mkdir(parents=True, exist_ok=True)
        files: list[str] = []
        from app.services.processing_scope import scope_for_generation
        snapshot=scope_for_generation(s,doc_id,active_generations.get(doc_id))
        if snapshot is not None:
            write_json_atomic(dest_dir/'processing-scope.json',snapshot)
            files.append('processing-scope.json')
        if active_generations.get(doc_id):
            from app.services.table_store import export_generation_tables
            manifest = export_generation_tables(s, doc_id, active_generations[doc_id], dest_dir)
            if manifest["tables"]:
                files.extend("tables/" + entry["rows_file"] for entry in manifest["tables"])
                files.append("tables/manifest.json")

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
                        "source_id": a.source_id,
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
                    "source_id": c.source_id,
                    "section_title": c.section_title or "",
                    "table_refs": c.table_refs,
                }
            )
            files.append(f"chunks/{f.name}")
        write_json_atomic(chunks_dir / "manifest.json", chunks_manifest)
        files.append("chunks/manifest.json")
        if projection_version is not None:
            write_json_atomic(chunks_dir / 'projection.json', {
                'chunk_projection_version':projection_version, 'chunks':projection_metadata,
            })
            files.append('chunks/projection.json')

    if source_rows:
        serialized_sources = [
            {
                "source_id": row.source_id,
                "parent_source_id": row.parent_source_id,
                "ordinal": row.ordinal,
                "kind": row.kind,
                "display_name": row.display_name,
                "metadata": row.metadata_json,
                "saved_path": row.saved_path,
                "extraction_status": row.extraction_status,
                "artifact_kind": row.artifact_kind,
                "container_source_id": row.container_source_id,
                "container_locator": row.container_locator,
                "content_fingerprint": row.content_fingerprint,
                "parser_version": row.parser_version,
                "warnings": row.warnings,
            }
            for row in source_rows
        ]
        write_json_atomic(dest_dir / "sources.json", _source_manifest(serialized_sources))
        files.append("sources.json")

    # Концепты (.md) + _files.json.
    concepts = [
        Concept(
            id="",
            title=c.title,
            type=c.type,
            tags=list(c.tags or []),
            content=c.content or "",
            relations=list(c.relations or []),
            table_refs=c.table_refs or [],
        )
        for c in concept_rows
    ]
    slugs = [c.slug for c in concept_rows]
    source_ids = [c.source_id for c in concept_rows]
    chunk_of_slug = {c.slug: c.chunk_index for c in concept_rows if c.chunk_index is not None}
    okf_docs, manifest = generator.build_okf_docs(
        doc_id,
        filename,
        concepts,
        attachments=attachments_meta,
        global_tags=global_tags,
        slugs=slugs,
        chunk_of_slug=chunk_of_slug,
        source_ids=source_ids,
        bundle_dir=dest_dir,
    )
    for d in okf_docs:
        Path(d.filepath).write_text(d.markdown, encoding="utf-8")
        files.append(Path(d.filepath).name)
    write_json_atomic(dest_dir / "_files.json", manifest)
    files.append("_files.json")
    return files
