"""Generation-scoped materialized generic chunk representations.

Writers use bounded batches and the existing generation lock. Readers must be
called after document ACL checks, in the caller's canonical read snapshot.
"""

from itertools import islice
from sqlalchemy import select, delete, func
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentGeneration,
    DocumentChunkProjection,
    DocumentChunkFragment,
)
from app.db.session import session_scope
from app.services.generation_store import (
    lock_writable_generation,
    lock_generation_read,
    GenerationConflict,
)
from app.services.source_store import validate_source_references
from app.services.chunk_index_projection import (
    CURRENT_CHUNK_PROJECTION_VERSION,
    build_chunk_index_projection,
    chunk_fragment_point_id,
    chunk_fragment_point_id_from_descriptor,
    digest,
    measure,
)


class ChunkProjectionUnavailable(LookupError):
    """Missing, foreign, unpublished or inconsistent canonical projection."""


def materialize_chunk_projection(
    doc_id, generation_id, chunk_index, title, content, *, source_id=None, batch_size=64
):
    if (
        type(chunk_index) is not int
        or chunk_index < 0
        or type(batch_size) is not int
        or not 1 <= batch_size <= 256
    ):
        raise ValueError("Invalid chunk/batch bounds")
    if source_id is not None and (
        not isinstance(source_id, str) or not source_id or len(source_id) > 255
    ):
        raise ValueError("Invalid source identity")
    projection = build_chunk_index_projection(
        title, content, version=CURRENT_CHUNK_PROJECTION_VERSION
    )
    key = (doc_id, generation_id, chunk_index)
    identity = (
        source_id,
        projection.source_hash,
        projection.title_hash,
        projection.projection_hash,
    )
    with session_scope() as s:
        generation = lock_writable_generation(s, doc_id, generation_id)
        if generation.chunk_projection_version not in {None, CURRENT_CHUNK_PROJECTION_VERSION}:
            raise GenerationConflict("Projection version cannot change during resume")
        # Candidate sources are committed together with canonical chunks at publication.
        generation.chunk_projection_version = CURRENT_CHUNK_PROJECTION_VERSION
        parent = s.get(DocumentChunkProjection, key)
        if parent and (
            parent.version != CURRENT_CHUNK_PROJECTION_VERSION
            or (parent.source_id, parent.source_hash, parent.title_hash, parent.projection_hash) != identity
        ):
            raise GenerationConflict("Projection source changed during resume")
        if parent is None:
            parent = DocumentChunkProjection(
                doc_id=doc_id,
                generation_id=generation_id,
                chunk_index=chunk_index,
                source_id=source_id,
                version=CURRENT_CHUNK_PROJECTION_VERSION,
                source_hash=projection.source_hash,
                title_hash=projection.title_hash,
                projection_hash=projection.projection_hash,
                fragment_count=0,
                complete=False,
            )
            s.add(parent)
        else:
            s.execute(
                delete(DocumentChunkFragment).where(
                    DocumentChunkFragment.doc_id == doc_id,
                    DocumentChunkFragment.generation_id == generation_id,
                    DocumentChunkFragment.chunk_index == chunk_index,
                )
            )
            parent.complete = False
            parent.fragment_count = 0
        if projection.legacy:
            parent.complete = True
            return 0
    iterator = projection.fragments()
    count = 0
    while batch := list(islice(iterator, batch_size)):
        with session_scope() as s:
            lock_writable_generation(s, doc_id, generation_id)
            parent = s.get(DocumentChunkProjection, key)
            if (
                parent is None
                or parent.version != CURRENT_CHUNK_PROJECTION_VERSION
                or parent.complete
                or (parent.source_id, parent.source_hash, parent.title_hash, parent.projection_hash)
                != identity
            ):
                raise GenerationConflict("Projection candidate changed during write")
            for part in batch:
                s.add(
                    DocumentChunkFragment(
                        doc_id=doc_id,
                        generation_id=generation_id,
                        chunk_index=chunk_index,
                        ordinal=part.ordinal,
                        point_id=chunk_fragment_point_id(
                            doc_id, chunk_index, generation_id, source_id, part
                        ),
                        descriptor=part.descriptor,
                        content=part.text,
                        text_hash=part.text_hash,
                    )
                )
            count += len(batch)
            parent.fragment_count = count
    with session_scope() as s:
        lock_writable_generation(s, doc_id, generation_id)
        parent = s.get(DocumentChunkProjection, key)
        actual = s.scalar(
            select(func.count())
            .select_from(DocumentChunkFragment)
            .where(
                DocumentChunkFragment.doc_id == doc_id,
                DocumentChunkFragment.generation_id == generation_id,
                DocumentChunkFragment.chunk_index == chunk_index,
            )
        )
        if (
            parent is None or parent.complete
            or parent.version != CURRENT_CHUNK_PROJECTION_VERSION
            or (parent.source_id, parent.source_hash, parent.title_hash, parent.projection_hash) != identity
            or parent.fragment_count != count or actual != count or count == 0
        ):
            raise GenerationConflict("Incomplete projection write")
        parent.complete = True
    return count


