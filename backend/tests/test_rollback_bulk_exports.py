"""Safety contract for the guarded bulk-export rollback CLI."""
from __future__ import annotations

import json

from app.config import Settings
from app.db.models import Job
from app.db.session import session_scope
from app.services import audit as audit_mod
from app.services.export_queue import BULK_EXPORT, ExportQueue
from scripts import rollback_bulk_exports


def _settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, bulk_export_min_free_mb=0)


def _job(status: str, result: dict | None = None) -> int:
    with session_scope() as session:
        job = Job(job_type=BULK_EXPORT, status=status, created_by_id="admin", result=result or {})
        session.add(job)
        session.flush()
        return job.id


def test_rollback_is_dry_run_by_default(tmp_path, monkeypatch, capsys):
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    queue = ExportQueue(start_worker=False)
    monkeypatch.setattr(rollback_bulk_exports, "get_export_queue", lambda: queue)
    job_id = _job("queued")

    assert rollback_bulk_exports.main([]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["job_ids"] == [job_id]
    assert queue.get(job_id)["status"] == "queued"


def test_apply_requires_exact_confirmation(capsys):
    assert rollback_bulk_exports.main(["--apply", "--confirm", "yes"]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_confirmation"


def test_apply_refuses_active_download_lease(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    queue = ExportQueue(start_worker=False)
    job_id = _job("queued")
    queue._leases[(job_id, 1)] = 1

    result = queue.apply_rollback(queue.plan_rollback(include_ready=False), actor=object())

    assert result.skipped_leases == (job_id,)
    assert result.failed_jobs == 0
    assert queue.get(job_id)["status"] == "queued"


def test_apply_fails_queued_job_and_preserves_unrelated_job(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    queue = ExportQueue(start_worker=False)
    target = _job("queued")
    unrelated = _job("queued")
    with session_scope() as session:
        session.get(Job, unrelated).job_type = "bulk_delete"

    result = queue.apply_rollback(queue.plan_rollback(include_ready=False), actor=object())

    assert result.failed_jobs == 1
    assert queue.get(target)["status"] == "failed"
    with session_scope() as session:
        assert session.get(Job, unrelated).status == "queued"


def test_apply_include_ready_deletes_artifact_audits_and_is_idempotent(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    queue = ExportQueue(start_worker=False)
    job_id = _job("completed", {"artifact_status": "available", "parts": [{"number": 1}]})
    ready = settings.exports_dir / f"{job_id}.ready"
    ready.mkdir(parents=True)
    (ready / "part.zip").write_bytes(b"artifact")

    first = queue.apply_rollback(queue.plan_rollback(include_ready=True), actor=object())

    assert first.deleted_ready == 1
    assert first.deleted_bytes == len(b"artifact")
    assert not ready.exists()
    assert queue.get(job_id)["result"]["artifact_status"] == "deleted"
    assert [entry["action_type"] for entry in audit_mod.get_audit().query(target_id=str(job_id))] == [
        audit_mod.DOCUMENT_BULK_EXPORT_DELETED
    ]
    second = queue.apply_rollback(queue.plan_rollback(include_ready=True), actor=object())
    assert second.deleted_ready == 0
    assert second.errors == ()


def test_apply_restores_ready_artifact_when_audit_transaction_fails(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    queue = ExportQueue(start_worker=False)
    job_id = _job("completed", {"artifact_status": "available"})
    ready = settings.exports_dir / f"{job_id}.ready"
    ready.mkdir(parents=True)
    (ready / "part.zip").write_bytes(b"artifact")
    monkeypatch.setattr(
        "app.services.export_queue.audit_mod.record_in_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit down")),
    )

    result = queue.apply_rollback(queue.plan_rollback(include_ready=True), actor=object())

    assert result.errors and result.errors[0].startswith("audit_or_database_failure:")
    assert ready.is_dir()
    assert queue.get(job_id)["result"]["artifact_status"] == "available"


def test_apply_retires_running_job_and_building_artifact(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.services.export_queue.get_settings", lambda: settings)
    queue = ExportQueue(start_worker=False)
    job_id = _job("running", {"artifact_status": "building"})
    building = settings.exports_dir / f"{job_id}.building"
    building.mkdir(parents=True)
    (building / "partial.zip").write_bytes(b"partial")

    result = queue.apply_rollback(queue.plan_rollback(include_ready=False), actor=object())

    assert result.failed_jobs == 1
    assert not building.exists()
    assert queue.get(job_id)["status"] == "failed"
