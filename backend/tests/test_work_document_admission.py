"""Durable admission commits document/original/receipt/work together."""
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Document, WorkItem, UploadReceipt
from app.db.session import session_scope
from app.services.errors import DomainError
from app.services.work.admission import enqueue_document
from tests.test_artifact_upload_commit import uploads as uploads, upload


def future_api(uploads):
    client, settings, started = uploads
    # Exercise the closed future branch; real Settings still reject this runtime.
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    return client, settings, started


def test_upload_commit_enqueues_atomically(uploads):
    client, _, started = future_api(uploads)
    first = upload(client)
    again = upload(client)
    assert first.status_code == again.status_code == 200
    assert first.json()["id"] == again.json()["id"]
    assert started == []
    with session_scope() as session:
        work = session.query(WorkItem).filter_by(kind="document").one()
        assert work.doc_id == first.json()["id"] and work.payload_json["action"] == "process"
        assert session.query(Document).one().status == "queued"
        assert session.query(UploadReceipt).count() == 1


def test_failed_upload_commit_leaves_no_document_or_work(uploads):
    client, _, started = future_api(uploads)
    def fail(session):
        if session.info.get("artifact_upload"):
            raise RuntimeError("interrupted upload commit")
    event.listen(Session, "before_commit", fail)
    try:
        assert upload(client).status_code == 500
    finally:
        event.remove(Session, "before_commit", fail)
    with session_scope() as session:
        assert session.query(Document).count() == session.query(WorkItem).count() == session.query(UploadReceipt).count() == 0
    assert not started


def request(session, settings, doc, key):
    return enqueue_document(session, settings, doc_id=doc * 16, action="process", source_file_hash="a" * 64, request_id=key)


def test_document_admission_budget_is_global_and_replay_ignores_full_queue():
    settings = Settings(_env_file=None, work_max_pending_documents=2)
    with session_scope() as session:
        first = request(session, settings, "a", "one")
        request(session, settings, "b", "two")
    with session_scope() as session:
        assert request(session, settings, "a", "one") == first
    with pytest.raises(DomainError), session_scope() as session:
        request(session, settings, "c", "three")
    with session_scope() as session:
        assert session.query(WorkItem).count() == 2


def test_two_intents_for_one_document_cannot_run_concurrently():
    settings = Settings(_env_file=None)
    with session_scope() as session:
        request(session, settings, "a", "one")
    with pytest.raises(DomainError), session_scope() as session:
        request(session, settings, "a", "another")


def test_full_queue_returns_503_without_partial_document(uploads):
    client, settings, _ = future_api(uploads)
    settings.work_max_pending_documents = 1
    assert upload(client).status_code == 200
    response = upload(client, key="another-upload-attempt-0123456789", payload=b"%PDF other document")
    assert response.status_code == 503 and response.json()["code"] == "queue_overloaded"
    with session_scope() as session:
        assert session.query(Document).count() == session.query(WorkItem).count() == session.query(UploadReceipt).count() == 1


def test_requested_development_is_visible_at_the_work_admission_commit(uploads):
    from app.db.session import get_session_factory
    from app.services.development_registry import get_development_registry
    client, _, _ = future_api(uploads)
    development = get_development_registry().create("12010", "Selected development")
    seen = []
    def observe(session):
        if session.info.get("document_work_admission"):
            with get_session_factory()() as probe:
                seen.append(probe.query(Document).one().development_id)
    event.listen(Session, "after_commit", observe)
    try:
        assert upload(client, development_id=str(development["id"])).status_code == 200
    finally:
        event.remove(Session, "after_commit", observe)
    assert seen == [development["id"]]
