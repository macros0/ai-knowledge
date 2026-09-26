"""Batch hydration of retrieval hits from canonical relational storage."""
from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

from sqlalchemy import func, select, tuple_

from app.db.models import Document, DocumentChunk, DocumentGenerationState, OkfConcept
from app.db.session import session_scope
from app.services.glossary.matching import group_form_matches
from app.services.glossary.types import MatchGroup
from app.services.source_store import fetch_source_paths

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
    full_text: bool = False,
) -> list:
    """Hydrate concept and chunk hits using an already-open DB session."""
    concept_pairs: list[tuple[str, str]] = []
    chunk_pairs: list[tuple[str, int]] = []
    for hit in hits:
        payload = hit.payload
        # Provenance is canonical SQL data, never a claim in an index payload.
        payload.pop("source_path", None)
        payload.pop("mail_fragment", None)
        payload.pop("source_id", None)
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
                OkfConcept.content if full_text else func.substr(OkfConcept.content, 1, max_concept_chars),
                OkfConcept.source_id,
                OkfConcept.chunk_index,
            ).where(tuple_(OkfConcept.doc_id, OkfConcept.slug).in_(concept_pairs))
        ).all()
        concept_contents = {
            (doc_id, slug): content for doc_id, slug, content, _source_id, _chunk_index in rows
        }
        concept_source_ids = {
            (doc_id, slug): source_id for doc_id, slug, _content, source_id, _chunk_index in rows
        }
        concept_chunk_indices = {(doc_id, slug): index for doc_id, slug, _, _, index in rows}
    else:
        concept_source_ids = {}
        concept_chunk_indices = {}
    needed_chunk_pairs = chunk_pairs if full_text else _chunk_pairs_needed_for_merge(
        hits, concept_contents, chunk_pairs
    )
    skipped_chunk_pairs = set(chunk_pairs) - set(needed_chunk_pairs)
    if needed_chunk_pairs:
        rows = session.execute(
            select(
                DocumentChunk.doc_id,
                DocumentChunk.chunk_index,
                DocumentChunk.content if full_text else func.substr(DocumentChunk.content, 1, max_chunk_chars),
                DocumentChunk.section_title,
                DocumentChunk.source_id,
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
                "source_id": source_id,
            }
            for doc_id, chunk_index, content, section_title, source_id in rows
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
            if key in concept_source_ids:
                payload["source_id"] = concept_source_ids[key]
            if not payload.get("filepath"):
                payload["filepath"] = f"{doc_id}/{slug}.md"
        elif point_type == "chunk":
            chunk_index = payload.get("chunk_index")
            key = (doc_id, int(chunk_index)) if chunk_index is not None else None
            if key in chunk_contents:
                payload["content"] = chunk_contents[key]["content"]
                payload["section_title"] = chunk_contents[key]["section_title"]
                payload["source_id"] = chunk_contents[key]["source_id"]
            elif key in skipped_chunk_pairs:
                continue
            else:
                logger.warning(
                    "Чанк (%s, %s) не найден в document_chunks — рассинхрон БД и Qdrant",
                    doc_id,
                    chunk_index,
                )
    paths = fetch_source_paths(session, {
        (hit.payload["doc_id"], hit.payload["source_id"])
        for hit in hits if hit.payload.get("doc_id") and hit.payload.get("source_id")
    })
    for hit in hits:
        key = (hit.payload.get("doc_id"), hit.payload.get("source_id"))
        if key in paths:
            hit.payload["source_path"] = paths[key]
    # Concept digests may remove quote markers and merge speaker turns. Read
    # bounded original mail text in this same snapshot, even for concept-only hits.
    mail_keys = {}
    for hit in hits:
        payload = hit.payload
        path = payload.get("source_path") or []
        if not path or not path[-1].get("mail"):
            continue
        doc_id = payload.get("doc_id")
        index = (concept_chunk_indices.get((doc_id, _concept_slug(hit)))
                 if payload.get("point_type") == "concept" else payload.get("chunk_index"))
        if index is not None:
            mail_keys[hit.point_id] = (doc_id, index, payload["source_id"])
    if mail_keys:
        rows = session.execute(select(
            DocumentChunk.doc_id, DocumentChunk.chunk_index, DocumentChunk.source_id,
            func.substr(DocumentChunk.content, 1, max_chunk_chars),
        ).where(tuple_(DocumentChunk.doc_id, DocumentChunk.chunk_index, DocumentChunk.source_id)
                .in_(set(mail_keys.values())))).all()
        fragments = {(doc_id, index, source_id): content for doc_id, index, source_id, content in rows}
        for hit in hits:
            key = mail_keys.get(hit.point_id)
            if key in fragments:
                hit.payload["mail_fragment"] = {"chunk_index": key[1], "content": fragments[key]}
    return hits


def enrich_retrieval_hits(hits: list) -> list:
    """Filter stale hits and hydrate canonical text, preserving list identity."""
    visible, _lookup = load_visible_retrieval_hits(hits)
    hits[:] = visible
    return hits


def _filter_exact_hits(hits: list, exact_groups: tuple[MatchGroup, ...]) -> list:
    if not exact_groups:
        return hits
    return [
        hit for hit in hits
        if any(
            group_form_matches(
                f"{hit.payload.get('title', '')}\n{hit.payload.get('content', '')}", group
            )
            for group in exact_groups
        )
    ]


def load_visible_retrieval_hits(
    hits: list,
    *,
    timings: dict[str, float] | None = None,
    max_concept_chars: int | None = None,
    max_chunk_chars: int | None = None,
    exact_groups: tuple[MatchGroup, ...] = (),
) -> tuple[list, dict]:
    """Read visibility, active generation and canonical text consistently.

    SQLite uses a read snapshot; PostgreSQL holds shared Document row locks
    until hydration finishes. A publication racing with Qdrant retrieval may
    remove stale hits, but cannot pair their metadata with a new version's text.
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
        connection = session.connection()
        # SQLite's legacy driver does not BEGIN on SELECT. An explicit read
        # transaction keeps the pointer and canonical text in one snapshot.
        if connection.dialect.name == "sqlite":
            if not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
        statement = select(Document.id, Document.filename, Document.deleted_at).where(
            Document.id.in_(doc_ids)
        ).order_by(Document.id)
        if connection.dialect.name == "postgresql":
            # Publication takes a writer lock on these same rows. Lock them
            # BEFORE reading the pointer so a waited-for writer cannot leave
            # us with a stale joined pointer and freshly published text.
            statement = statement.with_for_update(read=True)
        rows = session.execute(statement).all()
        active_generations = dict(session.execute(
            select(DocumentGenerationState.doc_id, DocumentGenerationState.active_generation_id)
            .where(DocumentGenerationState.doc_id.in_(doc_ids))
        ).all())
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
            and hit.payload.get("generation_id") == active_generations.get(doc["id"])
        ]
        if timings is not None:
            timings["visibility_ms"] = round((perf_counter() - started) * 1000, 3)
        started = perf_counter()
        _enrich_retrieval_hits_in_session(
            visible,
            session,
            max_concept_chars=concept_chars,
            max_chunk_chars=chunk_chars,
            full_text=bool(exact_groups),
        )
        visible = _filter_exact_hits(visible, exact_groups)
        if timings is not None:
            timings["enrichment_ms"] = round((perf_counter() - started) * 1000, 3)
    return visible, doc_lookup
