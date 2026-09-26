"""Publication must not expose incomplete or superseded document generations."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.chunk_store import replace_chunks
from app.services.registry import DocumentRegistry


DOC_ID = "generationdoc001"


def _document():
    with session_scope() as session:
        session.add(Document(id=DOC_ID, filename="letter.eml", status="done"))
        session.flush()
        replace_chunks(session, DOC_ID, [{"chunk_index": 0, "content": "Old decision"}])


def test_preparing_generation_preserves_active_legacy_content():
    from app.services.generation_store import begin_generation, get_generation_state

    _document()
    with session_scope() as session:
        generation = begin_generation(session, DOC_ID)
        generation_id = generation.id
    with session_scope() as session:
        state = get_generation_state(session, DOC_ID)
        assert state.active_generation_id is None
        assert state.candidate_generation_id == generation_id
        assert session.query(DocumentChunk).one().content == "Old decision"


def test_unprepared_generation_cannot_be_published():
    from app.services.generation_store import GenerationConflict, begin_generation, publish_generation

    _document()
    with session_scope() as session:
        generation_id = begin_generation(session, DOC_ID).id
    with pytest.raises(GenerationConflict, match="ready"):
        with session_scope() as session:
            publish_generation(session, DOC_ID, generation_id)


def test_failed_publication_rolls_back_pointer_and_canonical_rows_together():
    from app.services.generation_store import (
        begin_generation, get_generation_state, mark_generation_ready, publish_generation,
    )

    _document()
    with session_scope() as session:
        generation_id = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, generation_id)
    with pytest.raises(RuntimeError, match="commit interrupted"):
        with session_scope() as session:
            publish_generation(session, DOC_ID, generation_id)
            replace_chunks(session, DOC_ID, [{"chunk_index": 0, "content": "New decision"}])
            raise RuntimeError("commit interrupted")
    with session_scope() as session:
        state = get_generation_state(session, DOC_ID)
        assert state.active_generation_id is None
        assert state.candidate_generation_id == generation_id
        assert session.query(DocumentChunk).one().content == "Old decision"
        publish_generation(session, DOC_ID, generation_id)
        replace_chunks(session, DOC_ID, [{"chunk_index": 0, "content": "New decision"}])
    with session_scope() as session:
        assert get_generation_state(session, DOC_ID).active_generation_id == generation_id
        assert session.query(DocumentChunk).one().content == "New decision"


def test_superseded_generation_cannot_replace_newer_publication():
    from app.services.generation_store import (
        GenerationConflict, abandon_generation, begin_generation, get_generation_state,
        mark_generation_ready, publish_generation,
    )

    _document()
    with session_scope() as session:
        old = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, old)
        abandon_generation(session, DOC_ID, old)
        new = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, new)
        publish_generation(session, DOC_ID, new)
    with pytest.raises(GenerationConflict):
        with session_scope() as session:
            publish_generation(session, DOC_ID, old)
    with session_scope() as session:
        assert get_generation_state(session, DOC_ID).active_generation_id == new


def test_two_concurrent_starts_admit_only_one_candidate():
    from app.services.generation_store import GenerationConflict, begin_generation

    _document()
    barrier = Barrier(2)

    def start():
        barrier.wait(timeout=10)
        try:
            with session_scope() as session:
                begin_generation(session, DOC_ID)
            return "created"
        except GenerationConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: start(), range(2)))
    assert sorted(results) == ["conflict", "created"]


def test_purge_removes_generation_records_in_sqlite_without_fk_cascade():
    from app.db.models import DocumentGeneration, DocumentGenerationState
    from app.services.generation_store import begin_generation

    _document()
    with session_scope() as session:
        begin_generation(session, DOC_ID)
    registry = DocumentRegistry()
    registry.soft_delete(DOC_ID, "tester")
    assert registry.delete_if_deleted(DOC_ID)
    with session_scope() as session:
        assert session.query(DocumentGeneration).count() == 0
        assert session.query(DocumentGenerationState).count() == 0


def test_second_publication_retires_old_generation_only_when_committed():
    from app.db.models import DocumentGeneration
    from app.services.generation_store import (
        begin_generation, get_generation_state, mark_generation_ready, publish_generation,
    )

    _document()
    with session_scope() as session:
        first = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, first)
        publish_generation(session, DOC_ID, first)
    with session_scope() as session:
        second = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, second)
    with session_scope() as session:
        assert get_generation_state(session, DOC_ID).active_generation_id == first
        assert session.get(DocumentGeneration, first).phase == "active"
        publish_generation(session, DOC_ID, second)
    with session_scope() as session:
        assert get_generation_state(session, DOC_ID).active_generation_id == second
        assert session.get(DocumentGeneration, first).phase == "retired"
        assert session.get(DocumentGeneration, second).phase == "active"


def test_active_generation_cannot_be_abandoned():
    from app.services.generation_store import (
        GenerationConflict, abandon_generation, begin_generation,
        mark_generation_ready, publish_generation,
    )

    _document()
    with session_scope() as session:
        generation_id = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, generation_id)
        publish_generation(session, DOC_ID, generation_id)
    with pytest.raises(GenerationConflict):
        with session_scope() as session:
            abandon_generation(session, DOC_ID, generation_id)
