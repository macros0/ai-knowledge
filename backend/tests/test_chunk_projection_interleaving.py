"""Actual transaction interleavings at a committed batch boundary."""
import pytest
from sqlalchemy import select
from app.db.models import DocumentChunkFragment, DocumentChunkProjection, DocumentGeneration
from app.db.session import session_scope
from app.services.chunk_index_projection import ChunkIndexProjection, build_chunk_index_projection
from app.services.generation_artifacts import GenerationIntegrityError
from app.services.generation_store import GenerationConflict, mark_generation_ready, publish_generation
from tests.test_chunk_projection_store import BODY, DOC, TITLE, activate, candidate, write


def interleave(monkeypatch, action):
    original = ChunkIndexProjection.fragments
    fired = []
    def fragments(projection):
        for i, part in enumerate(original(projection)):
            if i == 3 and not fired:
                fired.append(True)
                action()
            yield part
    monkeypatch.setattr(ChunkIndexProjection, "fragments", fragments)
    return fired


def assert_complete_exact(gen, count):
    expected = list(build_chunk_index_projection(TITLE, BODY, version=1).fragments())
    with session_scope() as s:
        parent = s.get(DocumentChunkProjection, (DOC, gen, 0))
        rows = list(s.scalars(select(DocumentChunkFragment).where(
            DocumentChunkFragment.generation_id == gen).order_by(DocumentChunkFragment.ordinal)))
        assert parent.complete and parent.fragment_count == count == len(expected)
        assert [(r.ordinal, r.content, r.text_hash, r.descriptor) for r in rows] == [
            (p.ordinal, p.text, p.text_hash, p.descriptor) for p in expected]


def test_same_identity_second_writer_completes_first_cannot_overwrite(monkeypatch):
    gen = candidate()
    counts = []
    fired = interleave(monkeypatch, lambda: counts.append(write(gen)))
    with pytest.raises(GenerationConflict, match="candidate changed"):
        write(gen)
    assert fired == [True] and len(counts) == 1
    assert_complete_exact(gen, counts[0])


def test_different_identity_second_writer_cannot_corrupt_first(monkeypatch):
    gen = candidate()
    def second():
        with pytest.raises(GenerationConflict, match="source changed"):
            write(gen, body=BODY + "foreign row")
    fired = interleave(monkeypatch, second)
    count = write(gen)
    assert fired == [True]
    assert_complete_exact(gen, count)


def test_ready_and_publish_between_batches_preserve_previous_active(monkeypatch):
    old = candidate()
    write(old)
    activate(old)
    gen = candidate()
    def premature():
        with session_scope() as s:
            parent = s.get(DocumentChunkProjection, (DOC, gen, 0))
            assert not parent.complete and parent.fragment_count == 3
        with pytest.raises(GenerationIntegrityError), session_scope() as s:
            mark_generation_ready(s, DOC, gen)
        with pytest.raises(GenerationConflict), session_scope() as s:
            publish_generation(s, DOC, gen)
        with session_scope() as s:
            assert s.get(DocumentGeneration, old).phase == "active"
            assert s.get(DocumentGeneration, gen).phase == "preparing"
    fired = interleave(monkeypatch, premature)
    count = write(gen)
    assert fired == [True]
    assert_complete_exact(gen, count)
    with session_scope() as s:
        assert s.get(DocumentGeneration, old).phase == "active"
        assert s.get(DocumentGeneration, gen).phase == "preparing"
