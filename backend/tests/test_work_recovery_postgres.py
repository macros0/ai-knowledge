"""Real PG + Qdrant; independent workers share the filesystem adapter's root.

The host-shared test root does not certify a Kubernetes RWX/CSI provider.
"""
from multiprocessing import get_context
import os
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import text, update

from app.db.models import DocumentGeneration, DocumentGenerationState, WorkItem
from app.db.session import configure_database, get_engine, session_scope
from app.services.work.store import claim
from tests.support.replica_postgres import disposable_postgres
from tests.test_generation_pipeline import DOC_ID, pipeline_env as pipeline_env


@pytest.fixture(autouse=True)
def _db():
    with disposable_postgres() as url:
        yield url


@pytest.fixture
def live_pipeline(pipeline_env, tmp_path):
    endpoint = os.environ.get("OKF_REPLICA_QDRANT_URL")
    if endpoint != "http://127.0.0.1:25433":
        pytest.fail("Explicit owned loopback Qdrant endpoint required")
    pipeline, _, _ = pipeline_env
    store = pipeline.vector_store
    records, _ = store.client.scroll(store.collection, limit=100, with_vectors=True)
    store.client.close()
    store.settings.qdrant_url = endpoint
    store.settings.qdrant_collection = "replica_test_" + uuid4().hex
    store.client = QdrantClient(url=endpoint, timeout=10)
    store.ensure_collection()
    from qdrant_client.http.models import PointStruct
    store.client.upsert(store.collection, [PointStruct(id=row.id, vector=row.vector, payload=row.payload) for row in records], wait=True)
    settings = pipeline.settings
    settings.artifact_root = tmp_path / "shared-adapter-root"
    settings.artifact_root.mkdir()
    settings.artifact_backend, settings.artifact_uploads_enabled = "rwx", True
    try:
        yield pipeline_env
    finally:
        assert store.collection.startswith("replica_test_") and len(store.collection) == 45
        store.client.delete_collection(store.collection)


def _configure_child(url, settings):
    import app.config
    app.config.get_settings = lambda: settings
    configure_database(url)


def _old_document_worker(url, settings, pipe, resume):
    _configure_child(url, settings)
    try:
        from app.services.work.context import document_execution
        from app.services.work.document_handler import prepare_document_attempt
        from app.services.work.models import LostOwnership
        from app.services.registry import DocumentRegistry
        from qdrant_client.http.models import PointStruct
        with session_scope() as session:
            work = claim(session, owner_id="old-process", supported={"document": {1}}, lease_seconds=120)
            generation_id = prepare_document_attempt(session, work, settings).id
        pipe.send((work, generation_id))
        if not resume.wait(600):
            raise RuntimeError("Recovery barrier timed out")
        try:
            with document_execution(work):
                DocumentRegistry().update(DOC_ID, status="processing")
        except LostOwnership:
            outcome = "lost"
        else:
            outcome = "unguarded"
        point_id = str(uuid4())
        client = QdrantClient(url=settings.qdrant_url, timeout=10)
        try:
            client.upsert(settings.qdrant_collection, [PointStruct(id=point_id, vector=[1.0] + [0.0] * 7,
                payload={"doc_id": DOC_ID, "generation_id": generation_id, "point_type": "concept", "slug": "late", "deleted": False})], wait=True)
        finally:
            client.close()
        pipe.send((outcome, point_id))
    finally:
        get_engine().dispose()
        pipe.close()


def _join_owned(process, pipe, release):
    release.set()
    process.join(35)
    if process.is_alive():
        process.terminate()
        process.join(5)
    pipe.close()


def test_old_worker_returns_after_reclaim_and_late_server_write_is_hidden(_db, live_pipeline, tmp_path, monkeypatch):
    from app.services.work.admission import enqueue_existing_document
    from app.services.work.document_handler import run_document_job
    pipeline, _, _ = live_pipeline
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.repository import attach_blob
    with live_pipeline[1].open("rb") as source:
        ref = get_blob_store(pipeline.settings).put(source, object_id=uuid4().hex, media_type="message/rfc822")
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC_ID, generation_id=None, logical_name="original")
    enqueue_existing_document(pipeline.settings, DOC_ID, "regenerate")
    context = get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    release = context.Event()
    process = context.Process(target=_old_document_worker, args=(_db, pipeline.settings, sending, release))
    process.start()
    sending.close()
    try:
        assert receiving.poll(30)
        old, abandoned_id = receiving.recv()
        with session_scope() as session:
            session.execute(update(WorkItem).where(WorkItem.id == old.work_id)
                .values(lease_until=text("clock_timestamp() - interval '1 second'")))
        monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
        from threading import Event
        from app.services.work.handlers import HandlerRegistry
        from app.services.work.worker import Worker
        handlers = HandlerRegistry()
        handlers.register("document", 1, run_document_job)
        runner = Worker(pipeline.settings, registry=handlers, owner_id="replacement")
        assert runner.run_once(Event())
        pipeline.cleanup_inactive_generations()
        with session_scope() as session:
            active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
            assert active != abandoned_id and session.get(DocumentGeneration, abandoned_id).work_cleanup_done
            current = session.get(WorkItem, old.work_id)
            assert current.status == "succeeded" and current.fence > old.fence
        release.set()
        assert receiving.poll(30)
        outcome, point_id = receiving.recv()
        assert outcome == "lost"
        assert pipeline.vector_store.client.retrieve(pipeline.vector_store.collection, [point_id])
        hits = pipeline.vector_store.search_composite(dense_vec=[1.0] + [0.0] * 7, sparse_vec=None, tags=None, branches={"dense"}, top_k=20)
        assert all(hit.point_id != point_id for hit in hits)
        assert pipeline.registry.get(DOC_ID)["status"] == "done"
    finally:
        _join_owned(process, receiving, release)
    assert process.exitcode == 0


