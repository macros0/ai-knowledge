from email.message import EmailMessage
import io
import uuid

import pytest
from openpyxl import Workbook

from tests import test_upload_similarity

base_upload_client = test_upload_similarity.upload_client


@pytest.fixture
def upload_client(base_upload_client, monkeypatch):
    client, settings, started = base_upload_client
    settings.mail_import_enabled = True
    # These tests exercise review/revision semantics as one editor. The lease
    # contract itself is exercised through the unwrapped client in its API suite.
    original_request = client.request
    tokens = {}
    def editing_request(method, url, **kwargs):
        route = str(url).rsplit("/", 1)[0]
        if (method.upper() in {"POST", "PUT", "DELETE"} and route.endswith("/processing-scope")
                and str(url).rsplit("/", 1)[1] in {"review", "confirm", "change-preview", "apply-changes", "apply-exclusions"}):
            if route not in tokens:
                tokens[route] = uuid.uuid4().hex + uuid.uuid4().hex
                original_request("POST", route + "/edit-lock", json={"edit_token": tokens[route]})
            kwargs["json"] = {**kwargs.get("json", {}), "edit_token": tokens[route]}
        return original_request(method, url, **kwargs)
    monkeypatch.setattr(client, "request", editing_request)
    return client, settings, started


def upload_mail(client, rows):
    book = Workbook()
    for _ in range(rows):
        book.active.append(["TABLE_CANARY"])
    data = io.BytesIO()
    book.save(data)
    message = EmailMessage()
    message["Subject"] = "Scope test"
    message.set_content("ROOT EVIDENCE")
    message.add_attachment(
        data.getvalue(), maintype="application", subtype="octet-stream", filename="table.xlsx"
    )
    return client.post(
        "/api/documents", files={"file": ("decision.eml", message.as_bytes(), "message/rfc822")}
    )


def test_heavy_upload_is_durable_and_does_not_start_pipeline(upload_client):
    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    response = upload_mail(client, 600)
    assert response.status_code == 202, response.text
    doc = response.json()
    assert doc["status"] == "awaiting_attachments"
    assert started == []
    state = client.get(f"/api/documents/{doc['id']}/processing-scope")
    assert state.status_code == 200, state.text
    pending = state.json()["pending"]
    assert pending["state"] == "draft"
    assert pending["decisions"][0]["effective_included"] is False


def test_light_upload_keeps_automatic_flow(upload_client):
    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    response = upload_mail(client, 2)
    assert response.status_code == 200, response.text
    assert started == [response.json()["id"]]


def test_scope_capabilities_match_editor_access(upload_client):
    client, settings, _ = upload_client
    settings.attachment_review_enabled = True
    response = upload_mail(client, 600)
    capabilities = client.get(f"/api/documents/{response.json()['id']}/processing-scope").json()["capabilities"]
    assert capabilities["fast_exclusion"] is True
    assert capabilities["cached_restore"] is True
    assert capabilities["incremental_generation"] is True


def test_save_uses_revision_and_confirm_is_idempotent(upload_client, monkeypatch):
    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    from app.api import documents
    import app.services.processing_admission as admission

    monkeypatch.setattr(admission, "get_pipeline", documents.get_pipeline)
    monkeypatch.setattr(admission, "get_settings", lambda: settings)
    doc = upload_mail(client, 600).json()
    route = f"/api/documents/{doc['id']}/processing-scope"
    review = client.get(route).json()["pending"]
    body = {
        "review_id": review["id"],
        "expected_revision": review["revision"],
        "selected_source_keys": [],
    }
    saved = client.put(route + "/review", json=body)
    assert saved.status_code == 200, saved.text
    assert client.put(route + "/review", json=body).status_code == 409
    review = saved.json()["pending"]
    confirm = {
        "review_id": review["id"],
        "revision": review["revision"],
        "request_id": uuid.uuid4().hex,
    }
    response = client.post(route + "/confirm", json=confirm)
    assert response.status_code == 202, response.text
    replay = client.post(route + "/confirm", json=confirm)
    assert replay.status_code == 202, (
        replay.text
    )  # Stub has no durable generation; admission remains leased.
    assert len(started) == 1
    from app.db.models import AuditLog
    from app.db.session import session_scope
    from sqlalchemy import select

    with session_scope() as session:
        actions = list(
            session.scalars(
                select(AuditLog.action_type)
                .where(AuditLog.action_type.like("document_processing_scope_%"))
                .order_by(AuditLog.id)
            )
        )
    assert actions == ["document_processing_scope_updated", "document_processing_scope_confirmed"]


def test_invalid_root_is_rejected_before_review(upload_client):
    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    response = client.post(
        "/api/documents",
        files={"file": ("broken.docx", b"not an office package", "application/octet-stream")},
    )
    assert response.status_code == 422, response.text
    assert not started


@pytest.mark.parametrize("fresh_restart", [False, True])
def test_expired_admission_returns_to_review_without_starting_job(
    upload_client, monkeypatch, fresh_restart
):
    from datetime import datetime, timezone, timedelta
    from app.db.models import DocumentProcessingReview
    from app.db.session import session_scope
    from app.services.processing_scope import recover_processing_reviews

    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    doc = upload_mail(client, 600).json()
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, doc["id"])
        row.state = "submitted"
        row.validation_started_at = datetime.now(timezone.utc) - timedelta(
            hours=0 if fresh_restart else 1
        )
    recover_processing_reviews(settings, force=fresh_restart)
    review = client.get(f"/api/documents/{doc['id']}/processing-scope").json()["pending"]
    assert review["state"] == "draft"
    assert not started


