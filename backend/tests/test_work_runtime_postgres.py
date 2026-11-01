"""Actual PostgreSQL checks for R3 schema, private cursors and coordinators."""
from multiprocessing import get_context
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.db.models import Document, DocumentGeneration, DocumentGenerationState, Job, WorkItem, WorkProgress, DiagnosticBundle
from app.db.session import configure_database, get_engine, session_scope
from app.services.work.models import WorkSpec, LostOwnership
from app.services.work.runtime import build_handlers
from app.services.work.store import enqueue, claim
from tests.support.replica_postgres import disposable_postgres
from tests.test_work_store import expire
from tests.test_processing_scope_change_end_to_end import source_environment as source_environment
from tests.test_work_scope_runtime import future_scope as future_scope, accepted_scope


@pytest.fixture(autouse=True)
def _db():
    with disposable_postgres() as url:
        yield url


def _late_progress(url, work, go, pipe):
    from app.services.work.progress import save_progress
    configure_database(url)
    try:
        if not go.wait(20):
            raise RuntimeError("Progress test barrier timed out")
        try:
            with session_scope() as session:
                save_progress(session, work, version=1, state={"last_id": 99})
        except LostOwnership:
            pipe.send("fenced")
        else:
            pipe.send("unexpected write")
    finally:
        get_engine().dispose()
        pipe.close()


def take(kind):
    with session_scope() as session:
        return claim(session, owner_id=uuid4().hex, supported={kind: {1}}, lease_seconds=120)


def test_progress_reclaim_fences_an_independent_process(_db):
    from app.services.work.progress import save_progress, load_progress
    with session_scope() as session:
        enqueue(session, WorkSpec("test", 1, {}, "pg-progress"))
    old = take("test")
    with session_scope() as session:
        save_progress(session, old, version=1, state={"last_id": 1})
    context = get_context("spawn")
    go = context.Event()
    receiving, sending = context.Pipe(duplex=False)
    process = context.Process(target=_late_progress, args=(_db, old, go, sending))
    process.start()
    sending.close()
    try:
        expire(old.work_id)
        current = take("test")
        go.set()
        assert receiving.poll(25)
        assert receiving.recv() == "fenced"
        with session_scope() as session:
            assert load_progress(session, current, version=1) == {"last_id": 1}
    finally:
        go.set()
        process.join(25)
        if process.is_alive():
            process.terminate()
            process.join(5)
        receiving.close()
    assert process.exitcode == 0


def test_rpl02b_upgrade_preserves_jobs_and_refuses_cursor_loss(_db, monkeypatch):
    from alembic import command
    from alembic.config import Config
    WorkProgress.__table__.drop(get_engine())
    with session_scope() as session:
        session.add(Job(job_type="bulk_delete", status="awaiting_approval", created_by="admin"))
        schema = session.scalar(text("select current_schema()"))
    assert schema.startswith("artifact_") and len(schema) == 41
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=get_engine().url.set(query={}).render_as_string(hide_password=False)))
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.stamp(config, "rpl02a_document_work")
    command.upgrade(config, "rpl02b_work_progress")
    with session_scope() as session:
        assert session.query(Job).one().status == "awaiting_approval"
        enqueue(session, WorkSpec("test", 1, {}, "schema-progress"))
    work = take("test")
    from app.services.work.progress import save_progress
    with session_scope() as session:
        save_progress(session, work, version=1, state={"last_id": 1})
    with pytest.raises(RuntimeError, match="progress"):
        command.downgrade(config, "rpl02a_document_work")


def test_postgres_json_coordinators_and_terminal_repair_cleanup(_db):
    doc_id, active, candidate = "a" * 16, "c" * 32, "d" * 32
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="test.docx", status="done"))
        session.flush()
        session.add(DocumentGeneration(id=active, doc_id=doc_id, phase="active"))
        session.flush()
        session.add(DocumentGenerationState(doc_id=doc_id, active_generation_id=active))
        enqueue(session, WorkSpec("index_backfill_sweep", 1, {}, "pg-index-sweep"))
    sweep = build_handlers().resolve(WorkSpec("index_backfill_sweep", 1, {}, "sweep"))
    sweep(take("index_backfill_sweep"))
    with session_scope() as session:
        enqueue(session, WorkSpec("index_backfill_sweep", 1, {}, "pg-index-sweep-next"))
    sweep(take("index_backfill_sweep"))
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="index_backfill").count() == 3
        job = Job(job_type="reference_translation", status="running", params={})
        session.add(job)
        session.flush()
        job_id = job.id
        parent = enqueue(session, WorkSpec("reference_translation", 1, {"job_id": job_id}, "failed-translation"))
        session.get(WorkItem, parent).status = "failed"
        bundle_id = str(uuid4())
        session.add(DiagnosticBundle(id=bundle_id, status="queued", artifact_version=2, cutoff_at=datetime.now(timezone.utc),
            created_by_id="system", created_by="system", request={}, manifest={}, counts={}, size_bytes=0))
        parent = enqueue(session, WorkSpec("diagnostic_bundle", 1, {"bundle_id": bundle_id}, "failed-bundle"))
        session.get(WorkItem, parent).status = "failed"
        parent = enqueue(session, WorkSpec("canonical_repair", 1, {}, "failed-repair", doc_id))
        session.get(WorkItem, parent).status = "failed"
        session.get(WorkItem, parent).fence = 1
        session.add(DocumentGeneration(id=candidate, doc_id=doc_id, phase="preparing", work_id=parent, work_fence=1))
        session.flush()
        session.get(DocumentGenerationState, doc_id).candidate_generation_id = candidate
        for kind in ("translation_reconcile", "diagnostic_maintenance", "repair_reconcile"):
            enqueue(session, WorkSpec(kind, 1, {}, f"pg-{kind}"))
    for kind in ("translation_reconcile", "diagnostic_maintenance", "repair_reconcile"):
        work = take(kind)
        build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.get(Job, job_id).status == "failed"
        assert session.get(DiagnosticBundle, bundle_id).status == "failed"
        assert session.get(DocumentGenerationState, doc_id).candidate_generation_id is None
        assert session.get(DocumentGeneration, candidate).phase == "abandoned"


def test_scope_publication_reclaims_with_postgres_fencing(_db, future_scope):
    from threading import Event
    from app.services.work.scope import prepare_scope_attempt
    from app.services.work.worker import Worker
    env = future_scope
    old = accepted_scope(env)
    with session_scope() as session:
        previous = prepare_scope_attempt(session, old, env["settings"]).id
    expire(old.work_id)
    env["settings"].work_heartbeat_seconds = 1
    runner = Worker(env["settings"], registry=build_handlers(), owner_id="postgres-scope-worker")
    assert runner.run_once(Event())
    with pytest.raises(LostOwnership):
        build_handlers().resolve(old.spec)(old)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, env["doc_id"])
        assert state.active_generation_id != previous
        item = session.get(WorkItem, old.work_id)
        assert session.get(DocumentGeneration, state.active_generation_id).work_fence == item.fence == old.fence + 1
        assert item.status == "succeeded"
