"""Prepared generations must never leak through retrieval or overwrite active points."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from sqlalchemy import event

from app.config import Settings
from app.db.models import Document, OkfConcept
from app.db.session import get_engine, session_scope
from app.models.schemas import OkfDocument
from app.services.fusion import Hit
from app.services.generation_store import (
    abandon_generation, begin_generation, mark_generation_ready, publish_generation,
)
from app.services.retrieval_hydration import load_visible_retrieval_hits
from app.services.sparse import to_sparse_vector
from app.services.vector_store import VectorStore


DOC_ID = "generationdoc001"


@pytest.fixture
def store():
    result = VectorStore.__new__(VectorStore)
    result.settings = Settings(
        _env_file=None, embedding_dimensions=2, qdrant_collection="generation_test",
        search_graph_expansion_enabled=False,
    )
    result.client = QdrantClient(location=":memory:")
    result.client.create_collection(
        collection_name=result.collection,
        vectors_config=qm.VectorParams(size=2, distance=qm.Distance.COSINE),
        sparse_vectors_config={"sparse": qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
    )
    with session_scope() as session:
        session.add(Document(id=DOC_ID, filename="letter.eml", status="done"))
    yield result
    result.client.close()


def _concept(slug="decision", relations=None):
    return OkfDocument(
        filepath=f"{DOC_ID}/{slug}.md", content="Decision confirmed", markdown="",
        metadata={"title": "Decision", "relations": relations or [], "chunk_index": 0},
    )


def _candidate():
    with session_scope() as session:
        return begin_generation(session, DOC_ID).id


def _publish(generation_id):
    with session_scope() as session:
        mark_generation_ready(session, DOC_ID, generation_id)
        publish_generation(session, DOC_ID, generation_id)


def _search(store, branch):
    return store.search_composite(
        dense_vec=[1.0, 0.0], sparse_vec=to_sparse_vector("Decision"),
        tags=None, branches={branch}, top_k=20,
    )


@pytest.mark.parametrize("branch", ["dense", "bm25"])
@pytest.mark.parametrize("legacy_payload", ["null", "missing"])
def test_search_keeps_legacy_points_until_generation_publication(store, branch, legacy_payload):
    old = store.index_concepts(DOC_ID, [_concept()], [[1.0, 0.0]])
    old |= store.index_chunks(DOC_ID, "letter.eml", ["Decision confirmed"], [], [[1.0, 0.0]])
    if legacy_payload == "missing":
        store.client.delete_payload(store.collection, ["generation_id"], list(old))
    generation_id = _candidate()
    new = store.index_concepts(DOC_ID, [_concept()], [[1.0, 0.0]], generation_id=generation_id)
    new |= store.index_chunks(
        DOC_ID, "letter.eml", ["Decision confirmed"], [], [[1.0, 0.0]], generation_id=generation_id,
    )
    assert old.isdisjoint(new)
    assert store.client.count(store.collection, exact=True).count == 4
    assert {hit.point_id for hit in _search(store, branch)} == old
    _publish(generation_id)
    hits = _search(store, branch)
    assert {hit.point_id for hit in hits} == new
    assert {hit.payload["generation_id"] for hit in hits} == {generation_id}


@pytest.mark.parametrize("branch", ["dense", "bm25"])
def test_candidate_points_cannot_consume_active_search_result_limit(store, branch):
    old = store.index_concepts(DOC_ID, [_concept()], [[0.7, 0.3]])
    generation_id = _candidate()
    concepts = [_concept(f"candidate-{i}") for i in range(40)]
    store.index_concepts(
        DOC_ID, concepts, [[1.0, 0.0]] * len(concepts), generation_id=generation_id,
    )
    assert {hit.point_id for hit in _search(store, branch)} == old


def test_search_excludes_retired_and_abandoned_points_before_cleanup(store):
    first = _candidate()
    store.index_concepts(DOC_ID, [_concept()], [[1.0, 0.0]], generation_id=first)
    _publish(first)
    abandoned = _candidate()
    store.index_concepts(DOC_ID, [_concept()], [[1.0, 0.0]], generation_id=abandoned)
    with session_scope() as session:
        abandon_generation(session, DOC_ID, abandoned)
    second = _candidate()
    current = store.index_concepts(DOC_ID, [_concept()], [[1.0, 0.0]], generation_id=second)
    _publish(second)
    assert store.client.count(store.collection, exact=True).count == 3
    assert {hit.point_id for hit in _search(store, "dense")} == current


def test_graph_expansion_cannot_reveal_unpublished_related_concept(store):
    old = store.index_concepts(DOC_ID, [_concept("related")], [[1.0, 0.0]])
    generation_id = _candidate()
    new = store.index_concepts(
        DOC_ID, [_concept("related")], [[1.0, 0.0]], generation_id=generation_id,
    )
    seed = Hit("seed", 1.0, {"point_type": "concept", "relations": ["related"]})
    before = store._graph_expansion([([seed], 1.0)], search_filter=store._build_search_filter(None))
    assert {hit.point_id for hit in before} == old
    _publish(generation_id)
    after = store._graph_expansion([([seed], 1.0)], search_filter=store._build_search_filter(None))
    assert {hit.point_id for hit in after} == new


def test_candidate_orphan_cleanup_does_not_remove_active_points(store):
    old = store.index_concepts(DOC_ID, [_concept()], [[1.0, 0.0]])
    generation_id = _candidate()
    new = store.index_concepts(DOC_ID, [_concept()], [[1.0, 0.0]], generation_id=generation_id)
    store.delete_orphaned_points(DOC_ID, set(), generation_id=generation_id)
    records, _cursor = store.client.scroll(store.collection, limit=20)
    assert {str(row.id) for row in records} == old
    assert old.isdisjoint(new)


def test_hydration_drops_old_and_unregistered_generations_with_same_slug():
    with session_scope() as session:
        session.add(Document(id=DOC_ID, filename="letter.eml", status="done"))
        session.add(OkfConcept(doc_id=DOC_ID, slug="decision", title="Decision", content="New content"))
        session.flush()
        generation_id = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, generation_id)
        publish_generation(session, DOC_ID, generation_id)
    hits = [
        Hit("legacy", 1.0, {"doc_id": DOC_ID, "point_type": "concept", "slug": "decision"}),
        Hit("unknown", 1.0, {
            "doc_id": DOC_ID, "point_type": "concept", "slug": "decision", "generation_id": "0" * 32,
        }),
        Hit("current", 0.9, {
            "doc_id": DOC_ID, "point_type": "concept", "slug": "decision", "generation_id": generation_id,
        }),
    ]
    visible, _lookup = load_visible_retrieval_hits(hits)
    assert [hit.point_id for hit in visible] == ["current"]
    assert visible[0].payload["content"] == "New content"


def test_hydration_cannot_mix_generation_pointer_and_concurrent_new_content():
    with session_scope() as session:
        session.add(Document(id=DOC_ID, filename="letter.eml", status="done"))
        session.add(OkfConcept(doc_id=DOC_ID, slug="decision", title="Decision", content="Old content"))
        session.flush()
        generation_id = begin_generation(session, DOC_ID).id
        mark_generation_ready(session, DOC_ID, generation_id)
    trigger, committed = Event(), Event()
    reader_thread = get_ident()

    def writer():
        assert trigger.wait(10), "Reader never checked the generation pointer"
        with session_scope() as session:
            publish_generation(session, DOC_ID, generation_id)
            session.query(OkfConcept).filter_by(doc_id=DOC_ID).update({"content": "New content"})
        committed.set()

    def after_read(_conn, _cursor, statement, _parameters, _context, _many):
        if (
            get_ident() == reader_thread and not trigger.is_set()
            and statement.lstrip().upper().startswith("SELECT")
            and "document_generation_states" in statement
        ):
            trigger.set()
            # An unlocked reader lets publication complete here, between the
            # pointer and content SELECTs. A consistent snapshot keeps old text.
            committed.wait(0.5)

    engine = get_engine()
    event.listen(engine, "after_cursor_execute", after_read)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            hits, _lookup = load_visible_retrieval_hits([
                Hit("legacy", 1.0, {"doc_id": DOC_ID, "point_type": "concept", "slug": "decision"}),
            ])
            assert trigger.is_set()
            future.result(timeout=10)
        assert [hit.payload["content"] for hit in hits] == ["Old content"]
        assert committed.is_set()
        with session_scope() as session:
            assert session.query(OkfConcept).one().content == "New content"
    finally:
        event.remove(engine, "after_cursor_execute", after_read)
