"""Mail headers shown in document cards come from the canonical root source."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import DocumentSource
from app.db.session import session_scope
from app.main import create_app
from app.services.registry import DocumentRegistry


def client_with_source(tmp_path, monkeypatch, metadata, *, kind="document", source_id="root"):
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
    for module in ("app.config", "app.main", "app.api.documents"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    registry = DocumentRegistry()
    registry.create("aaaaaaaaaaaaaaaa", "reply.msg", "application/vnd.ms-outlook", 224500)
    with session_scope() as session:
        session.add(DocumentSource(
            doc_id="aaaaaaaaaaaaaaaa", source_id=source_id, kind=kind,
            display_name="reply.msg", metadata_json=metadata,
        ))
    return TestClient(create_app())


def test_list_and_detail_preserve_original_reply_date(tmp_path, monkeypatch):
    client = client_with_source(tmp_path, monkeypatch, {
        "mail": True, "sender": "sender@example.test",
        "sent_at": "2023-09-26T18:55:58+00:00",
        "date_raw": "Tue, 26 Sep 2023 21:55:58 +0300",
        "private_header": "not a display field",
    })
    for url in ("/api/documents", "/api/documents/aaaaaaaaaaaaaaaa"):
        response = client.get(url)
        assert response.status_code == 200, response.text
        doc = response.json()["documents"][0] if url == "/api/documents" else response.json()
        assert doc["mail"] == {
            "sender": "sender@example.test", "sent_at": "2023-09-26T18:55:58Z",
        }
        assert doc["created_at"] != doc["mail"]["sent_at"]


@pytest.mark.parametrize("sent_at", [None, "invalid", "2023-09-26T21:55:58", 42])
def test_unknown_mail_date_never_falls_back_to_upload_date(tmp_path, monkeypatch, sent_at):
    client = client_with_source(tmp_path, monkeypatch, {"sent_at": sent_at}, kind="mail")
    response = client.get("/api/documents/aaaaaaaaaaaaaaaa")
    assert response.status_code == 200, response.text
    assert response.json()["mail"] == {"sender": None, "sent_at": None}


@pytest.mark.parametrize("source_id,kind,metadata", [
    ("root/0", "mail", {"sent_at": "2023-09-26T18:55:58Z"}),
    ("root", "document", {"sender": "author", "sent_at": "2023-09-26T18:55:58Z"}),
])
def test_non_mail_document_does_not_inherit_an_attachment_date(tmp_path, monkeypatch, source_id, kind, metadata):
    client = client_with_source(tmp_path, monkeypatch, metadata, kind=kind, source_id=source_id)
    response = client.get("/api/documents/aaaaaaaaaaaaaaaa")
    assert response.status_code == 200, response.text
    assert response.json()["mail"] is None
