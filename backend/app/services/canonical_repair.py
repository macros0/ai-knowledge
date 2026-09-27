"""Offline repairs prepare a complete generation while the old one stays readable.

Application writers should be stopped for bulk maintenance. Document locks and
a canonical snapshot hash additionally reject stale repair proposals. Transforms
may edit concepts/chunks, but cannot add or replace source artifacts.
"""
from copy import deepcopy
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil

from sqlalchemy import inspect

from app.db.models import (
    Development, Document, DocumentChunk, DocumentGeneration, DocumentGenerationState, DocumentSource, OkfAttachment, OkfConcept,
)
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.generation_artifacts import artifact_manifest, file_digest
from app.services.generation_cleanup import cleanup_document_generations
from app.services.generation_files import GenerationStorageError, _reject_links, prepare_generation_paths
from app.services.generation_publication import publish_prepared_document
from app.services.generation_store import (
    GenerationConflict, abandon_generation, begin_generation, lock_document_write, lock_generation_read, mark_generation_ready,
)
from app.services.errors import VectorStoreError
from app.services.json_atomic import write_json_atomic
from app.services.okf_generator import _source_manifest
from app.services.vector_store import chunk_point_id, concept_point_id

logger = logging.getLogger(__name__)


def _canonical_rows(session, doc_id: str) -> dict:
    rows = {}
    for name, model, order in (
        ("concepts", OkfConcept, OkfConcept.slug),
        ("chunks", DocumentChunk, DocumentChunk.chunk_index),
        ("sources", DocumentSource, DocumentSource.source_id),
        ("attachments", OkfAttachment, OkfAttachment.saved_path),
    ):
        fields = [attr.key for attr in inspect(model).column_attrs if attr.key not in {"id", "doc_id", "created_at"}]
        rows[name] = [
            {"metadata" if key == "metadata_json" else key: deepcopy(getattr(row, key)) for key in fields}
            for row in session.query(model).filter_by(doc_id=doc_id).order_by(order).all()
        ]
    return rows


def canonical_rows_digest(session, doc_id: str) -> str:
    return _digest(_canonical_rows(session, doc_id))


