"""Only editors/admins may abandon the exact update shown to the user."""
import pytest

from app.db.models import AuditLog
from app.db.session import session_scope
from tests.test_generation_pipeline import DOC_ID, pipeline_env as pipeline_env
from tests.test_my_documents import login, make_client


def _failed_update(pipeline_env, monkeypatch):
    import app.services.generation_publication as publication

    pipeline, source, write = pipeline_env
    write("Unpublished updated decision.")

    def fail(*_args, **_kwargs):
        raise RuntimeError("Injected pre-commit failure")

    monkeypatch.setattr(publication, "publish_generation", fail)
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    return pipeline


@pytest.mark.parametrize("username,status", [
    ("demo.user", 403), ("demo.security", 403), ("demo.editor", 200), ("demo.admin", 200),
])
def test_cancel_update_roles_and_audit(pipeline_env, monkeypatch, tmp_path, username, status):
    pipeline = _failed_update(pipeline_env, monkeypatch)
    monkeypatch.setattr("app.api.documents.get_pipeline", lambda: pipeline)
    client = make_client(tmp_path, monkeypatch)
    login(client, username)
    update_id = pipeline.registry.get(DOC_ID)["update_id"]
    response = client.post(f"/api/documents/{DOC_ID}/cancel-update", json={"update_id": update_id})
    assert response.status_code == status, response.text
    with session_scope() as session:
        entries = session.query(AuditLog).filter_by(action_type="document_update_cancel").all()
        assert len(entries) == (1 if status == 200 else 0)
    assert pipeline.registry.get(DOC_ID)["status"] == ("done" if status == 200 else "failed")
    if status == 200:
        assert response.json()["has_published_version"]
        assert not response.json()["can_cancel_update"]


def test_stale_update_id_cannot_cancel_current_attempt(pipeline_env, monkeypatch, tmp_path):
    pipeline = _failed_update(pipeline_env, monkeypatch)
    monkeypatch.setattr("app.api.documents.get_pipeline", lambda: pipeline)
    client = make_client(tmp_path, monkeypatch)
    login(client, "demo.editor")
    response = client.post(f"/api/documents/{DOC_ID}/cancel-update", json={"update_id": "0" * 32})
    assert response.status_code == 409
    assert response.json()["code"] == "document_update_conflict"
    assert pipeline.registry.get(DOC_ID)["status"] == "failed"


def test_cancel_audit_failure_leaves_update_resumable(pipeline_env, monkeypatch, tmp_path):
    pipeline = _failed_update(pipeline_env, monkeypatch)
    monkeypatch.setattr("app.api.documents.get_pipeline", lambda: pipeline)
    client = make_client(tmp_path, monkeypatch)
    login(client, "demo.editor")

    def unavailable(*_a, **_k):
        raise RuntimeError("Audit unavailable")

    monkeypatch.setattr("app.services.audit.record_in_session", unavailable)
    client.raise_server_exceptions = False
    response = client.post(f"/api/documents/{DOC_ID}/cancel-update", json={
        "update_id": pipeline.registry.get(DOC_ID)["update_id"],
    })
    assert response.status_code == 500
    assert pipeline.registry.get(DOC_ID)["status"] == "failed"
    assert not pipeline.registry.get(DOC_ID)["update_cancelling"]


def test_deleted_document_cannot_cancel_update(pipeline_env, monkeypatch, tmp_path):
    pipeline = _failed_update(pipeline_env, monkeypatch)
    doc = pipeline.registry.get(DOC_ID)
    pipeline.registry.soft_delete(DOC_ID, "demo.editor")
    monkeypatch.setattr("app.api.documents.get_pipeline", lambda: pipeline)
    client = make_client(tmp_path, monkeypatch)
    login(client, "demo.editor")
    response = client.post(f"/api/documents/{DOC_ID}/cancel-update", json={"update_id": doc["update_id"]})
    assert response.status_code == 404
