"""Frozen processing policy belongs to admission, before any worker can claim."""
import uuid

import pytest

from app.db.models import DocumentProcessingReview, WorkItem
from app.db.session import session_scope
from app.services.errors import DomainError
from tests.test_processing_scope_admission import setup_admission
from tests.test_processing_scope_api import upload_client as upload_client, base_upload_client as base_upload_client
from tests.test_processing_scope_api import upload_mail


def future_scope(upload_client, tmp_path):
    _, settings, _ = upload_client
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    settings.artifact_uploads_enabled = True
    settings.artifact_root = tmp_path / "blobs"


def test_confirmed_scope_and_work_commit_atomically(upload_client, tmp_path, monkeypatch):
    future_scope(upload_client, tmp_path)
    admission, doc, review, _, started = setup_admission(upload_client, monkeypatch)
    monkeypatch.setattr(admission, "get_pipeline", lambda: pytest.fail("API must not start an executor"))
    request_id = uuid.uuid4().hex
    result = admission.confirm_processing_scope(doc["id"], review["id"], review["revision"], request_id, actor="editor")
    assert result.status == "accepted" and not started
    with session_scope() as session:
        from app.db.models import DocumentWorkInput
        work = session.query(WorkItem).filter_by(kind="document").one()
        frozen = session.get(DocumentWorkInput, work.id)
        row = session.get(DocumentProcessingReview, doc["id"])
        assert frozen.scope_request_id == request_id and frozen.processing_scope_json["selection_hash"] == row.selection_hash
        assert row.state == "submitted"
    assert admission.confirm_processing_scope(doc["id"], review["id"], review["revision"], request_id, actor="editor").status == "already_accepted"


def test_scope_queue_full_preserves_editable_review(upload_client, tmp_path, monkeypatch):
    from app.services.work.admission import enqueue_document
    future_scope(upload_client, tmp_path)
    admission, doc, review, settings, _ = setup_admission(upload_client, monkeypatch)
    settings.work_max_pending_documents = 1
    with session_scope() as session:
        enqueue_document(session, settings, doc_id="a" * 16, action="process", source_file_hash="b" * 64, request_id="full")
    with pytest.raises(DomainError) as exc:
        admission.confirm_processing_scope(doc["id"], review["id"], review["revision"], uuid.uuid4().hex, actor="editor")
    assert exc.value.code == "queue_overloaded"
    with session_scope() as session:
        assert session.get(DocumentProcessingReview, doc["id"]).state == "draft"
        assert session.query(WorkItem).count() == 1


def test_automatic_upload_policy_is_frozen_before_admission(upload_client, tmp_path):
    from app.db.models import DocumentWorkInput
    future_scope(upload_client, tmp_path)
    client, settings, started = upload_client
    settings.attachment_review_enabled, settings.dedup_enabled = True, False
    response = upload_mail(client, 2)
    assert response.status_code == 200, response.text
    with session_scope() as session:
        work = session.query(WorkItem).filter_by(kind="document").one()
        frozen = session.get(DocumentWorkInput, work.id)
        assert frozen is not None and frozen.processing_scope_json["source_file_hash"] == work.payload_json["source_file_hash"]
    assert not started


def test_review_recovery_cannot_revoke_a_durable_queued_confirmation(upload_client, tmp_path, monkeypatch):
    from app.services.processing_scope import recover_processing_reviews
    future_scope(upload_client, tmp_path)
    admission, doc, review, settings, _ = setup_admission(upload_client, monkeypatch)
    admission.confirm_processing_scope(doc["id"], review["id"], review["revision"], uuid.uuid4().hex, actor="editor")
    recover_processing_reviews(settings, force=True)
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, doc["id"])
        assert row.state == "submitted" and row.revision == review["revision"]
