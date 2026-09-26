"""Publish a verified candidate independently of pipeline checkpoint cleanup."""
import logging
from pathlib import Path

from app.db.models import Development, Document, DocumentGeneration
from app.db.session import session_scope
from app.models.schemas import OkfDocument
from app.services.attachment_store import replace_attachments
from app.services.chunk_store import replace_chunks
from app.services.concept_store import replace_concepts
from app.services.generation_artifacts import load_verified_publication
from app.services.generation_store import publish_generation
from app.services.source_store import replace_sources

logger = logging.getLogger(__name__)


def publish_prepared_document(settings, vector_store, doc_id: str, generation_id: str) -> bool:
    """Commit canonical rows and active pointer together; False if already active.

    File and search-point verification precede canonical writes. The caller owns
    any checkpoint cleanup, which must never turn a committed publication into
    a failed operation.
    """
    with session_scope() as session:
        if not publish_generation(session, doc_id, generation_id):
            return False
        generation = session.get(DocumentGeneration, generation_id)
        prepared = load_verified_publication(settings, doc_id, generation_id, generation.publication_hash)
        if prepared.get("canonical_base_hash"):
            from app.services.canonical_repair import canonical_rows_digest
            from app.services.generation_store import GenerationConflict

            if canonical_rows_digest(session, doc_id) != prepared["canonical_base_hash"]:
                raise GenerationConflict("Canonical document changed before repair publication")
        vector_store.verify_generation_points(doc_id, generation_id, prepared["point_ids"])
        okf_docs = [OkfDocument.model_validate(item) for item in prepared["concepts"]]
        effects = prepared.get("parse_effects") or {}
        previous_duplicate_ids = set()
        if effects.get("signature"):
            from app.services.deduplication import _duplicate_ids, find_duplicates_for_document

            previous_duplicate_ids = _duplicate_ids(find_duplicates_for_document(doc_id))
        document = session.get(Document, doc_id)
        if effects.get("development") and all(
            getattr(document, key) == value for key, value in effects["development_base"].items()
        ):
            for key, value in effects["development"].items():
                setattr(document, key, value)
        fields = dict(prepared["document_fields"])
        if document.source_locale_source == "manual":
            fields.pop("source_locale", None)
            fields.pop("source_locale_source", None)
        global_tags = [item.tag_rel.canonical_text for item in document.tags_rel]
        old_metadata = prepared["index_metadata"]
        removed = set(old_metadata["global_tags"]) - set(global_tags)
        for item in okf_docs:
            item.metadata["tags"] = _merge_tags(
                [tag for tag in item.metadata.get("tags", []) if tag not in removed], global_tags,
            )
        dev = session.get(Development, document.development_id) if document.development_id else None
        dev_tags = [dev.number, dev.name, *([dev.module] if dev.module else [])] if dev else []
        metadata = {
            "global_tags": global_tags, "dev_tags": dev_tags,
            "source_locale": fields.get("source_locale", document.source_locale),
        }
        if metadata != old_metadata:
            vector_store.update_generation_metadata(
                doc_id, generation_id, **metadata,
                concept_tags={Path(item.filepath).stem: item.metadata["tags"] for item in okf_docs},
            )
        if prepared["sources"] is not None:
            replace_sources(session, doc_id, prepared["sources"])
        replace_chunks(session, doc_id, prepared["chunks"])
        replace_concepts(session, doc_id, okf_docs)
        replace_attachments(
            session, doc_id, prepared["attachments"], storage_root=settings.uploads_dir / doc_id,
        )
        if prepared.get("preserve_attachment_provenance"):
            from app.db.models import OkfAttachment
            from app.services.concept_store import _parse_iso

            originals = {row["saved_path"]: row for row in prepared["attachments"] if row.get("saved_path")}
            for row in session.query(OkfAttachment).filter_by(doc_id=doc_id):
                original = originals[row.saved_path]
                for key in ("content_type", "extracted_chars", "error"):
                    setattr(row, key, original.get(key))
                row.processed_at = _parse_iso(original.get("processed_at"))
        if effects.get("signature"):
            from app.services.deduplication import apply_document_signature

            apply_document_signature(session, doc_id, effects["signature"])
        for key, value in fields.items():
            setattr(document, key, value)
    if effects.get("signature"):
        from app.services.deduplication import refresh_duplicate_flags

        try:
            refresh_duplicate_flags(doc_id, previous_duplicate_ids)
        except Exception:
            logger.warning("[%s] Опубликовано; бейджи дублей требуют повторного обновления", doc_id, exc_info=True)
    return True


def _merge_tags(base: list[str], extra: list[str]) -> list[str]:
    seen = set(base)
    merged = list(base)
    for tag in extra:
        tag = tag.strip()
        if tag and tag not in seen:
            seen.add(tag)
            merged.append(tag)
    return merged
