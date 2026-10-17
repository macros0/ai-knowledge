"""Materialized generic parts obey candidate and canonical-generation boundaries."""

import pytest
from sqlalchemy import select, func
from app.db.models import (
    Document,
    DocumentChunk,
    DocumentChunkProjection,
    DocumentChunkFragment,
    DocumentGeneration,
)
from app.db.session import session_scope
from app.services.generation_store import (
    begin_generation,
    mark_generation_ready,
    publish_generation,
    GenerationConflict,
)
from app.services.chunk_index_projection import digest
from app.services.chunk_projection_store import (
    materialize_chunk_projection,
    get_chunk_projection_fragments,
    ChunkProjectionUnavailable,
)

DOC = "chunk-proj-doc"
BODY = "physical row 0 False\n" * 600
TITLE = "Section"


def candidate():
    with session_scope() as s:
        if s.get(Document, DOC) is None:
            s.add(Document(id=DOC, filename="source.docx", status="done"))
            s.flush()
        return begin_generation(s, DOC).id


def activate(gen, body=BODY):
    with session_scope() as s:
        s.query(DocumentChunk).filter_by(doc_id=DOC).delete()
        s.add(
            DocumentChunk(
                doc_id=DOC,
                chunk_index=0,
                section_title=TITLE,
                content=body,
                content_hash=digest(body),
                char_count=len(body),
            )
        )
        mark_generation_ready(s, DOC, gen)
        publish_generation(s, DOC, gen)


def write(gen, body=BODY, batch_size=3):
    return materialize_chunk_projection(
        DOC, gen, 0, TITLE, body, source_id=None, batch_size=batch_size
    )


def test_materialization_is_bounded_complete_and_resume_is_idempotent():
    gen = candidate()
    count = write(gen)
    assert count > 1
    assert write(gen) == count
    with session_scope() as s:
        parent = s.get(DocumentChunkProjection, (DOC, gen, 0))
        assert parent.complete and parent.fragment_count == count
        assert s.scalar(select(func.count()).select_from(DocumentChunkFragment)) == count
        assert s.get(DocumentGeneration, gen).chunk_projection_version == 1


def test_candidate_invisible_then_active_reads_only_selected_exact_parts():
    gen = candidate()
    count = write(gen)
    with session_scope() as s, pytest.raises(ChunkProjectionUnavailable):
        get_chunk_projection_fragments(s, DOC, gen, 0, [0])
    activate(gen)
    with session_scope() as s:
        rows = get_chunk_projection_fragments(s, DOC, gen, 0, [0, count - 1])
        assert [row.ordinal for row in rows] == [0, count - 1]
        assert all(len(row.content.encode()) <= 2000 for row in rows)
        assert all(row.text_hash == digest(row.content) for row in rows)
        assert rows[0].point_id != rows[1].point_id


def test_same_generation_source_change_and_active_write_are_rejected():
    gen = candidate()
    write(gen)
    with pytest.raises(GenerationConflict):
        write(gen, BODY + "changed")
    activate(gen)
    with pytest.raises(GenerationConflict):
        write(gen)


@pytest.mark.parametrize(
    "mutation",
    [
        "raw_hash",
        "title",
        "source",
        "part_text",
        "part_descriptor",
        "incomplete",
        "unknown_version",
    ],
)
def test_reader_rejects_inconsistent_canonical_and_materialized_parts(mutation):
    gen = candidate()
    write(gen)
    activate(gen)
    with session_scope() as s:
        canonical = s.scalar(select(DocumentChunk).where(DocumentChunk.doc_id == DOC))
        parent = s.get(DocumentChunkProjection, (DOC, gen, 0))
        part = s.get(DocumentChunkFragment, (DOC, gen, 0, 0))
        if mutation == "raw_hash":
            canonical.content_hash = "0" * 64
        elif mutation == "title":
            canonical.section_title = "changed"
        elif mutation == "source":
            parent.source_id = "alien"
        elif mutation == "part_text":
            part.content += "changed"
        elif mutation == "part_descriptor":
            part.descriptor = {**part.descriptor, "ordinal": 999}
        elif mutation == "incomplete":
            parent.complete = False
        else:
            s.get(DocumentGeneration, gen).chunk_projection_version = 999
    with session_scope() as s, pytest.raises(ChunkProjectionUnavailable):
        get_chunk_projection_fragments(s, DOC, gen, 0, [0])


