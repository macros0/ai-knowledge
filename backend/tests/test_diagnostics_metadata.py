"""Operation metadata in a document-session bundle stays within that document."""
from uuid import UUID

from app.db.models import Document, Job
from app.db.session import session_scope
from app.models.diagnostics import BundleRequest
from app.services.diagnostics.snapshot import _metadata, _safe_operations
from tests.test_diagnostics_sessions import actor, service as _service_fixture


service = _service_fixture


def test_document_session_metadata_selects_target_without_unrelated_operations(service):
    target = "f" * 16
    with session_scope() as db:
        for number in range(51):
            db.add(Document(id=f"{number:016x}", filename=f"sample-{number}.pdf"))
        db.add(Document(id=target, filename="target.pdf", status="processing"))
        db.add(Job(job_type="test", status="running"))
    session = service.start("document", 5, actor(), doc_id=target)
    _, operations = _metadata(BundleRequest(session_id=UUID(session.id)))
    assert operations == [{"kind": "document", "id": target, "status": "processing"}]


def test_numeric_job_id_survives_operation_metadata_validation():
    assert _safe_operations([{"kind": "job", "id": "1", "status": "running"}]) == [
        {"kind": "job", "id": "1", "status": "running"}]
