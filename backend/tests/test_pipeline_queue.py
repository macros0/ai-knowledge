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
    p.settings = SimpleNamespace(uploads_dir=tmp_path, pipeline_max_workers=1, pipeline_max_pending=1)
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


def test_queue_status_counts_admitted_work_and_frees_capacity(queue_pipeline):
    p = queue_pipeline
    assert p.queue_status() == {
        "processing": 0, "processing_limit": 1,
        "queued": 0, "queue_limit": 1, "available": 2,
    }
    paused_document(p, "first")
    paused_document(p, "second")
    paused_document(p, "third")
    started = threading.Event()
    release_processing = threading.Event()
    def process(*_args, **_kwargs):
        started.set()
        assert release_processing.wait(timeout=10)
    p._process = process
    p._test_release_worker.set()
    try:
        p.resume("first")
        assert started.wait(timeout=10)
        p.resume("second")
        assert p.queue_status() == {
            "processing": 1, "processing_limit": 1,
            "queued": 1, "queue_limit": 1, "available": 0,
        }
        with pytest.raises(DomainError) as exc:
            p.resume("third")
        assert exc.value.code == "queue_overloaded"
    finally:
        tasks = list(p._threads.values())
        release_processing.set()
        for task in tasks:
            task.result(timeout=10)
    assert p.queue_status()["available"] == 2


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


@pytest.fixture
def admin_queue_pipeline(queue_pipeline):
    p = queue_pipeline
    p.settings.pipeline_admin_max_pending = 3
    p._pipeline_slots = threading.BoundedSemaphore(4)
    p._process = lambda *a, **kw: None
    return p


def test_admin_can_admit_work_beyond_regular_limit_and_stops_at_own_limit(admin_queue_pipeline):
    p = admin_queue_pipeline
    for doc_id in ("first", "second", "third", "fourth", "fifth"):
        paused_document(p, doc_id)
    p.resume("first")
    p.resume("second")
    with pytest.raises(DomainError) as exc:
        p.resume("third")
    assert exc.value.code == "queue_overloaded"
    p.resume("third", is_admin=True)
    assert p.queue_status()["available"] == 0
    assert p.queue_status(is_admin=True) == {
        "processing": 0, "processing_limit": 1,
        "queued": 3, "queue_limit": 3, "available": 1,
    }
    p.resume("fourth", is_admin=True)
    before = p.registry.get("fifth")
    with pytest.raises(DomainError) as exc:
        p.resume("fifth", is_admin=True)
    assert exc.value.code == "queue_overloaded"
    assert p.registry.get("fifth") == before


def test_cancel_and_finish_return_admin_capacity(admin_queue_pipeline):
    p = admin_queue_pipeline
    p.vector_store = SimpleNamespace(set_document_deleted=lambda *args: None)
    for doc_id in ("first", "second", "third", "fourth"):
        paused_document(p, doc_id)
        p.resume(doc_id, is_admin=True)
    assert p.queue_status(is_admin=True)["available"] == 0
    p.soft_delete("fourth")
    assert p.queue_status(is_admin=True)["available"] == 1
    p.restore("fourth")
    p.resume("fourth", is_admin=True)
    tasks = list(p._threads.values())
    p._test_release_worker.set()
    for task in tasks:
        task.result(timeout=10)
    assert p.queue_status(is_admin=True)["available"] == 4
    assert p.queue_status()["available"] == 2


@pytest.mark.parametrize("operation", ["upload", "resume", "regenerate"])
@pytest.mark.parametrize("username,expected", [("demo.editor", 503), ("demo.admin", 200)])
def test_document_api_uses_authenticated_role_for_admission(
    admin_queue_pipeline, tmp_path, monkeypatch, operation, username, expected,
):
    from app.api import documents
    from tests.test_authz import make_client, login
    from tests.test_upload_similarity import docx_bytes

    p = admin_queue_pipeline
    client = make_client(tmp_path, monkeypatch, dedup_enabled=False)
    monkeypatch.setattr(documents, "get_pipeline", lambda: p)
    for doc_id in ("first", "second", "third"):
        paused_document(p, doc_id)
    p.resume("first")
    p.resume("second")
    login(client, username)
    # Untrusted query/form flags must not grant the admin admission ceiling.
    if operation == "upload":
        response = client.post("/api/documents?is_admin=true", data={"is_admin": "true"},
                               files={"file": ("new.docx", docx_bytes("queue"))})
    else:
        response = client.post(f"/api/documents/third/{operation}?is_admin=true")
    assert response.status_code == expected, response.text
    if expected == 503:
        assert response.json()["code"] == "queue_overloaded"
        assert len(p._threads) == 2
    else:
        assert response.json()["status"] == "queued"
        assert len(p._threads) == 3
    snapshot = client.get("/api/documents/queue-status")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.headers["cache-control"] == "no-store"
    assert snapshot.json()["queue_limit"] == (3 if username == "demo.admin" else 1)
    assert snapshot.json()["available"] == (1 if username == "demo.admin" else 0)


def test_admin_admits_initial_batch_of_500_with_one_processing_worker(tmp_path, monkeypatch):
    from app.config import Settings

    settings = Settings(_env_file=None, data_dir=tmp_path,
                        pipeline_max_workers=1, pipeline_max_pending=8)
    monkeypatch.setattr("app.services.pipeline.get_settings", lambda: settings)
    for dependency in ("Embedder", "VectorStore", "OKFGenerator"):
        monkeypatch.setattr(f"app.services.pipeline.{dependency}", SimpleNamespace)
    p = Pipeline()
    started = threading.Event()
    release_processing = threading.Event()
    def process(*_args, **_kwargs):
        started.set()
        assert release_processing.wait(timeout=60)
    p._process = process
    # Generation cleanup is outside admission and requires published artifacts.
    p._cleanup_document_generations = lambda *_args: None
    try:
        for index in range(500):
            doc_id = f"initial-{index}"
            p.registry.create(doc_id, "test.docx", "docx", 100)
            p.ingest(doc_id, "test.docx", "test.docx", is_admin=True)
            if index == 0:
                assert started.wait(timeout=10)
        assert p.queue_status(is_admin=True) == {
            "processing": 1, "processing_limit": 1,
            "queued": 499, "queue_limit": 1000, "available": 501,
        }
        assert p.queue_status()["available"] == 0
    finally:
        release_processing.set()
        p._executor.shutdown(wait=True)


def test_concurrent_admin_starts_do_not_exceed_admission_limit(admin_queue_pipeline):
    p = admin_queue_pipeline
    doc_ids = [f"parallel-{index}" for index in range(12)]
    for doc_id in doc_ids:
        paused_document(p, doc_id)
    def submit(doc_id):
        try:
            p.resume(doc_id, is_admin=True)
        except DomainError as exc:
            assert exc.code == "queue_overloaded"
            return False
        return True
    with ThreadPoolExecutor(max_workers=12) as callers:
        results = list(callers.map(submit, doc_ids))
    assert sum(results) == 4
    assert len(p._threads) == 4
    assert p.queue_status(is_admin=True)["available"] == 0