def get_chunk_projection_fragments(
    session, doc_id, generation_id, chunk_index, ordinals, *, source_ids=None
):
    """Caller owns ACL; this reader adds active/trash/source/hash validation."""
    if not isinstance(ordinals, (list, tuple)) or any(
        type(n) is not int or n < 0 for n in ordinals
    ):
        raise ValueError("Invalid fragment selection")
    if (
        len(ordinals) > 256
        or not ordinals
        or len(set(ordinals)) != len(ordinals)
        or any(type(n) is not int or n < 0 for n in ordinals)
    ):
        raise ValueError("Invalid fragment selection")
    active = lock_generation_read(session, [doc_id])
    document = session.get(Document, doc_id, populate_existing=True)
    generation = session.get(DocumentGeneration, generation_id, populate_existing=True)
    parent = session.get(
        DocumentChunkProjection, (doc_id, generation_id, chunk_index), populate_existing=True
    )
    canonical = session.execute(
        select(
            DocumentChunk.source_id, DocumentChunk.content_hash, canonical_title_hash(session)
        ).where(DocumentChunk.doc_id == doc_id, DocumentChunk.chunk_index == chunk_index)
    ).one_or_none()
    if (
        document is None
        or document.deleted_at is not None
        or active.get(doc_id) != generation_id
        or generation is None
        or generation.doc_id != doc_id
        or generation.phase != "active"
        or generation.chunk_projection_version != CURRENT_CHUNK_PROJECTION_VERSION
        or parent is None
        or parent.version != CURRENT_CHUNK_PROJECTION_VERSION
        or not parent.complete
        or parent.fragment_count <= 0
        or any(n >= parent.fragment_count for n in ordinals)
        or canonical is None
        or canonical[0] != parent.source_id
        or canonical[1] != parent.source_hash
        or canonical[2] != parent.title_hash
        or (source_ids is not None and parent.source_id not in source_ids)
    ):
        raise ChunkProjectionUnavailable()
    try:
        validate_source_references(
            session, doc_id, {parent.source_id} if parent.source_id is not None else set()
        )
    except ValueError:
        raise ChunkProjectionUnavailable() from None
    rows = list(
        session.scalars(
            select(DocumentChunkFragment)
            .where(
                DocumentChunkFragment.doc_id == doc_id,
                DocumentChunkFragment.generation_id == generation_id,
                DocumentChunkFragment.chunk_index == chunk_index,
                DocumentChunkFragment.ordinal.in_(ordinals),
            )
            .order_by(DocumentChunkFragment.ordinal)
        )
    )
    if {row.ordinal for row in rows} != set(ordinals):
        raise ChunkProjectionUnavailable()
    for row in rows:
        d = row.descriptor
        try:
            valid = (
                d["ordinal"] == row.ordinal
                and d["source_hash"] == parent.source_hash
                and d["title_hash"] == parent.title_hash
                and d["projection_hash"] == parent.projection_hash
                and d["text_hash"] == row.text_hash
                and digest(row.content) == row.text_hash
                and measure(row.content) <= 2000
                and row.point_id
                == chunk_fragment_point_id_from_descriptor(
                    doc_id, chunk_index, generation_id, parent.source_id, d
                )
            )
        except (ValueError, TypeError, KeyError):
            valid = False
        if not valid:
            raise ChunkProjectionUnavailable()
    return rows


def canonical_title_hash(session):
    """Hash current SQL title without returning its unbounded value to callers.

    PostgreSQL hashes in the server. SQLite's deterministic connection-local UDF
    still decodes the title transiently; RSS/CPU acceptance must include that cost.
    """
    title = func.coalesce(DocumentChunk.section_title, "")
    if session.get_bind().dialect.name == "postgresql":
        return func.encode(func.sha256(func.convert_to(title, "UTF8")), "hex")
    connection = session.connection().connection.driver_connection
    connection.create_function("r2_title_sha256", 1, digest, deterministic=True)
    return func.r2_title_sha256(title)
