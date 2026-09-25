"""Bulk recovery must preserve checkpoints and never restart unrelated documents."""
from types import SimpleNamespace

import pytest

from app import error_codes as codes
from app.services.audit import AuditService
from app.services.job_queue import JobQueue
from app.services.registry import DocumentRegistry, SERVER_RESTARTED_MESSAGE
from tests.test_bulk_ops import client as client, login, make_docs
from tests.test_pipeline_integration import (
    isolated_env as isolated_env,
    TestPartialGenerationRecovery as _RecoverySetup,
)


def test_resume_is_admin_only(client):
    login(client, "demo.user")
    response = client.post("/api/documents/bulk-resume", json={"doc_ids": ["one"]})
    assert response.status_code == 403


def test_resume_queues_unique_selection(client):
    login(client)
    ids = make_docs(2)
    response = client.post("/api/documents/bulk-resume", json={"doc_ids": ids + ids})
    assert response.status_code == 200, response.text
    assert response.json()["job_type"] == "bulk_resume"
    assert response.json()["status"] == "queued"
    assert response.json()["params"]["doc_ids"] == ids


def test_interrupted_preview_excludes_manual_pauses_and_trash(client):
    login(client)
    ids = make_docs(5)
    reg = DocumentRegistry()
    reg.update(ids[0], status="processing")
    reg.update(ids[1], status="paused", error=None)
    reg.update(ids[2], status="done")
    reg.update(ids[3], status="paused", error=SERVER_RESTARTED_MESSAGE, error_code=None)
    from datetime import datetime, timezone
    reg.update(ids[4], status="processing", deleted_at=datetime.now(timezone.utc))
    reg.reset_stale_statuses()
    response = client.get("/api/documents/bulk-resume/interrupted")
    assert response.status_code == 200, response.text
    assert {d["id"] for d in response.json()["documents"]} == {ids[0], ids[3]}


def test_resume_limit_and_empty_selection(client, monkeypatch):
    from app.config import Settings
    monkeypatch.setattr("app.api.documents.get_settings", lambda: Settings(_env_file=None, bulk_resume_max_docs=2))
    login(client)
    assert client.post("/api/documents/bulk-resume", json={"doc_ids": []}).status_code == 400
    assert client.post("/api/documents/bulk-resume", json={"doc_ids": make_docs(3)}).status_code == 400


@pytest.mark.parametrize("operation", ["bulk_resume", "bulk_regenerate"])
def test_worker_skips_ineligible_and_records_failures(operation, monkeypatch):
    ids = make_docs(5)
    reg = DocumentRegistry()
    for doc_id, status in zip(ids, ["paused", "processing", "done", "failed", "paused"]):
        reg.update(doc_id, status=status)
    from datetime import datetime, timezone
    reg.update(ids[4], deleted_at=datetime.now(timezone.utc))
    invoked = []

    def generate(doc_id):
        invoked.append(doc_id)
        reg.update(doc_id, status="paused" if doc_id == ids[3] else "done", error_code=codes.STORAGE_FULL if doc_id == ids[3] else None)

    pipeline = SimpleNamespace(registry=reg, resume=generate, regenerate=generate)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    q = JobQueue(start_worker=False)
    job = q.submit(operation, ids, SimpleNamespace(user_id="admin", username="admin"))
    q._execute(job["id"])
    result = q.get(job["id"])["result"]
    assert ids[1] not in invoked and ids[4] not in invoked
    assert result["processed"] == (1 if operation == "bulk_resume" else 2)
    assert result["errors"][0]["doc_id"] == ids[3]
    assert result["errors"][0]["error_code"] == codes.STORAGE_FULL
    if operation == "bulk_resume":
        assert ids[2] not in invoked
        entries = AuditService().query(action_type="document_bulk_resume")
        assert {e["target_id"] for e in entries} == {ids[0], ids[3]}
    assert {e["doc_id"] for e in result["skipped"]} == ({ids[1], ids[2], ids[4]} if operation == "bulk_resume" else {ids[1], ids[4]})


def test_restart_preserves_completed_document_results(monkeypatch):
    ids = make_docs(2)
    reg = DocumentRegistry()
    for doc_id in ids:
        reg.update(doc_id, status="paused")
    q = JobQueue(start_worker=False)
    job = q.submit("bulk_resume", ids, SimpleNamespace(user_id="admin", username="admin"))

    def resume(doc_id):
        if doc_id == ids[1]:
            assert q.get(job["id"])["result"]["processed"] == 1
            raise SystemExit("simulated server shutdown")
        reg.update(doc_id, status="done")

    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: SimpleNamespace(registry=reg, resume=resume))
    with pytest.raises(SystemExit):
        q._execute(job["id"])
    q.recover_after_restart()
    recovered = q.get(job["id"])
    assert recovered["status"] == "failed"
    assert recovered["result"]["processed"] == 1
    assert recovered["result"]["results"] == [{"doc_id": ids[0], "ok": True}]
    assert recovered["result"]["error_code"] == codes.JOB_INTERRUPTED


def test_resume_rejects_overlapping_pending_jobs(client, monkeypatch):
    q = JobQueue(start_worker=False)
    monkeypatch.setattr("app.api.documents.get_job_queue", lambda: q)
    login(client)
    ids = make_docs(2)
    first = client.post("/api/documents/bulk-resume", json={"doc_ids": ids})
    assert first.status_code == 200
    duplicate = client.post("/api/documents/bulk-resume", json={"doc_ids": ids})
    assert duplicate.status_code == 409
    assert len(q.list()) == 1


