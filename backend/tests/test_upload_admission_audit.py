"""Lost acknowledgements cannot separate durable admission from its audit."""
import json

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, AuditOutbox, Document, UploadReceipt, WorkItem
from app.db.session import session_scope
from app.services import audit
from tests.test_artifact_upload_commit import uploads as uploads, upload
from tests.test_work_document_admission import future_api


@pytest.mark.parametrize("destination", ["db", "external", "both"])
def test_api_death_after_admission_commit_retains_exactly_one_upload_audit(uploads, monkeypatch, destination):
    from app.api import documents
    client, settings, _ = future_api(uploads)
    settings.audit_db_enabled = destination != "external"
    settings.audit_stdout_mode = "disabled" if destination == "db" else "durable"
    settings.audit_stdout_format = "native"
    create = documents._registry.create

    def admit_then_lose_api(*args, **kwargs):
        create(*args, **kwargs)
        raise RuntimeError("API stopped immediately after acknowledged admission commit")

    monkeypatch.setattr(documents._registry, "create", admit_then_lose_api)
    assert upload(client).status_code == 500
    monkeypatch.setattr(documents._registry, "create", create)
    replay = upload(client)
    assert replay.status_code == 200
    with session_scope() as session:
        document = session.scalars(select(Document)).one()
        assert document.id == replay.json()["id"]
        assert len(session.scalars(select(WorkItem)).all()) == len(session.scalars(select(UploadReceipt)).all()) == 1
        logs = session.scalars(select(AuditLog).where(AuditLog.action_type == audit.DOCUMENT_UPLOAD)).all()
        outbox = [json.loads(value) for value in session.scalars(select(AuditOutbox.payload))
                  if json.loads(value)["action"] == audit.DOCUMENT_UPLOAD]
        assert len(logs) == (destination != "external")
        assert len(outbox) == (destination != "db")
        if logs:
            assert logs[0].target_id == document.id
            assert logs[0].username == "demo.editor"
            assert logs[0].new_value == {"filename": "a.pdf", "size": len(b"%PDF-1.4 source bytes")}
        if outbox:
            assert outbox[0]["target"]["id"] == document.id
            if logs:
                assert outbox[0]["event_id"] == logs[0].event_id


def test_upload_audit_failure_rolls_back_document_receipt_and_work(uploads, monkeypatch):
    client, _, _ = future_api(uploads)
    record = audit.record_in_session

    def unavailable(session, **kwargs):
        if kwargs["action_type"] == audit.DOCUMENT_UPLOAD:
            raise RuntimeError("Durable upload audit unavailable")
        return record(session, **kwargs)

    monkeypatch.setattr(audit, "record_in_session", unavailable)
    assert upload(client).status_code == 500
    with session_scope() as session:
        assert not session.scalars(select(Document)).all()
        assert not session.scalars(select(WorkItem)).all()
        assert not session.scalars(select(UploadReceipt)).all()