def test_trash_and_retired_generation_are_unavailable():
    first = candidate()
    write(first)
    activate(first)
    second = candidate()
    write(second)
    activate(second)
    with session_scope() as s, pytest.raises(ChunkProjectionUnavailable):
        get_chunk_projection_fragments(s, DOC, first, 0, [0])
    with session_scope() as s:
        from datetime import datetime, timezone

        s.get(Document, DOC).deleted_at = datetime.now(timezone.utc)
    with session_scope() as s, pytest.raises(ChunkProjectionUnavailable):
        get_chunk_projection_fragments(s, DOC, second, 0, [0])


def test_generation_delete_cascades_materialized_parts():
    gen = candidate()
    write(gen)
    with session_scope() as s:
        s.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
        s.delete(s.get(DocumentGeneration, gen))
        s.flush()
        assert s.scalar(select(func.count()).select_from(DocumentChunkProjection)) == 0
        assert s.scalar(select(func.count()).select_from(DocumentChunkFragment)) == 0


def test_short_legacy_chunk_needs_no_materialized_part():
    gen = candidate()
    assert write(gen, "short") == 0
    with session_scope() as s:
        parent = s.get(DocumentChunkProjection, (DOC, gen, 0))
        assert parent.complete and parent.fragment_count == 0
        assert parent.source_hash == digest("short")
        assert s.scalar(select(func.count()).select_from(DocumentChunkFragment)) == 0


def test_missing_parts_duplicate_requests_and_invalid_ordinals_are_rejected():
    gen = candidate()
    count = write(gen)
    activate(gen)
    for ordinals in [[count], [-1], [True], [0, 0], list(range(257)), [{}], [[0]], "0"]:
        with session_scope() as s, pytest.raises((ValueError, ChunkProjectionUnavailable)):
            get_chunk_projection_fragments(s, DOC, gen, 0, ordinals)


def test_registry_delete_cleans_parts_even_with_sqlite_foreign_keys_disabled():
    from app.services.registry import DocumentRegistry

    gen = candidate()
    write(gen)
    assert DocumentRegistry().delete(DOC)
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(DocumentChunkProjection)) == 0
        assert s.scalar(select(func.count()).select_from(DocumentChunkFragment)) == 0


def test_generation_cleanup_removes_only_inactive_projection(tmp_path):
    from app.config import Settings
    from app.services.generation_store import abandon_generation
    from app.services.generation_files import cleanup_generation_files

    first = candidate()
    write(first)
    activate(first)
    second = candidate()
    write(second)
    with session_scope() as s:
        abandon_generation(s, DOC, second)
    with session_scope() as s:
        cleanup_generation_files(s, Settings(_env_file=None, data_dir=tmp_path), DOC, second)
        assert s.get(DocumentChunkProjection, (DOC, second, 0)) is None
        assert s.get(DocumentChunkProjection, (DOC, first, 0)) is not None
        assert get_chunk_projection_fragments(s, DOC, first, 0, [0])


def test_new_candidate_source_can_be_materialized_before_canonical_publication():
    gen = candidate()
    assert (
        materialize_chunk_projection(
            DOC, gen, 0, TITLE, BODY, source_id="new-attachment", batch_size=3
        )
        > 1
    )
    with session_scope() as s, pytest.raises(ChunkProjectionUnavailable):
        get_chunk_projection_fragments(s, DOC, gen, 0, [0])


def test_active_part_with_missing_canonical_source_tree_is_unavailable():
    gen = candidate()
    materialize_chunk_projection(DOC, gen, 0, TITLE, BODY, source_id="new-attachment")
    activate(gen)
    with session_scope() as s:
        s.scalar(
            select(DocumentChunk).where(DocumentChunk.doc_id == DOC)
        ).source_id = "new-attachment"
    with session_scope() as s, pytest.raises(ChunkProjectionUnavailable):
        get_chunk_projection_fragments(s, DOC, gen, 0, [0])


