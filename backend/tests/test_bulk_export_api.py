"""HTTP integration contract not covered by the generic jobs tests."""
from __future__ import annotations

from app.db.models import Job
from app.db.session import session_scope
from app.services.export_queue import BULK_EXPORT, ExportAuditUnavailableError
from tests.test_authz import make_client, login


def test_export_job_is_visible_through_shared_jobs_endpoints(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    with session_scope() as session:
        job = Job(
            job_type=BULK_EXPORT,
            status="completed",
            result={"artifact_status": "available", "parts": [{"number": 1}], "expires_at": "2099-01-01T00:00:00+00:00"},
        )
        session.add(job)
        session.flush()
        job_id = job.id

    login(client, "demo.admin")
    listed = client.get("/api/jobs")
    assert listed.status_code == 200
    assert any(item["id"] == job_id and item["job_type"] == BULK_EXPORT for item in listed.json()["jobs"])
    fetched = client.get(f"/api/jobs/{job_id}")
    assert fetched.status_code == 200
    assert fetched.json()["result"]["parts"] == [{"number": 1}]


def test_delete_export_is_fail_closed_when_audit_is_unavailable(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)

    class _Queue:
        @staticmethod
        def delete_artifacts(*_args, **_kwargs):
            raise ExportAuditUnavailableError("audit unavailable", code="bulk_export_audit_unavailable")

    monkeypatch.setattr("app.api.jobs.get_export_queue", lambda: _Queue())
    login(client, "demo.admin")
    response = client.delete("/api/jobs/71/export")

    assert response.status_code == 503
    assert response.json()["code"] == "bulk_export_audit_unavailable"
