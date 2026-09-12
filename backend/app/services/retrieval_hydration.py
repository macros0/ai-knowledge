"""Batch hydration of retrieval hits from canonical relational storage."""
from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

from sqlalchemy import func, select, tuple_

from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope

logger = logging.getLogger(__name__)


def _dedupe_pairs(pairs: list[tuple]) -> list[tuple]:
    """Remove duplicate natural keys while preserving first-seen order."""
    seen: set[tuple] = set()
    result: list[tuple] = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def _concept_slug(hit) -> str:
    slug = hit.payload.get("slug")
    if slug:
        return str(slug)
    return Path(hit.payload.get("filepath", "")).stem


def _chunk_pairs_needed_for_merge(
    hits: list,
    concept_contents: dict[tuple[str, str], str],
    chunk_pairs: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    """Return chunk keys whose canonical text can affect merge output.

    A group with multiple non-review concepts uses the primary concept digest
    as its content. Once that digest is known and the primary title is present,
    the raw chunk text cannot affect ``merge_and_format``. Groups with one
    concept, an empty digest, or an empty primary title retain the chunk
    hydration fallback.
    """
    groups: dict[tuple[str, int], list] = {}
    for hit in hits:
        payload = hit.payload
        if payload.get("point_type") == "chunk":
            doc_id = payload.get("doc_id", "")
            chunk_index = payload.get("chunk_index")
            if doc_id and chunk_index is not None:
                groups.setdefault((doc_id, int(chunk_index)), []).append(hit)
        elif payload.get("point_type") == "concept":
            doc_id = payload.get("doc_id", "")
            chunk_index = payload.get("chunk_index")
            if doc_id and chunk_index is not None:
                groups.setdefault((doc_id, int(chunk_index)), []).append(hit)

    skip: set[tuple[str, int]] = set()
    for key, group_hits in groups.items():
        if not any(hit.payload.get("point_type") == "chunk" for hit in group_hits):
            continue
        main_concepts = [
            hit
            for hit in group_hits
            if hit.payload.get("point_type") == "concept"
            and "review" not in (hit.payload.get("tags") or [])
        ]
        if len(main_concepts) < 2:
            continue
        primary = main_concepts[0]
        primary_key = (key[0], _concept_slug(primary))
        if primary.payload.get("title") and concept_contents.get(primary_key):
            skip.add(key)

    return [pair for pair in chunk_pairs if pair not in skip]


def _enrich_retrieval_hits_in_session(
    hits: list,
    session,
    *,
    max_concept_chars: int,
    max_chunk_chars: int,
) -> list:
    """Hydrate concept and chunk hits using an already-open DB session."""
    concept_pairs: list[tuple[str, str]] = []
    chunk_pairs: list[tuple[str, int]] = []
    for hit in hits:
        payload = hit.payload
        point_type = payload.get("point_type")
        doc_id = payload.get("doc_id", "")
        if point_type == "concept":
            slug = _concept_slug(hit)
            if doc_id and slug:
                concept_pairs.append((doc_id, slug))
        elif point_type == "chunk":
            chunk_index = payload.get("chunk_index")
            if doc_id and chunk_index is not None:
                chunk_pairs.append((doc_id, int(chunk_index)))

    concept_pairs = _dedupe_pairs(concept_pairs)
    chunk_pairs = _dedupe_pairs(chunk_pairs)

    if not concept_pairs and not chunk_pairs:
        return hits

    concept_contents: dict[tuple[str, str], str] = {}
    chunk_contents: dict[tuple[str, int], dict] = {}
    if concept_pairs:
        rows = session.execute(
            select(
                OkfConcept.doc_id,
                OkfConcept.slug,
                func.substr(OkfConcept.content, 1, max_concept_chars),
            ).where(tuple_(OkfConcept.doc_id, OkfConcept.slug).in_(concept_pairs))
        ).all()
        concept_contents = {
            (doc_id, slug): content for doc_id, slug, content in rows
        }
    needed_chunk_pairs = _chunk_pairs_needed_for_merge(
        hits, concept_contents, chunk_pairs
    )
    skipped_chunk_pairs = set(chunk_pairs) - set(needed_chunk_pairs)
    if needed_chunk_pairs:
        rows = session.execute(
            select(
                DocumentChunk.doc_id,
                DocumentChunk.chunk_index,
                func.substr(DocumentChunk.content, 1, max_chunk_chars),
                DocumentChunk.section_title,
            ).where(
                tuple_(DocumentChunk.doc_id, DocumentChunk.chunk_index).in_(
                    needed_chunk_pairs
                )
            )
        ).all()
        chunk_contents = {
            (doc_id, int(chunk_index)): {
                "content": content,
                "section_title": section_title or "",
            }
            for doc_id, chunk_index, content, section_title in rows
        }

    for hit in hits:
        payload = hit.payload
        point_type = payload.get("point_type")
        doc_id = payload.get("doc_id", "")
        if point_type == "concept":
            slug = _concept_slug(hit)
            key = (doc_id, slug)
            if key in concept_contents:
                payload["content"] = concept_contents[key]
            if not payload.get("filepath"):
                payload["filepath"] = f"{doc_id}/{slug}.md"
        elif point_type == "chunk":
            chunk_index = payload.get("chunk_index")
            key = (doc_id, int(chunk_index)) if chunk_index is not None else None
            if key in chunk_contents:
                payload["content"] = chunk_contents[key]["content"]
                payload["section_title"] = chunk_contents[key]["section_title"]
            elif key in skipped_chunk_pairs:
                continue
            else:
                logger.warning(
                    "Чанк (%s, %s) не найден в document_chunks — рассинхрон БД и Qdrant",
                    doc_id,
                    chunk_index,
                )
    return hits


def enrich_retrieval_hits(hits: list) -> list:
    """Hydrate concept and chunk hits in one read-only DB session.

    Qdrant remains the source of ranking and payload metadata; PostgreSQL is
    still the canonical source of full text. The function mutates the supplied
    hit payloads exactly like the former two hydrators, while avoiding a second
    session/connection setup when a result contains both point types.
    """
    from app.config import get_settings

    settings = get_settings()
    with session_scope() as session:
        _enrich_retrieval_hits_in_session(
            hits,
            session,
            max_concept_chars=max(1, int(settings.chat_concept_max_chars)),
            max_chunk_chars=max(1, int(settings.chat_chunk_max_chars)),
        )
    return hits


def load_visible_retrieval_hits(
    hits: list,
    *,
    timings: dict[str, float] | None = None,
    max_concept_chars: int | None = None,
    max_chunk_chars: int | None = None,
) -> tuple[list, dict]:
    """Filter visibility and hydrate retrieval hits inside one DB session.

    The search and chat paths previously opened one session for the visibility
    lookup and a second one for canonical text hydration. Keeping both reads in
    one read-only request transaction removes that repeated session/transaction
    setup while preserving the same deleted/orphan filtering and hydration
    semantics.
    """
    if not hits:
        return [], {}

    from app.config import get_settings

    settings = get_settings()
    concept_chars = max(
        1,
        int(
            settings.chat_concept_max_chars
            if max_concept_chars is None
            else max_concept_chars
        ),
    )
    chunk_chars = max(
        1,
        int(
            settings.chat_chunk_max_chars
            if max_chunk_chars is None
            else max_chunk_chars
        ),
    )
    doc_ids = {str(hit.payload.get("doc_id", "")) for hit in hits}
    doc_ids.discard("")
    with session_scope() as session:
        started = perf_counter()
        rows = session.execute(
            select(Document.id, Document.filename, Document.deleted_at).where(
                Document.id.in_(doc_ids)
            )
        ).all()
        values = {
            doc_id: {
                "id": doc_id,
                "filename": filename,
                "deleted_at": deleted_at,
            }
            for doc_id, filename, deleted_at in rows
        }
        doc_lookup = {doc_id: values.get(doc_id) for doc_id in doc_ids}
        visible = [
            hit
            for hit in hits
            if (doc := doc_lookup.get(hit.payload.get("doc_id", ""))) is not None
            and not doc.get("deleted_at")
        ]
        if timings is not None:
            timings["visibility_ms"] = round((perf_counter() - started) * 1000, 3)
        started = perf_counter()
        _enrich_retrieval_hits_in_session(
            visible,
            session,
            max_concept_chars=concept_chars,
            max_chunk_chars=chunk_chars,
        )
        if timings is not None:
            timings["enrichment_ms"] = round((perf_counter() - started) * 1000, 3)
    return visible, doc_lookup