def test_published_source_tree_and_requested_scope_allow_only_own_parts():
    from app.services.source_store import replace_sources

    gen = candidate()
    materialize_chunk_projection(DOC, gen, 0, TITLE, BODY, source_id="new-attachment")
    activate(gen)
    with session_scope() as s:
        replace_sources(
            s,
            DOC,
            [
                {"source_id": "root", "parent_source_id": None, "kind": "document", "metadata": {}},
                {
                    "source_id": "new-attachment",
                    "parent_source_id": "root",
                    "kind": "attachment",
                    "metadata": {},
                },
            ],
        )
        s.scalar(
            select(DocumentChunk).where(DocumentChunk.doc_id == DOC)
        ).source_id = "new-attachment"
    with session_scope() as s:
        assert get_chunk_projection_fragments(s, DOC, gen, 0, [0], source_ids={"new-attachment"})
    with session_scope() as s, pytest.raises(ChunkProjectionUnavailable):
        get_chunk_projection_fragments(s, DOC, gen, 0, [0], source_ids={"root"})


@pytest.mark.parametrize("change", ["title", "body", "source", "short-to-long"], ids=["title", "body", "source", "short-to-long"])
def test_short_retry_rejects_changed_identity(change):
    gen = candidate()
    assert materialize_chunk_projection(DOC, gen, 0, "title", "short", source_id="root") == 0
    args = {"title": "title", "content": "short", "source_id": "root"}
    args[{"title": "title", "body": "content", "source": "source_id", "short-to-long": "content"}[change]] = BODY if change == "short-to-long" else "changed"
    with pytest.raises(GenerationConflict):
        materialize_chunk_projection(DOC, gen, 0, **args)


def test_short_retry_keeps_binding_and_legacy_point_identity():
    from app.services.chunk_index_projection import build_chunk_index_projection, chunk_fragment_point_id
    from app.services.vector_store import chunk_point_id
    gen = candidate()
    assert write(gen, "short") == write(gen, "short") == 0
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(DocumentChunkProjection)) == 1
        assert s.scalar(select(func.count()).select_from(DocumentChunkFragment)) == 0
    part = next(build_chunk_index_projection(TITLE, "short", version=1).fragments())
    assert chunk_fragment_point_id(DOC, 0, gen, None, part) == chunk_point_id(DOC, 0, generation_id=gen)


def test_long_to_short_retry_rejects_changed_identity():
    gen = candidate()
    write(gen)
    with pytest.raises(GenerationConflict):
        write(gen, "short")


def test_unknown_parent_version_cannot_be_reinterpreted_on_resume():
    gen = candidate()
    write(gen)
    with session_scope() as s:
        s.get(DocumentChunkProjection, (DOC, gen, 0)).version = 999
    with pytest.raises(GenerationConflict):
        write(gen)


def test_interrupted_batch_stays_incomplete_and_resume_rebuilds_exact_parts(monkeypatch):
    from app.services.chunk_index_projection import ChunkIndexProjection
    original = ChunkIndexProjection.fragments
    def interrupted(projection):
        for i, part in enumerate(original(projection)):
            if i == 3:
                raise RuntimeError("synthetic batch interruption")
            yield part
    gen = candidate()
    with monkeypatch.context() as patch:
        patch.setattr(ChunkIndexProjection, "fragments", interrupted)
        with pytest.raises(RuntimeError, match="synthetic batch interruption"):
            write(gen, batch_size=3)
    with session_scope() as s:
        parent = s.get(DocumentChunkProjection, (DOC, gen, 0))
        assert not parent.complete and parent.fragment_count == 3
    count = write(gen, batch_size=3)
    assert count > 3
    with session_scope() as s:
        parent = s.get(DocumentChunkProjection, (DOC, gen, 0))
        parts = list(s.scalars(select(DocumentChunkFragment).order_by(DocumentChunkFragment.ordinal)))
        assert parent.complete and parent.fragment_count == count
        assert [part.ordinal for part in parts] == list(range(count))