def _digest(rows: dict) -> str:
    data = json.dumps(rows, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def repair_published_document(doc_id, settings, generator, embedder, vector_store, transform, *, dry_run=False) -> dict:
    """Transform a canonical snapshot and publish it with no parser/LLM calls."""
    with session_scope() as session:
        if not lock_document_write(session, doc_id, allow_deleted=False):
            raise GenerationConflict("Document is missing or deleted")
        document = session.get(Document, doc_id)
        state = session.get(DocumentGenerationState, doc_id)
        if document.status != "done" or (state and state.candidate_generation_id):
            raise GenerationConflict("Repair requires a completed document without a pending generation")
        before = _canonical_rows(session, doc_id)
        proposal = deepcopy(before)
        global_tags = [item.tag_rel.canonical_text for item in document.tags_rel]
        proposal["filename"] = document.filename
        proposal["file_hash"] = document.file_hash
        proposal["global_tags"] = global_tags
        transform(proposal)
        if proposal["sources"] != before["sources"] or proposal["attachments"] != before["attachments"]:
            raise ValueError("Canonical repair cannot replace source artifacts")
        changed = any(proposal[name] != before[name] for name in ("chunks", "concepts"))
        if not changed or dry_run:
            return {"changed": changed, "generation_id": None, "dry_run": dry_run}
        generation = begin_generation(session, doc_id)
        generation_id, base_id = generation.id, generation.base_generation_id
        dev = session.get(Development, document.development_id) if document.development_id else None
        metadata = {"global_tags": global_tags, "source_locale": document.source_locale,
                    "dev_tags": [dev.number, dev.name, *([dev.module] if dev.module else [])] if dev else []}
        mail_fingerprint = document.mail_fingerprint
        filename = document.filename
    # The reservation is durable before any files/points are created, so failure
    # or cleanup interruption cannot leave an undiscoverable generation.
    base_hash = _digest(before)
    try:
        with session_scope() as session:
            paths = prepare_generation_paths(session, settings, doc_id, generation_id)
        with session_scope() as session:
            # Shared publication locks keep the candidate alive while allowing
            # concurrent readers of the old version during slow embedding calls.
            lock_generation_read(session, [doc_id])
            state = session.get(DocumentGenerationState, doc_id)
            candidate = session.get(DocumentGeneration, generation_id)
            document = session.get(Document, doc_id)
            if (document is None or document.deleted_at is not None or state is None
                    or state.candidate_generation_id != generation_id or candidate is None
                    or candidate.phase != "preparing"):
                raise GenerationConflict("Repair candidate is no longer writable")
            if canonical_rows_digest(session, doc_id) != base_hash:
                raise GenerationConflict("Canonical document changed while preparing repair")
            _copy_artifacts(settings, doc_id, base_id, generation_id, paths, proposal)
            docs = _write_bundle(generator, doc_id, filename, paths, proposal)
            point_ids = _index_candidate(settings, vector_store, embedder, doc_id, filename,
                                         generation_id, docs, proposal["chunks"], metadata, sources=proposal["sources"])
            vector_store.verify_generation_points(doc_id, generation_id, sorted(point_ids))
            effects = {}
            if proposal["chunks"] != before["chunks"]:
                from app.services.deduplication import prepare_document_signature

                effects["signature"] = prepare_document_signature(
                    "\n\n".join(row["content"] for row in proposal["chunks"]), mail_fingerprint,
                )
            prepared = {
                "doc_id": doc_id, "generation_id": generation_id, "canonical_base_hash": base_hash,
                "concepts": [doc.model_dump(mode="json") for doc in docs],
                "chunks": proposal["chunks"], "sources": proposal["sources"] or None,
                "attachments": proposal["attachments"], "preserve_attachment_provenance": True,
                "document_fields": {"okf_concept_count": len(docs)}, "index_metadata": metadata,
                "parse_effects": effects, "point_ids": sorted(point_ids),
                "artifacts": artifact_manifest(settings, doc_id, generation_id),
            }
            publication = paths.uploads_root / "publication.json"
            # Snapshot provenance includes datetimes; use the same JSON form as
            # Pydantic manifests so restored publication does not need ORM rows.
            serializable = json.loads(json.dumps(prepared, default=lambda value: value.isoformat()))
            write_json_atomic(publication, serializable)
        with session_scope() as session:
            mark_generation_ready(session, doc_id, generation_id, publication_hash=file_digest(publication))
        publish_prepared_document(settings, vector_store, doc_id, generation_id)
    except Exception as exc:
        try:
            with session_scope() as session:
                candidate = session.get(DocumentGeneration, generation_id)
                # A verified ready manifest is retryable after index/readback failure.
                if candidate is not None and (candidate.phase != "ready" or not isinstance(exc, VectorStoreError)):
                    abandon_generation(session, doc_id, generation_id)
        except Exception:
            logger.warning("[%s] Не удалось отменить ремонтную версию %s", doc_id, generation_id, exc_info=True)
        raise
    try:
        cleanup_document_generations(settings, vector_store, doc_id)
    except Exception:
        logger.warning("[%s] Ремонт опубликован; очистка старой версии будет повторена", doc_id, exc_info=True)
    return {"changed": True, "generation_id": generation_id, "dry_run": False}


def _copy_artifacts(settings, doc_id, base_id, generation_id, paths, proposal):
    prefix = f"generations/{base_id}/attachments/" if base_id else "attachments/"
    new_prefix = f"generations/{generation_id}/attachments/"
    copied = {}
    expected_hashes = {row["saved_path"]: row["sha256"] for row in proposal["attachments"]
                       if row.get("saved_path") and row.get("sha256")}
    for row in [*proposal["sources"], *proposal["attachments"]]:
        saved = row.get("saved_path")
        if not saved:
            continue
        if saved not in copied:
            path = PurePosixPath(saved)
            if (PureWindowsPath(saved).drive or "\\" in saved or path.is_absolute()
                    or any(part in {".", ".."} for part in saved.split("/")) or not saved.startswith(prefix)):
                raise GenerationStorageError("Registered artifact is outside the active generation")
            relative = saved[len(prefix):]
            if not relative:
                raise GenerationStorageError("Registered artifact has no filename")
            source = settings.uploads_dir / doc_id / saved
            _reject_links(source)
            if not source.is_file():
                raise GenerationStorageError("Registered artifact is missing")
            digest = file_digest(source)
            if saved in expected_hashes and digest != expected_hashes[saved]:
                raise GenerationStorageError("Registered artifact hash changed")
            target = paths.attachments / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            bundle_target = paths.bundle / "attachments" / relative
            bundle_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, bundle_target)
            if file_digest(target) != digest or file_digest(bundle_target) != digest:
                raise GenerationStorageError("Copied artifact hash differs from the registered original")
            copied[saved] = new_prefix + relative
        row["saved_path"] = copied[saved]


