"""Root mail validation is mandatory independently of duplicate detection."""
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope
from app.services.tag_registry import TagRegistry
from tests.test_upload_development import login, make_client


FIXTURES = Path(__file__).parents[2] / "doc-parser/tests/fixtures/mail"


def _client(tmp_path, monkeypatch, *, dedup_enabled):
    from app.api import documents
    from app.services import deduplication

    client = make_client(tmp_path, monkeypatch, dedup_enabled=dedup_enabled,
                         mail_import_enabled=True, parser_supervisor_enabled=False)
    settings = documents.get_settings()
    monkeypatch.setattr(deduplication, "get_settings", lambda: settings)
    started = []
    monkeypatch.setattr(documents, "get_pipeline", lambda: SimpleNamespace(
        ingest=lambda doc_id, *args, **kwargs: started.append(doc_id)))
    login(client)
    return client, settings, started


@pytest.mark.parametrize("dedup_enabled", [False, True])
@pytest.mark.parametrize("name,payload,code", [
    ("bad.eml", b"Subject: not a valid message\n\nopaque", "mail_parse_failed"),
    ("bad.msg", b"not a compound file", "mail_parse_failed"),
    ("rtf.msg", None, "mail_rtf_unsupported"),
    ("task.msg", None, "mail_object_unsupported"),
    ("encrypted.eml", b"From: sender@example.test\nContent-Type: application/pkcs7-mime\n\nopaque", "mail_protected"),
])
def test_unreadable_root_mail_is_rejected_before_any_admission(tmp_path, monkeypatch, dedup_enabled, name, payload, code):
    client, settings, started = _client(tmp_path, monkeypatch, dedup_enabled=dedup_enabled)
    if payload is None:
        if name == "task.msg":
            payload = (FIXTURES / "synthetic-unicode-attachment.msg").read_bytes()
            old_class = "IPM.Note\0".encode("utf-16-le")
            assert payload.count(old_class) == 1
            # Same-width property replacement preserves the valid CFB layout.
            payload = payload.replace(old_class, "IPM.Task\0".encode("utf-16-le"))
        else:
            payload = (FIXTURES / "synthetic-rtf-only-compressed.msg").read_bytes()
    response = client.post("/api/documents", files={"file": (name, payload)}, data={"tags": "not-admitted"})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    with session_scope() as session:
        assert not session.scalars(select(Document)).all()
    assert not list(settings.uploads_dir.iterdir())
    assert not started
    assert "not-admitted" not in {tag["name"] for tag in TagRegistry().all()}


@pytest.mark.parametrize("dedup_enabled", [False, True])
def test_unsupported_embedded_mail_does_not_reject_readable_parent(tmp_path, monkeypatch, dedup_enabled):
    client, _settings, started = _client(tmp_path, monkeypatch, dedup_enabled=dedup_enabled)
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content("Readable parent decision: allowance is twelve days.")
    message.add_attachment((FIXTURES / "synthetic-rtf-only-compressed.msg").read_bytes(),
                           maintype="application", subtype="vnd.ms-outlook", filename="rtf.msg")
    response = client.post("/api/documents", files={"file": ("parent.eml", message.as_bytes())})
    assert response.status_code == 200, response.text
    assert started == [response.json()["id"]]


@pytest.mark.parametrize("failure,code", [("timeout", "parser_timeout"), ("memory", "parser_resource_limit")])
def test_parser_resource_failure_has_specific_code_before_admission(tmp_path, monkeypatch, failure, code):
    from app.api import documents
    from app.services.parser_supervisor import ParserMemoryLimitError, ParserTimeoutError

    client, settings, started = _client(tmp_path, monkeypatch, dedup_enabled=False)
    settings.parser_supervisor_enabled = True

    def fail(*args, **kwargs):
        raise (ParserTimeoutError if failure == "timeout" else ParserMemoryLimitError)("test limit")

    monkeypatch.setattr(documents, "parse_document_supervised", fail)
    response = client.post("/api/documents", files={"file": ("valid.eml", b"From: sender@example.test\n\nBody")})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == code
    assert not list(settings.uploads_dir.iterdir())
    assert not started


def test_validation_does_not_enable_similarity_when_dedup_is_disabled(tmp_path, monkeypatch):
    from app.api import documents

    client, _settings, started = _client(tmp_path, monkeypatch, dedup_enabled=False)

    def unexpected_similarity(*args, **kwargs):
        raise AssertionError("mail validation must not enable disabled duplicate detection")

    monkeypatch.setattr(documents, "find_duplicates_for_text", unexpected_similarity)
    response = client.post("/api/documents", files={"file": ("valid.eml", b"From: sender@example.test\n\nBody")})
    assert response.status_code == 200, response.text
    assert started == [response.json()["id"]]


def test_supervised_root_warning_rejects_upload_without_publishing(tmp_path, monkeypatch):
    client, settings, started = _client(tmp_path, monkeypatch, dedup_enabled=False)
    settings.parser_supervisor_enabled = True
    response = client.post("/api/documents", files={"file": (
        "rtf.msg", (FIXTURES / "synthetic-rtf-only-compressed.msg").read_bytes(),
    )})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "mail_rtf_unsupported"
    assert not list(settings.uploads_dir.iterdir())
    assert not started


def test_os_protection_failure_is_service_unavailable_before_admission(tmp_path, monkeypatch):
    from app.api import documents
    from app.services.parser_supervisor import ParserIsolationError

    client, settings, started = _client(tmp_path, monkeypatch, dedup_enabled=False)
    settings.parser_supervisor_enabled = True

    def refuse(*args, **kwargs):
        raise ParserIsolationError("OS protection unavailable")

    monkeypatch.setattr(documents, "parse_document_supervised", refuse)
    response = client.post("/api/documents", files={"file": ("valid.eml", b"From: sender@example.test\n\nBody")})
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "parser_isolation_unavailable"
    assert not list(settings.uploads_dir.iterdir())
    assert not started
    with session_scope() as session:
        assert not session.scalars(select(Document)).all()
