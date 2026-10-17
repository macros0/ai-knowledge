import hashlib
from types import SimpleNamespace
import uuid

import pytest

from app.db.models import DocumentProcessingReview
from app.db.session import session_scope
from tests.test_processing_scope_api import upload_mail
from tests import test_processing_scope_api as scope_fixtures

upload_client = scope_fixtures.upload_client
base_upload_client = scope_fixtures.base_upload_client


def setup_admission(upload_client, monkeypatch):
    import app.services.processing_admission as admission

    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    monkeypatch.setattr(admission, "get_settings", lambda: settings)
    doc = upload_mail(client, 600).json()
    review = client.get(f"/api/documents/{doc['id']}/processing-scope").json()["pending"]
    return admission, doc, review, settings, started


def test_similarity_uses_selected_content_and_stores_only_token_hash(upload_client, monkeypatch):
    admission, doc, review, settings, started = setup_admission(upload_client, monkeypatch)
    settings.dedup_enabled = True
    inspected = []
    import app.services.deduplication as dedup

    def duplicates(markdown, **kwargs):
        inspected.append(markdown)
        return {
            "level2": [{"doc": {"id": "other-document", "filename": "other.eml"}}],
            "level3": [],
        }

    monkeypatch.setattr(dedup, "find_duplicates_for_text", duplicates)
    monkeypatch.setattr(
        admission,
        "get_pipeline",
        lambda: SimpleNamespace(ingest=lambda *a, **k: started.append(a[0])),
    )
    request_id = uuid.uuid4().hex
    result = admission.confirm_processing_scope(
        doc["id"], review["id"], review["revision"], request_id, actor="editor"
    )
    assert result.status == "similar" and not started
    assert inspected and all("TABLE_CANARY" not in text for text in inspected)
    token = result.detail["similarity_token"]
    with session_scope() as session:
        stored = session.get(DocumentProcessingReview, doc["id"]).similarity_confirmation_json
        assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert token not in str(stored)
    result = admission.confirm_processing_scope(
        doc["id"],
        review["id"],
        review["revision"],
        request_id,
        actor="editor",
        similarity_token=token,
    )
    assert result.status == "accepted" and started == [doc["id"]]


def test_queue_failure_preserves_review_for_retry(upload_client, monkeypatch):
    from app.services.errors import DomainError

    admission, doc, review, settings, started = setup_admission(upload_client, monkeypatch)

    def overloaded(*args, **kwargs):
        raise DomainError("queue full", code="queue_overloaded")

    monkeypatch.setattr(admission, "get_pipeline", lambda: SimpleNamespace(ingest=overloaded))
    with pytest.raises(DomainError, match="queue full"):
        admission.confirm_processing_scope(
            doc["id"], review["id"], review["revision"], uuid.uuid4().hex, actor="editor"
        )
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, doc["id"])
        assert row.state == "draft" and row.generation_id is None
        assert row.id == review["id"] and not row.selection_json[0]["effective_included"]


def test_first_confirm_reserves_similarity_without_switching_published_signature(
    upload_client, monkeypatch
):
    from app.api import documents
    from app.db.models import Document
    import app.services.deduplication as dedup

    admission, first, review, settings, started = setup_admission(upload_client, monkeypatch)
    second = upload_mail(upload_client[0], 600).json()
    second_review = (
        upload_client[0].get(f"/api/documents/{second['id']}/processing-scope").json()["pending"]
    )
    settings.dedup_enabled = True
    monkeypatch.setattr(admission, "get_pipeline", documents.get_pipeline)
    monkeypatch.setattr(dedup, "get_settings", lambda: settings)
    result = admission.confirm_processing_scope(
        first["id"], review["id"], review["revision"], uuid.uuid4().hex, actor="editor"
    )
    assert result.status == "accepted" and len(started) == 1
    with session_scope() as session:
        assert session.get(Document, first["id"]).content_hash is None
        row = session.get(DocumentProcessingReview, first["id"])
        assert (
            row.similarity_confirmation_json["reservation"]["selection_hash"] == row.selection_hash
        )
    result = admission.confirm_processing_scope(
        second["id"],
        second_review["id"],
        second_review["revision"],
        uuid.uuid4().hex,
        actor="editor",
    )
    assert result.status == "similar" and len(started) == 1
    assert any(
        item["doc"]["id"] == first["id"]
        for items in result.detail["duplicates"].values()
        for item in items
    )
    with session_scope() as session:
        session.get(DocumentProcessingReview, first["id"]).state = "canceled"
    # A canceled reservation cannot block another admission.
    assert not any(dedup.find_duplicates_for_text("ROOT EVIDENCE").values())
