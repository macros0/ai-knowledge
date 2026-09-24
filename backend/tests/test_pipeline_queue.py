"""Queue admission must be visible before a worker starts processing."""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.services.errors import ConflictError, DomainError
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry


@pytest.fixture
def queue_pipeline(tmp_path):
    p = Pipeline.__new__(Pipeline)
    p.registry = DocumentRegistry()
    p.settings = SimpleNamespace(uploads_dir=tmp_path)
    p._threads = {}
    p._abort_events = {}
    p._start_lock = threading.Lock()
    p._pipeline_slots = threading.BoundedSemaphore(2)
    p._executor = ThreadPoolExecutor(max_workers=1)
    release = threading.Event()
    blocker = p._executor.submit(release.wait)
    p._test_release_worker = release
    yield p
    release.set()
    blocker.result(timeout=10)
    p._executor.shutdown(wait=True)


def paused_document(p, doc_id="resume-me"):
    p.registry.create(doc_id, "test.docx", "docx", 100)
    p.registry.update(doc_id, status="paused", error="Server restarted",
                      error_code="server_restarted", total_chunks=12, processed_chunks=3)
    (p.settings.uploads_dir / f"{doc_id}.docx").write_bytes(b"test")
    return p.registry.get(doc_id)


def test_resume_waiting_for_worker_is_queued_and_preserves_progress(queue_pipeline):
    p = queue_pipeline
    paused_document(p)
    processed = []
    p._process = lambda *a, **kw: processed.append((a[0], kw["resume"]))
    p.resume("resume-me")
    doc = p.registry.get("resume-me")
    assert not processed
    assert doc["status"] == "queued"
    assert doc["error"] is None
    assert doc["error_code"] is None
    assert (doc["processed_chunks"], doc["total_chunks"]) == (3, 12)
    with pytest.raises(ConflictError):
        p.resume("resume-me")
    task = p._threads["resume-me"]
    p._test_release_worker.set()
    task.result(timeout=10)
    assert processed == [("resume-me", True)]
    assert "resume-me" not in p._threads


def test_full_queue_keeps_paused_document_resumable(queue_pipeline):
    p = queue_pipeline
    before = paused_document(p)
    assert p._pipeline_slots.acquire(blocking=False)
    assert p._pipeline_slots.acquire(blocking=False)
    with pytest.raises(DomainError) as exc:
        p.resume("resume-me")
    assert exc.value.code == "queue_overloaded"
    assert p.registry.get("resume-me") == before
    assert not p._threads and not p._abort_events


def test_submit_failure_restores_status_and_releases_capacity(queue_pipeline, monkeypatch):
    p = queue_pipeline
    before = paused_document(p)
    def fail(*args, **kwargs):
        raise RuntimeError("Executor unavailable")
    monkeypatch.setattr(p._executor, "submit", fail)
    with pytest.raises(RuntimeError, match="Executor unavailable"):
        p.resume("resume-me")
    doc = p.registry.get("resume-me")
    for key in ("status", "error", "error_code", "processed_chunks"):
        assert doc[key] == before[key]
    assert not p._threads and not p._abort_events
    assert p._pipeline_slots.acquire(blocking=False)
    assert p._pipeline_slots.acquire(blocking=False)


def test_server_restart_returns_queued_document_to_resumable_state():
    reg = DocumentRegistry()
    reg.create("queued", "test.docx", "docx", 100)
    reg.update("queued", status="queued", total_chunks=12, processed_chunks=3)
    reg.reset_stale_statuses()
    doc = reg.get("queued")
    assert doc["status"] == "paused"
    assert doc["error_code"] == "server_restarted"
    assert doc["processed_chunks"] == 3


def test_delete_and_restore_waiting_document_can_resume_again(queue_pipeline):
    p = queue_pipeline
    p.vector_store = SimpleNamespace(set_document_deleted=lambda *args: None)
    p._process = lambda *a, **kw: None
    paused_document(p)
    p.resume("resume-me")
    p.soft_delete("resume-me")
    assert "resume-me" not in p._threads
    p.restore("resume-me")
    doc = p.registry.get("resume-me")
    assert doc["status"] == "paused"
    assert doc["processed_chunks"] == 3
    p.resume("resume-me")
    assert p.registry.get("resume-me")["status"] == "queued"


def test_resume_api_reports_queue_in_response_and_document_list(queue_pipeline, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from app.api import documents
    from app.config import Settings
    from app.main import create_app

    p = queue_pipeline
    settings = Settings(_env_file=None, data_dir=tmp_path, auth_provider="disabled")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr(documents, "get_pipeline", lambda: p)
    p._process = lambda *a, **kw: None
    paused_document(p)
    client = TestClient(create_app())
    response = client.post("/api/documents/resume-me/resume")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued"
    assert response.json()["error_code"] is None
    response = client.get("/api/documents", params={"status": "queued"})
    assert response.status_code == 200, response.text
    assert [doc["id"] for doc in response.json()["documents"]] == ["resume-me"]
    response = client.post("/api/documents/resume-me/regenerate")
    assert response.status_code == 409
    assert response.json()["code"] == "already_processing"