def _publish_after_barrier(url, settings, work, generation_id, pipe, release):
    _configure_child(url, settings)
    try:
        from app.services.generation_publication import publish_prepared_document
        from app.services.vector_store import VectorStore
        from app.services.work.models import LostOwnership
        pipe.send("ready")
        if not release.wait(30):
            raise RuntimeError("Publication barrier timed out")
        try:
            # Spawn does not inherit pytest monkeypatches; never let a default
            # settings import select a different Qdrant endpoint/collection.
            vector_store = VectorStore.__new__(VectorStore)
            vector_store.settings = settings
            client = QdrantClient(url=settings.qdrant_url, timeout=10)
            try:
                vector_store.client = client
                publish_prepared_document(settings, vector_store, DOC_ID, generation_id, claim=work)
            finally:
                client.close()
        except LostOwnership:
            pipe.send("lost")
        else:
            pipe.send("published")
    finally:
        get_engine().dispose()
        pipe.close()


@pytest.mark.parametrize("operation", ["cancel", "delete"])
def test_cancel_delete_wins_against_other_process_publication(_db, live_pipeline, monkeypatch, operation):
    from tests.test_generation_cleanup import _prepare_second
    from tests.test_work_document_publication import document_claim, bind_candidate
    pipeline, candidate = _prepare_second(live_pipeline, monkeypatch)
    work = document_claim()
    bind_candidate(candidate, work)
    context = get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    release = context.Event()
    process = context.Process(target=_publish_after_barrier, args=(_db, pipeline.settings, work, candidate, sending, release))
    process.start()
    sending.close()
    try:
        assert receiving.poll(30) and receiving.recv() == "ready"
        if operation == "delete":
            assert pipeline.registry.delete(DOC_ID)
        else:
            from app.services.work.admission import invalidate_document_intents
            with session_scope() as session:
                invalidate_document_intents(session, DOC_ID)
        release.set()
        assert receiving.poll(30) and receiving.recv() == "lost"
        with session_scope() as session:
            assert session.get(WorkItem, work.work_id).cancel_requested
        assert pipeline.registry.get(DOC_ID) is None if operation == "delete" else pipeline.registry.get(DOC_ID)["status"] != "done"
    finally:
        _join_owned(process, receiving, release)
    assert process.exitcode == 0


def _admit_process(url, settings, doc_id, barrier, pipe):
    _configure_child(url, settings)
    try:
        from app.services.work.admission import enqueue_document
        from app.services.errors import DomainError
        barrier.wait(30)
        try:
            with session_scope() as session:
                enqueue_document(session, settings, doc_id=doc_id, action="process", source_file_hash="a" * 64, request_id="simultaneous")
        except DomainError as exc:
            pipe.send(exc.code)
        else:
            pipe.send("accepted")
    finally:
        get_engine().dispose()
        pipe.close()


def test_global_document_admission_budget_across_processes(_db):
    from app.config import Settings
    settings = Settings(_env_file=None, work_max_pending_documents=1)
    context = get_context("spawn")
    barrier = context.Barrier(2)
    handles = []
    outcomes = []
    try:
        for doc_id in ("a" * 16, "b" * 16):
            receiving, sending = context.Pipe(duplex=False)
            process = context.Process(target=_admit_process, args=(_db, settings, doc_id, barrier, sending))
            process.start()
            sending.close()
            handles.append((process, receiving))
        for _, receiving in handles:
            assert receiving.poll(35)
            outcomes.append(receiving.recv())
    finally:
        for process, receiving in handles:
            process.join(35)
            if process.is_alive():
                process.terminate()
                process.join(5)
            receiving.close()
    assert all(process.exitcode == 0 for process, _ in handles)
    assert sorted(outcomes) == ["accepted", "queue_overloaded"]
    with session_scope() as session:
        assert session.query(WorkItem).count() == 1


def test_postgres_document_work_schema_preserves_legacy_generation(_db, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from alembic import command
    from alembic.config import Config
    from app.db.models import Document
    with session_scope() as session:
        session.add(Document(id=DOC_ID, filename="legacy.pdf", status="done"))
        session.flush()
        session.add(DocumentGeneration(id="a" * 32, doc_id=DOC_ID, phase="active"))
        schema = session.scalar(text("select current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=get_engine().url.set(query={}).render_as_string(hide_password=False)))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl02a_document_work")
    command.downgrade(config, "rpl02_work_items")
    command.upgrade(config, "head")
    with session_scope() as session:
        generation = session.get(DocumentGeneration, "a" * 32)
        assert generation.phase == "active" and generation.work_id is None
        generation.work_id, generation.work_fence = "b" * 32, 1
    with pytest.raises(RuntimeError, match="fencing"):
        command.downgrade(config, "rpl02_work_items")