def test_interrupted_preview_moves_past_already_queued_batch(client, monkeypatch):
    from app.config import Settings
    settings = Settings(_env_file=None, bulk_resume_max_docs=2)
    monkeypatch.setattr("app.api.documents.get_settings", lambda: settings)
    q = JobQueue(start_worker=False)
    monkeypatch.setattr("app.api.documents.get_job_queue", lambda: q)
    login(client)
    ids = make_docs(3)
    reg = DocumentRegistry()
    for doc_id in ids:
        reg.update(doc_id, status="paused", error_code=codes.SERVER_RESTARTED)
    preview = client.get("/api/documents/bulk-resume/interrupted").json()
    assert preview["requested"] == 3
    assert preview["eligible_doc_ids"] == ids[:2]
    client.post("/api/documents/bulk-resume", json={"doc_ids": ids[:2]})
    remaining = client.get("/api/documents/bulk-resume/interrupted").json()
    assert remaining["requested"] == 1
    assert remaining["eligible_doc_ids"] == ids[2:]


def test_preview_separates_resumable_from_completed_and_active(client):
    login(client)
    ids = make_docs(3)
    reg = DocumentRegistry()
    for doc_id, status in zip(ids, ["paused", "done", "processing"]):
        reg.update(doc_id, status=status)
    preview = client.post("/api/documents/bulk-preview?operation=resume", json={"doc_ids": ids}).json()
    assert preview["eligible_doc_ids"] == ids[:1]
    assert preview["skipped"] == [
        {"doc_id": ids[1], "error_code": codes.NOT_RESUMABLE},
        {"doc_id": ids[2], "error_code": codes.ALREADY_PROCESSING},
    ]


def test_restart_to_bulk_resume_preserves_finished_chunk(client, isolated_env, monkeypatch):
    """Real API -> queue -> Pipeline.resume -> persisted checkpoint -> done."""
    from pathlib import Path
    from app.models.schemas import Concept
    from app.services.staging import StagingStore

    reg, source = isolated_env
    pipeline = _RecoverySetup().setup_pipeline(monkeypatch)
    reg.create("restart-doc", "test.doc", "doc", 100)
    pipeline.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    (pipeline.settings.uploads_dir / "restart-doc.doc").write_bytes(Path(source).read_bytes())
    staging = StagingStore("restart-doc")
    staging.create(2)
    staging.save_chunk_text(0, "first chunk")
    staging.save_chunk_text(1, "second chunk")
    staging.append_chunk(0, [Concept(id="saved", title="Saved", type="concept", content="Already generated")])
    checkpoint = (staging.dir / "chunk_00.json").read_bytes()
    reg.update("restart-doc", status="processing")
    reg.reset_stale_statuses()
    generated = []

    def generate(text, filename, index, total, **kwargs):
        generated.append(index)
        assert (staging.dir / "chunk_00.json").read_bytes() == checkpoint
        return [Concept(id="new", title="New", type="concept", content="Second chunk generated")]

    pipeline.okf_generator.generate_chunk = generate
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    q = JobQueue(start_worker=False)
    monkeypatch.setattr("app.api.documents.get_job_queue", lambda: q)
    login(client)
    preview = client.get("/api/documents/bulk-resume/interrupted").json()
    assert preview["eligible_doc_ids"] == ["restart-doc"]
    response = client.post("/api/documents/bulk-resume", json={"doc_ids": preview["eligible_doc_ids"]})
    assert response.status_code == 200, response.text
    q._execute(response.json()["id"])
    assert q.get(response.json()["id"])["result"]["processed"] == 1
    assert reg.get("restart-doc")["status"] == "done"
    assert generated == [2]


def test_resume_rate_limit_returns_retry_after(client):
    login(client)
    ids = make_docs(4)
    for doc_id in ids[:3]:
        assert client.post("/api/documents/bulk-resume", json={"doc_ids": [doc_id]}).status_code == 200
    response = client.post("/api/documents/bulk-resume", json={"doc_ids": ids[3:]})
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) > 0


def test_concurrent_resume_requests_create_one_job():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.services.errors import ConflictError

    q = JobQueue(start_worker=False)
    barrier = Barrier(2)

    def submit(_):
        barrier.wait(timeout=5)
        try:
            q.submit("bulk_resume", ["same-doc"], SimpleNamespace(user_id="admin", username="admin"))
            return "accepted"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, range(2)))
    assert sorted(outcomes) == ["accepted", "conflict"]
    assert len(q.list()) == 1


def test_audit_failure_prevents_resume(monkeypatch):
    reg = DocumentRegistry()
    doc_id = make_docs(1)[0]
    reg.update(doc_id, status="paused")
    calls = []
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: SimpleNamespace(registry=reg, resume=calls.append))

    def audit_unavailable(*args, **kwargs):
        raise RuntimeError("Audit unavailable")

    monkeypatch.setattr("app.services.job_queue.audit_mod.record", audit_unavailable)
    q = JobQueue(start_worker=False)
    job = q.submit("bulk_resume", [doc_id], SimpleNamespace(user_id="admin", username="admin"))
    q._execute(job["id"])
    assert calls == []
    assert reg.get(doc_id)["status"] == "paused"
    assert q.get(job["id"])["status"] == "failed"