def _write_bundle(generator, doc_id, filename, paths, proposal):
    concepts = proposal["concepts"]
    docs = generator.save_bundle(
        doc_id, filename,
        [Concept(id=row["slug"], title=row["title"], type=row["type"], content=row["content"],
                 tags=row.get("tags") or [], relations=row.get("relations") or [],
                 source_spans=row.get("source_spans") or []) for row in concepts],
        attachments=proposal["attachments"], global_tags=proposal["global_tags"],
        bundle_root=paths.bundle, slugs=[row["slug"] for row in concepts],
        chunk_of_slug={row["slug"]: row["chunk_index"] for row in concepts if row["chunk_index"] is not None},
        source_ids=[row["source_id"] for row in concepts],
    )
    by_slug = {row["slug"]: row for row in concepts}
    if len(docs) != len(concepts):
        raise ValueError("Repair produced invalid or duplicate concepts")
    for doc in docs:
        row = by_slug[Path(doc.filepath).stem]
        for field in ("generated_at", "model_id", "prompt_version", "source_spans"):
            doc.metadata[field] = row.get(field)
    chunks_dir = paths.bundle / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for chunk in proposal["chunks"]:
        text = chunk["content"] or ""
        chunk["content_hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunk["char_count"] = len(text)
        (chunks_dir / f"chunk_{chunk['chunk_index']:02d}.md").write_bytes(text.encode("utf-8"))
        manifest.append({"index": chunk["chunk_index"], "size": len(text.encode("utf-8")),
                         "source_id": chunk.get("source_id"),
                         "concepts_count": sum(row["chunk_index"] == chunk["chunk_index"] for row in concepts)})
    write_json_atomic(chunks_dir / "manifest.json", manifest)
    if proposal["sources"]:
        sources = _source_manifest(proposal["sources"])
        paths_by_source = {row["source_id"]: row.get("saved_path") for row in proposal["sources"]}
        for row in sources["sources"]:
            saved = paths_by_source[row["source_id"]]
            if saved:
                row["saved_path"] = "attachments/" + saved.split("/attachments/", 1)[1]
        write_json_atomic(paths.bundle / "sources.json", sources)
    return docs


def _index_candidate(settings, store, embedder, doc_id, filename, generation_id, docs, chunks, metadata,
                     *, sources=None):
    from app.services.mail_scope import build_record_mail_scopes

    concept_scopes, chunk_scopes = build_record_mail_scopes(
        sources or [], [doc.metadata for doc in docs], chunks,
    )
    store.ensure_collection()
    expected = set()
    if docs:
        vectors = embedder.embed_texts([
            f"{doc.metadata['title']}\n{doc.content[:settings.okf_max_concept_chars]}" for doc in docs
        ])
        store.index_concepts(doc_id, docs, vectors, dev_tags=metadata["dev_tags"],
                             source_locale=metadata["source_locale"], generation_id=generation_id,
                             mail_scopes=concept_scopes)
        expected.update(concept_point_id(doc_id, Path(doc.filepath).stem, generation_id=generation_id) for doc in docs)
    if chunks and settings.search_index_chunks_enabled:
        texts = [row["content"] for row in chunks]
        titles = [row["section_title"] or "" for row in chunks]
        vectors = embedder.embed_texts([
            f"{title}\n{text[:settings.okf_max_chunk_index_chars]}" if title else text[:settings.okf_max_chunk_index_chars]
            for title, text in zip(titles, texts)
        ])
        store.index_chunks(doc_id, filename, texts, metadata["global_tags"], vectors,
                           section_titles=titles, dev_tags=metadata["dev_tags"], source_locale=metadata["source_locale"],
                           source_ids=[row["source_id"] for row in chunks],
                           chunk_indices=[row["chunk_index"] for row in chunks], generation_id=generation_id,
                           mail_scopes=chunk_scopes)
        expected.update(chunk_point_id(doc_id, row["chunk_index"], generation_id=generation_id) for row in chunks)
    return expected
