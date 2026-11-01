"""Retained trash is rechecked before SQL purge and durable byte cleanup."""
from datetime import datetime, timedelta, timezone

import pytest
from qdrant_client import models as qm

from app.db.models import Document, WorkItem
from app.db.session import session_scope
from app.services import audit
from app.services.work.models import WorkSpec
from app.services.work.runtime import build_handlers
from app.services.work.store import enqueue, claim
from tests.test_generation_pipeline import DOC_ID, _published_snapshot, pipeline_env as pipeline_env
from tests.test_work_document_handler import durable_original


@pytest.fixture
def retained(pipeline_env, tmp_path, monkeypatch):
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "worker"
    pipeline.settings.trash_retention_days = 7
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    pipeline.soft_delete(DOC_ID, deleted_by="admin")
    expired = datetime.now(timezone.utc) - timedelta(days=10)
    with session_scope() as session:
        session.get(Document, DOC_ID).deleted_at = expired
        enqueue(session, WorkSpec("trash_purge", 1, {"doc_id": DOC_ID, "deleted_at": expired.isoformat()}, "purge", DOC_ID))
        work = claim(session, owner_id="purge", supported={"trash_purge": {1}}, lease_seconds=120)
    return pipeline, work


def test_trash_work_does_not_purge_a_restored_document(retained):
    pipeline, work = retained
    before = _published_snapshot(pipeline)
    pipeline.restore(DOC_ID)
    build_handlers().resolve(work.spec)(work)
    assert pipeline.registry.get(DOC_ID)["deleted_at"] is None
    assert _published_snapshot(pipeline) == before
    assert not any(entry["action_type"] == audit.DOCUMENT_AUTO_DELETE for entry in audit.get_audit().query(target_id=DOC_ID))


def test_trash_audit_failure_rolls_back_document_and_cleanup_intent(retained, monkeypatch):
    pipeline, work = retained
    before = _published_snapshot(pipeline)
    def fail(*_a, **_k):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(audit, "record_in_session", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        build_handlers().resolve(work.spec)(work)
    assert pipeline.registry.get(DOC_ID)["deleted_at"] is not None
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="trash_cleanup").count() == 0


def test_trash_work_commits_audit_and_followup_cleanup_with_sql_purge(retained):
    pipeline, work = retained
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.get(Document, DOC_ID) is None
        followup = session.query(WorkItem).filter_by(kind="trash_cleanup").one()
        assert followup.payload_json == {"doc_id": DOC_ID}
        cleanup = claim(session, owner_id="cleanup", supported={"trash_cleanup": {1}}, lease_seconds=120)
    entries = audit.get_audit().query(target_id=DOC_ID)
    assert sum(entry["action_type"] == audit.DOCUMENT_AUTO_DELETE for entry in entries) == 1
    build_handlers().resolve(cleanup.spec)(cleanup)
    points, _ = pipeline.vector_store.client.scroll(collection_name=pipeline.vector_store.collection,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=DOC_ID))]), limit=100)
    assert points == []
    assert list(pipeline.settings.uploads_dir.glob(DOC_ID + ".*")) == []


def test_trash_sweep_persists_cursor_and_skips_recent_deletions(retained):
    from app.services.work.store import finish
    from app.services.work.progress import load_progress
    _, previous = retained
    with session_scope() as session:
        finish(session, previous, outcome="succeeded")
        session.add(Document(id="b" * 16, filename="recent.pdf", deleted_at=datetime.now(timezone.utc)))
        enqueue(session, WorkSpec("trash_sweep", 1, {}, "sweep"))
        work = claim(session, owner_id="sweep", supported={"trash_sweep": {1}}, lease_seconds=120)
    handler = build_handlers().resolve(work.spec)
    handler(work)
    handler(work)
    with session_scope() as session:
        pending = session.query(WorkItem).filter_by(kind="trash_purge", status="queued").one()
        assert pending.doc_id == DOC_ID and pending.payload_json["doc_id"] == DOC_ID
        assert load_progress(session, work, version=1) == {"last_id": DOC_ID}
