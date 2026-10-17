"""Resolve selected generic index parts inside the canonical read snapshot."""

from itertools import islice

from sqlalchemy import String, and_, cast, func, literal_column, select, tuple_
from app.db.models import (
    DocumentChunk,
    DocumentChunkFragment,
    DocumentChunkProjection,
    DocumentGeneration,
)
from app.services.chunk_index_projection import (
    chunk_fragment_point_id_from_descriptor,
    digest,
    measure,
)
from app.services.chunk_projection_store import canonical_title_hash
from app.services.vector_store import chunk_point_id


def hydrate_chunk_projection_hits(hits, session, *, active_generations, original, source_paths):
    """Trusted SQL format selects representation; payloads never select fallback.

    Document visibility and canonical source/mail identities were checked by the
    caller in this same locked snapshot. Metadata is shared across all selected
    parts; only selected part rows are loaded, in batches of at most256.
    """
    generation_ids = set(active_generations.values()) - {None}
    generations = (
        {
            row.id: row
            for row in session.scalars(
                select(DocumentGeneration).where(DocumentGeneration.id.in_(generation_ids))
            )
        }
        if generation_ids
        else {}
    )
    pairs = {
        (h.payload.get("doc_id"), h.payload.get("chunk_index"))
        for h in hits
        if h.payload.get("point_type") == "chunk"
        and type(h.payload.get("chunk_index")) is int
        and (g := generations.get(active_generations.get(h.payload.get("doc_id")))) is not None
        and g.chunk_projection_version == 1
    }
    canonical, parents = {}, {}
    if pairs:
        has_refs = and_(
            DocumentChunk.table_refs.is_not(None),
            cast(DocumentChunk.table_refs, String) != literal_column("'null'"),
            cast(DocumentChunk.table_refs, String) != literal_column("'[]'"),
        )
        canonical = {
            (doc, index): (source, body_hash, title_hash, title, refs)
            for doc, index, source, body_hash, title_hash, title, refs in session.execute(
                select(
                    DocumentChunk.doc_id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.source_id,
                    DocumentChunk.content_hash,
                    canonical_title_hash(session),
                    func.substr(DocumentChunk.section_title, 1, 128),
                    has_refs,
                ).where(tuple_(DocumentChunk.doc_id, DocumentChunk.chunk_index).in_(pairs))
            )
        }
        parents = {
            (p.doc_id, p.generation_id, p.chunk_index): p
            for p in session.scalars(
                select(DocumentChunkProjection).where(
                    tuple_(DocumentChunkProjection.doc_id, DocumentChunkProjection.chunk_index).in_(
                        pairs
                    ),
                    DocumentChunkProjection.generation_id.in_(
                        set(active_generations.values()) - {None}
                    ),
                )
            )
        }
    allowed, selected = [], {}
    for hit in hits:
        payload = hit.payload
        doc, index, gen = (
            payload.get("doc_id"),
            payload.get("chunk_index"),
            payload.get("generation_id"),
        )
        generation = generations.get(gen)
        version = generation.chunk_projection_version if generation else None
        descriptor = payload.get("chunk_fragment")
        if version is not None and (type(version) is not int or version != 1):
            continue
        if payload.get("point_type") != "chunk":
            if descriptor is None:
                allowed.append(hit)
            continue
        if version is None:
            if descriptor is None:
                allowed.append(hit)
            continue
        value = canonical.get((doc, index))
        if (
            generation.phase != "active"
            or generation.doc_id != doc
            or value is None
            or payload.get("_canonical_verified") is not True
        ):
            continue
        source, body_hash, title_hash, title, has_refs = value
        if has_refs:
            if descriptor is None:
                allowed.append(hit)
            continue
        parent = parents.get((doc, gen, index))
        if (
            parent is None
            or parent.version != 1
            or not parent.complete
            or (parent.source_id, parent.source_hash, parent.title_hash)
            != (source, body_hash, title_hash)
            or (source is not None and (doc, source) not in source_paths)
            or original[id(hit)] != (gen, source)
        ):
            continue
        if parent.fragment_count == 0:
            if descriptor is None and hit.point_id == chunk_point_id(doc, index, generation_id=gen):
                allowed.append(hit)
            continue
        try:
            point = chunk_fragment_point_id_from_descriptor(doc, index, gen, source, descriptor)
            ordinal = descriptor["ordinal"]
            if point != hit.point_id or ordinal >= parent.fragment_count:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        selected.setdefault((doc, gen, index, ordinal), []).append((hit, parent, title or ""))
    keys = iter(selected)
    while batch := list(islice(keys, 256)):
        rows = session.scalars(
            select(DocumentChunkFragment).where(
                tuple_(
                    DocumentChunkFragment.doc_id,
                    DocumentChunkFragment.generation_id,
                    DocumentChunkFragment.chunk_index,
                    DocumentChunkFragment.ordinal,
                ).in_(batch)
            )
        )
        for row in rows:
            key = (row.doc_id, row.generation_id, row.chunk_index, row.ordinal)
            for hit, parent, title in selected[key]:
                d = row.descriptor
                try:
                    valid = (
                        d == hit.payload["chunk_fragment"]
                        and d["ordinal"] == row.ordinal
                        and d["source_hash"] == parent.source_hash
                        and d["title_hash"] == parent.title_hash
                        and d["projection_hash"] == parent.projection_hash
                        and d["text_hash"] == row.text_hash
                        and digest(row.content) == row.text_hash
                        and measure(row.content) <= 2000
                        and row.point_id == hit.point_id
                        and row.point_id
                        == chunk_fragment_point_id_from_descriptor(
                            row.doc_id, row.chunk_index, row.generation_id, parent.source_id, d
                        )
                    )
                except (ValueError, TypeError, KeyError):
                    valid = False
                if valid:
                    hit.payload["content"] = row.content
                    hit.payload["section_title"] = title
                    allowed.append(hit)
    allowed_ids = {id(hit) for hit in allowed}
    return [hit for hit in hits if id(hit) in allowed_ids]
