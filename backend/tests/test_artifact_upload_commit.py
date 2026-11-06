"""Durable upload admission: replay, rollback and actor isolation."""
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db.models import Document
from app.db.session import session_scope
from tests.test_upload_development import login, make_client

KEY = "upload-attempt-0123456789"
PAYLOAD = b"%PDF-1.4 source bytes"


def test_portable_upload_preview_cannot_enable_production_before_retention():
    from app.config import Settings
    with pytest.raises(ValueError, match="Artifact upload preview"):
        Settings(_env_file=None, environment="production", artifact_uploads_enabled=True)


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    from app.api import documents
    client = make_client(tmp_path, monkeypatch, artifact_uploads_enabled=True)
    started = []
    monkeypatch.setattr(documents, "get_pipeline", lambda: SimpleNamespace(
        ingest=lambda doc_id, *args, **kwargs: started.append(doc_id)))
    login(client)
    return client, documents.get_settings(), started


def upload(client, *, key=KEY, payload=PAYLOAD, **data):
    return client.post("/api/documents", headers={"Idempotency-Key": key} if key else {},
                       files={"file": ("a.pdf", payload, "application/pdf")}, data=data)


def test_retry_returns_same_document(uploads):
    client, settings, started = uploads
    first = upload(client)
    second = upload(client)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    from app.db.models import ArtifactBinding, ArtifactObject, UploadReceipt
    with session_scope() as session:
        assert len(session.scalars(select(Document)).all()) == 1
        assert len(session.scalars(select(ArtifactObject)).all()) == 1
        assert len(session.scalars(select(ArtifactBinding)).all()) == 1
        assert len(session.scalars(select(UploadReceipt)).all()) == 1
    assert started == [first.json()["id"]]
    assert len(list(settings.uploads_dir.glob("*.pdf"))) == 1


@pytest.mark.parametrize("change", [{"payload": b"changed"}, {"tags": "another"}, {"allow_similar": "true"}])
def test_conflicting_key_returns_409(uploads, change):
    client, _, started = uploads
    assert upload(client).status_code == 200
    conflict = upload(client, **change)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "upload_idempotency_conflict"
    assert len(started) == 1


def test_commit_failure_has_no_success_ack(uploads):
    client, settings, started = uploads
    def fail_commit(session):
        if session.info.get("artifact_upload"):
            raise RuntimeError("Injected commit failure")
    event.listen(Session, "before_commit", fail_commit)
    try:
        assert upload(client).status_code == 500
    finally:
        event.remove(Session, "before_commit", fail_commit)
    from app.db.models import ArtifactBinding, UploadReceipt
    with session_scope() as session:
        assert not session.scalars(select(Document)).all()
        assert not session.scalars(select(ArtifactBinding)).all()
        assert not session.scalars(select(UploadReceipt)).all()
    assert not started
    assert not list(settings.uploads_dir.glob("*.pdf"))


def test_old_client_without_key_still_uploads(uploads):
    client, _, started = uploads
    assert upload(client, key=None).status_code == 200
    assert len(started) == 1


def test_queue_failure_keeps_committed_receipt_for_retry(uploads, monkeypatch):
    from app.api import documents
    from app.services.errors import DomainError
    client, _, started = uploads
    def reject(*args, **kwargs):
        raise DomainError("busy", code="queue_overloaded")
    monkeypatch.setattr(documents, "get_pipeline", lambda: SimpleNamespace(ingest=reject))
    assert upload(client).status_code == 503
    response = upload(client)
    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    assert not started


def test_concurrent_retries_admit_once(uploads):
    from concurrent.futures import ThreadPoolExecutor
    client, _, started = uploads
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: upload(client), range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert len(started) == 1


def test_replay_of_deleted_document_cannot_restore_it(uploads):
    client, _, started = uploads
    doc_id = upload(client).json()["id"]
    from app.services.registry import get_registry
    get_registry().soft_delete(doc_id, "demo.editor")
    response = upload(client)
    assert response.status_code == 410
    assert len(started) == 1


def test_receipt_is_scoped_to_stable_actor(uploads):
    client, settings, started = uploads
    first = upload(client).json()["id"]
    settings.auth_sim_users.append({"user_id": "another", "username": "other", "groups": ["KB_Editor"]})
    assert client.post("/api/auth/simulate", json={"username": "other"}).status_code == 200
    second = upload(client)
    assert second.status_code == 200
    assert second.json()["id"] != first
    assert len(started) == 2