def test_new_document_review_refresh_without_active_scope(upload_client, monkeypatch):
    import app.api.document_processing_scope as scope_api

    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    monkeypatch.setattr(scope_api, "get_settings", lambda: settings)
    doc = upload_mail(client, 600).json()
    route = f"/api/documents/{doc['id']}/processing-scope"
    old = client.get(route).json()["pending"]["id"]
    initial = client.get(route).json()["pending"]
    assert (
        client.post(route + "/review", json={"expected_active_generation_id": None}).status_code
        == 409
    )
    response = client.post(
        route + "/review",
        json={
            "expected_active_generation_id": None,
            "review_id": old,
            "expected_revision": initial["revision"],
        },
    )
    assert response.status_code == 200, response.text
    current = response.json()["pending"]["id"]
    assert old == current and not started
    root = settings.uploads_dir / doc["id"] / "processing-review"
    assert [path.name for path in root.iterdir()] == [current]


def test_reader_can_inspect_but_cannot_change_scope(upload_client):
    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    doc = upload_mail(client, 600).json()
    route = f"/api/documents/{doc['id']}/processing-scope"
    review = client.get(route).json()["pending"]
    settings.auth_sim_users.append(
        {
            "user_id": "scope-reader",
            "username": "scope.reader",
            "email": "reader@example.test",
            "groups": ["KB_Viewer"],
        }
    )
    assert client.post("/api/auth/simulate", json={"username": "scope.reader"}).status_code == 200
    assert client.get(route).status_code == 200
    response = client.put(
        route + "/review",
        json={
            "review_id": review["id"],
            "expected_revision": review["revision"],
            "selected_source_keys": [],
        },
    )
    assert response.status_code == 403, response.text
    assert not started


def test_registry_admits_heavy_document_and_review_in_one_transaction():
    from app.services.registry import DocumentRegistry
    from docparser.attachment_policy import AttachmentInventory, AttachmentNode

    inventory = AttachmentInventory(
        source_file_hash="a" * 64,
        nodes=[AttachmentNode("1" * 64, "root", "one", "heavy.xlsx", "xlsx", assessment="heavy")],
    )
    document = DocumentRegistry().create(
        "0123456789abcdef", "decision.docx", "", 1, processing_inventory=inventory
    )
    assert document["status"] == "awaiting_attachments"
    assert document["processing_review"]["requires_action"] is True


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("state", ["draft", "validating", "submitted"])
def test_cancel_contract_preserves_choice_and_active(upload_client, monkeypatch, published, state):
    import app.api.document_processing_scope as api
    from app.db.models import Document, DocumentProcessingReview, DocumentGenerationState
    from app.db.session import session_scope
    from app.services.generation_store import begin_generation

    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    doc = upload_mail(client, 600).json()
    route = f"/api/documents/{doc['id']}/processing-scope"
    review = client.get(route).json()["pending"]
    key = review["inventory"]["nodes"][0]["source_key"]
    review = client.put(route + "/review", json={"review_id": review["id"],
                        "expected_revision": review["revision"], "selected_source_keys": [key]}).json()["pending"]
    active = None
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, doc["id"])
        row.state = state
        row.similarity_confirmation_json = {"token_hash": "old-consent"}
        if published:
            generation = begin_generation(session, doc["id"])
            generation.phase = "active"
            active = generation.id
            generation_state = session.get(DocumentGenerationState, doc["id"])
            generation_state.active_generation_id = active
            generation_state.candidate_generation_id = None
            session.get(Document, doc["id"]).status = "done"
    response = client.request("DELETE", route + "/review", json={"review_id": review["id"],
                              "expected_revision": review["revision"], "selected_source_keys": []})
    assert response.status_code == (200 if state == "draft" else 409), response.text
    with session_scope() as session:
        row = session.get(DocumentProcessingReview, doc["id"])
        assert row.selection_json == review["decisions"]
        if state == "draft" and not published:
            assert row.state == "draft" and row.revision == review["revision"] + 1
            assert row.similarity_confirmation_json is None
        elif state == "draft":
            assert row.state == "canceled"
        else:
            assert row.state == state and row.revision == review["revision"]
        generation_state = session.get(DocumentGenerationState, doc["id"])
        assert (generation_state.active_generation_id if generation_state else None) == active
    assert started == []


def test_upload_artifacts_exist_before_document_becomes_visible(upload_client, monkeypatch):
    import shutil
    from sqlalchemy import select, func
    from app.db.models import Document
    from app.db.session import session_scope
    from app.api import documents

    client, settings, started = upload_client
    settings.attachment_review_enabled = True
    settings.dedup_enabled = False
    real_copy = shutil.copytree
    real_set_hash = documents.set_file_hash
    observations = []

    def observed_copy(source, target, *args, **kwargs):
        if target.name == "attachments" and "processing-review" in target.parts:
            with session_scope() as session:
                # Independent transaction cannot see a partially prepared upload.
                assert session.scalar(select(func.count()).select_from(Document)) == 0
            observations.append("copy")
        return real_copy(source, target, *args, **kwargs)

    def observed_hash(doc_id, value):
        # This was the old race window: a refresh could create the directory first.
        review = client.get(f"/api/documents/{doc_id}/processing-scope").json()["pending"]
        root = settings.uploads_dir / doc_id / "processing-review" / review["id"] / "attachments"
        assert root.is_dir() and any(root.iterdir())
        observations.append("visible")
        return real_set_hash(doc_id, value)

    monkeypatch.setattr(shutil, "copytree", observed_copy)
    monkeypatch.setattr(documents, "set_file_hash", observed_hash)
    response = upload_mail(client, 600)
    assert response.status_code == 202, response.text
    assert observations == ["copy", "visible"] and not started
