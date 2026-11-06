"""Versioned, per-generation index maintenance never scans a neighbour document."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.db.models import Document, DocumentChunk, DocumentGeneration, DocumentGenerationState, OkfConcept, WorkItem
from app.db.session import session_scope
from app.services.vector_store import concept_point_id
from app.services.work.handlers import WorkDeferred
from app.services.work.models import WorkSpec, LostOwnership
from app.services.work.runtime import build_handlers
from app.services.work.store import enqueue, claim
from tests.test_backfills import _vs, _Rec, FakeEmbedder
from tests.test_work_store import expire


def create_document(session, doc_id):
    session.add(Document(id=doc_id, filename="test.docx", status="done"))
    session.flush()
    generation = uuid4().hex
    session.add(DocumentGeneration(id=generation, doc_id=doc_id, phase="active"))
    session.flush()
    session.add(DocumentGenerationState(doc_id=doc_id, active_generation_id=generation))
    return generation


def take(kind):
    with session_scope() as session:
        return claim(session, owner_id=uuid4().hex, supported={kind: {1}}, lease_seconds=120)


def test_index_sweep_cursor_survives_reclaim_without_duplicate_children(monkeypatch):
    settings = Settings(_env_file=None)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    with session_scope() as session:
        for number in range(101):
            create_document(session, f"{number:016x}")
        enqueue(session, WorkSpec("index_backfill_sweep", 1, {}, "index-sweep"))
    handler = build_handlers().resolve(WorkSpec("index_backfill_sweep", 1, {}, "index-sweep"))
    old = take("index_backfill_sweep")
    assert handler(old) == WorkDeferred(1)
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="index_backfill").count() == 300
    expire(old.work_id)
    current = take("index_backfill_sweep")
    assert handler(current) is None
    with pytest.raises(LostOwnership):
        handler(old)
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="index_backfill").count() == 303


@pytest.mark.parametrize("mode", ["sparse", "relations", "chunks"])
@pytest.mark.parametrize("superseded", [False, True])
def test_owned_backfill_touches_only_its_frozen_generation(tmp_path, monkeypatch, mode, superseded):
    doc_id, other = "a" * 16, "b" * 16
    with session_scope() as session:
        generation = create_document(session, doc_id)
        neighbour = create_document(session, other)
        for identifier in (doc_id, other):
            session.add(OkfConcept(doc_id=identifier, slug="concept", title="Title", content="Actual content", relations=["target"]))
            session.add(DocumentChunk(doc_id=identifier, chunk_index=0, content="Chunk content", content_hash="a" * 64, char_count=13))
    point = concept_point_id(doc_id, "concept", generation_id=generation)
    other_point = concept_point_id(other, "concept", generation_id=neighbour)
    store, fake = _vs(tmp_path, monkeypatch, [
        _Rec(point, vector={"": [0.] * 8}, payload={"doc_id": doc_id, "generation_id": generation}),
        _Rec(other_point, vector={"": [0.] * 8}, payload={"doc_id": other, "generation_id": neighbour})])
    settings = store.settings
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "worker"
    pipeline = SimpleNamespace(settings=settings, vector_store=store, embedder=FakeEmbedder())
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    spec = WorkSpec("index_backfill", 1, {"doc_id": doc_id, "generation_id": generation, "mode": mode}, "index-one", doc_id)
    with session_scope() as session:
        enqueue(session, spec)
    work = take("index_backfill")
    if superseded:
        from concurrent.futures import ThreadPoolExecutor
        method = getattr(store, "backfill_" + mode)
        def replace_generation():
            replacement = uuid4().hex
            with session_scope() as session:
                session.get(DocumentGeneration, generation).phase = "retired"
                session.add(DocumentGeneration(id=replacement, doc_id=doc_id, phase="active"))
                session.flush()
                session.get(DocumentGenerationState, doc_id).active_generation_id = replacement
            fake.records.append(_Rec(concept_point_id(doc_id, "concept", generation_id=replacement),
                vector={"": [0.] * 8}, payload={"doc_id": doc_id, "generation_id": replacement}))
        def changed(*args, **kwargs):
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(replace_generation).result(timeout=10)
            return method(*args, **kwargs)
        monkeypatch.setattr(store, "backfill_" + mode, changed)
    build_handlers().resolve(spec)(work)
    if superseded:
        assert not fake.updated_vectors and not fake.payload_updates and not fake.upserted
        return
    if mode == "sparse":
        assert [str(row.id) for row in fake.updated_vectors] == [point]
    elif mode == "relations":
        assert fake.payload_updates == [({"relations": ["target"]}, [point])]
    else:
        assert fake.upserted and {row.payload["doc_id"] for row in fake.upserted} == {doc_id}


@pytest.mark.parametrize("mode", ["sparse", "relations", "chunks"])
def test_future_backfill_requires_scoped_ownership(tmp_path, monkeypatch, mode):
    store, _ = _vs(tmp_path, monkeypatch, [])
    store.settings.okf_deployment_schema, store.settings.app_role = "replicas-v1", "worker"
    with pytest.raises(LostOwnership):
        getattr(store, "backfill_" + mode)(*([FakeEmbedder()] if mode == "chunks" else []))


def test_failed_chunk_batch_is_retryable_in_owned_work(tmp_path, monkeypatch):
    import httpx
    from app.services.work.handlers import TransientWorkError
    doc_id = "a" * 16
    with session_scope() as session:
        generation = create_document(session, doc_id)
        session.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="Accepted input", content_hash="a" * 64, char_count=14))
        spec = WorkSpec("index_backfill", 1, {"doc_id": doc_id, "generation_id": generation, "mode": "chunks"}, "chunk-retry", doc_id)
        enqueue(session, spec)
    store, fake = _vs(tmp_path, monkeypatch, [])
    store.settings.okf_deployment_schema, store.settings.app_role = "replicas-v1", "worker"
    monkeypatch.setattr("app.config.get_settings", lambda: store.settings)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: SimpleNamespace(vector_store=store, embedder=FakeEmbedder()))
    def offline(**_kwargs):
        raise httpx.ConnectError("temporary outage")
    monkeypatch.setattr(fake, "upsert", offline)
    work = take("index_backfill")
    with pytest.raises(TransientWorkError):
        build_handlers().resolve(spec)(work)
