"""Admin diagnostic access is role-checked per action, not per URL possession."""
from types import SimpleNamespace
from pathlib import Path
import io
import threading
import time
import zipfile
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import config
from app.auth.models import User
from app.db.models import AuditLog, DiagnosticBundle
from app.db.session import session_scope
from app.services import audit
from app.services.diagnostics.bundle_queue import BundleQueueError, DownloadLease
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
from app.services.diagnostics.recorder import DiagnosticRecorder
from app.services.diagnostics.sessions import DiagnosticSessionService
from app.services.diagnostics.store import DiagnosticStore
from tests.test_authz import login, make_client


@pytest.fixture
def api(tmp_path, monkeypatch):
    client = make_client(tmp_path / "app", monkeypatch, diagnostics_dir=tmp_path / "spool",
                         diagnostics_capture_enabled=True, diagnostics_bundle_enabled=True,
                         diagnostics_download_enabled=True, diagnostics_min_free_mb=0)
    settings = config.get_settings()
    store = DiagnosticStore(settings.diagnostics_dir, settings.diagnostics_limits())
    store.open()
    sessions = DiagnosticSessionService(store, settings)
    recorder = DiagnosticRecorder(store, capture_selector=sessions.active_for,
                                  on_capture_written=sessions.record_written,
                                  on_capture_failed=sessions.recording_failed)
    recorder.start()
    queue = DiagnosticBundleQueue(store)
    client.app.state.diagnostics = SimpleNamespace(settings=settings, store=store, sessions=sessions,
                                                   recorder=recorder, bundle_queue=queue)
    yield client, queue
    queue.shutdown()
    recorder.stop()
    store.close()


@pytest.mark.parametrize("username,status", [(None, 401), ("demo.user", 403),
                                                ("demo.editor", 403), ("demo.security", 403),
                                                ("demo.admin", 200)])
def test_status_role_matrix(api, username, status):
    client, _ = api
    if username:
        login(client, username)
    response = client.get("/api/admin/diagnostics/status")
    assert response.status_code == status
    if status == 200:
        assert response.json()["capabilities"]["bundle"] is True
        assert "events" not in response.json()


def test_session_and_bundle_lifecycle_with_fresh_role_check(api):
    client, queue = api
    login(client, "demo.admin")
    started = client.post("/api/admin/diagnostics/sessions", json={"scope": "interface", "minutes": 5})
    assert started.status_code == 201, started.text
    assert client.post("/api/admin/diagnostics/sessions", json={"scope": "interface"}).status_code == 409
    assert client.post(f"/api/admin/diagnostics/sessions/{started.json()['id']}/stop").status_code == 200
    created = client.post("/api/admin/diagnostics/bundles", json={})
    assert created.status_code == 202, created.text
    bundle_id = created.json()["id"]
    assert client.post(f"/api/admin/diagnostics/bundles/{bundle_id}/preview").status_code == 409
    queue.start()
    assert queue.wait_idle(10)
    preview = client.post(f"/api/admin/diagnostics/bundles/{bundle_id}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["manifest"]["format_version"] == 1
    with session_scope() as db:
        viewed = db.scalars(select(AuditLog).where(
            AuditLog.action_type == "diagnostic_view", AuditLog.target_id == bundle_id)).one()
        assert viewed.new_value["filters"]["session_id"] is None
        assert viewed.new_value["filters"]["from_utc"]
        assert viewed.new_value["filters"]["to_utc"]
        assert viewed.new_value["event_count"] == preview.json()["manifest"]["counts"]["events"]
    login(client, "demo.security")
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 403
    login(client, "demo.admin")
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download", headers={"Range": "bytes=0-10"}).status_code == 416
    download = client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download")
    assert download.status_code == 200
    assert download.content.startswith(b"PK")
    assert download.headers["cache-control"] == "no-store"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert "attachment; filename=" in download.headers["content-disposition"]
    assert client.delete(f"/api/admin/diagnostics/bundles/{bundle_id}").status_code == 202
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 410


def test_manual_stop_waits_for_accepted_capture_event(api):
    client, _ = api
    login(client, "demo.admin")
    session_id = client.post("/api/admin/diagnostics/sessions",
                             json={"scope": "system", "minutes": 5}).json()["id"]
    diagnostics = client.app.state.diagnostics
    entered, release = threading.Event(), threading.Event()
    original = diagnostics.store.append

    def slow_append(*args, **kwargs):
        if kwargs.get("stream") == session_id:
            entered.set()
            release.wait(3)
        return original(*args, **kwargs)

    diagnostics.store.append = slow_append
    response = []
    try:
        assert diagnostics.recorder.emit("stage_started", fields={"stage": "parse"})
        assert entered.wait(2)
        stopping = threading.Thread(target=lambda: response.append(
            client.post(f"/api/admin/diagnostics/sessions/{session_id}/stop")))
        stopping.start()
        time.sleep(0.05)
        assert stopping.is_alive()
        release.set()
        stopping.join(3)
        assert len(response) == 1 and response[0].status_code == 200
    finally:
        release.set()
    assert diagnostics.recorder.status()["expired_queue"] == 0
    assert list((diagnostics.store.root / "events" / session_id).glob("*.jsonl"))


def test_invalid_ids_and_filters_do_not_reach_storage(api):
    client, _ = api
    login(client, "demo.admin")
    assert client.post("/api/admin/diagnostics/events/query", json={"limit": 101}).status_code == 422
    assert client.post("/api/admin/diagnostics/bundles/not-uuid/preview").status_code == 422
    closed_before = len(client.app.state.diagnostics.recorder._closed_captures)
    assert client.post(f"/api/admin/diagnostics/sessions/{uuid4()}/stop").status_code == 404
    assert len(client.app.state.diagnostics.recorder._closed_captures) == closed_before


def test_query_is_bounded_audited_and_contains_only_safe_events(api):
    client, queue = api
    login(client, "demo.admin")
    now = datetime.now(timezone.utc)
    safe = encode_event({"schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
                         "timestamp_utc": now.isoformat(), "component": "backend", "level": "ERROR",
                         "event_code": "operation_failed", "origin": "server",
                         "error_code": "internal_error"})
    assert queue.store.append(safe, stream="baseline")
    response = client.post("/api/admin/diagnostics/events/query", json={"limit": 1})
    assert response.status_code == 200, response.text
    assert len(response.json()["events"]) == 1
    assert "message" not in response.json()["events"][0]
    with session_scope() as db:
        actions = list(db.scalars(select(AuditLog).where(AuditLog.action_type == "diagnostic_view")))
    assert len(actions) == 1
    assert actions[0].new_value["limit"] == 1
    assert actions[0].new_value["offset"] == 0
    assert actions[0].new_value["returned_count"] == 1


def test_query_audit_records_filters_and_blocks_response_on_failure(api, monkeypatch):
    client, _ = api
    login(client, "demo.admin")
    doc_id = "a" * 16
    response = client.post("/api/admin/diagnostics/events/query", json={"doc_id": doc_id, "limit": 2})
    assert response.status_code == 200
    with session_scope() as db:
        action = db.scalars(select(AuditLog).where(AuditLog.action_type == "diagnostic_view")).one()
        assert action.new_value["doc_id"] == doc_id
        assert action.new_value["returned_count"] == 0

    original = audit.record_in_session

    def reject_view(db, **kwargs):
        if kwargs["action_type"] == "diagnostic_view":
            raise OSError("CANARY_PRIVATE")
        return original(db, **kwargs)

    monkeypatch.setattr(audit, "record_in_session", reject_view)
    failed = client.post("/api/admin/diagnostics/events/query", json={"doc_id": doc_id})
    assert failed.status_code == 503
    assert "CANARY_PRIVATE" not in failed.text


def test_query_reports_partial_results_when_a_segment_is_unreadable(api, monkeypatch):
    client, queue = api
    login(client, "demo.admin")
    now = datetime.now(timezone.utc)
    safe = encode_event({"schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
                         "timestamp_utc": now.isoformat(), "component": "backend", "level": "ERROR",
                         "event_code": "operation_failed", "origin": "server",
                         "error_code": "internal_error"})
    assert queue.store.append(safe, stream="baseline")
    assert queue.store.append(safe, stream=str(uuid4()))
    blocked = next((queue.store.root / "events" / "baseline").glob("*.jsonl"))
    original_open = Path.open

    def deny_one_segment(path, mode="r", *args, **kwargs):
        if path == blocked and mode == "rb":
            raise PermissionError("segment belongs to another owner")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_one_segment)
    response = client.post("/api/admin/diagnostics/events/query", json={"limit": 100})
    assert response.status_code == 200, response.text
    assert response.json()["partial"] is True
    assert response.json()["counts"]["invalid"] >= 1
    assert response.json()["events"]


def test_delete_defers_physical_unlink_until_existing_lease_releases(api):
    client, queue = api
    login(client, "demo.admin")
    bundle_id = client.post("/api/admin/diagnostics/bundles", json={}).json()["id"]
    queue.start()
    assert queue.wait_idle(10)
    actor = User(user_id="sim-admin", username="demo.admin", roles=["admin"])
    lease = queue.acquire_download(bundle_id, actor)
    path = queue.store.root / "bundles" / (bundle_id + ".zip")
    assert path.exists()
    assert client.delete(f"/api/admin/diagnostics/bundles/{bundle_id}").status_code == 202
    assert path.exists()
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 410
    lease.release()
    lease.release()
    assert not path.exists()


def test_http_download_finishes_after_delete_during_stream(api, monkeypatch):
    client, queue = api
    login(client, "demo.admin")
    bundle_id = client.post("/api/admin/diagnostics/bundles", json={}).json()["id"]
    queue.start()
    assert queue.wait_idle(10)
    path = queue.store.root / "bundles" / (bundle_id + ".zip")
    streaming = threading.Event()
    resume = threading.Event()
    original_chunks = DownloadLease.chunks

    def paused_chunks(lease):
        streaming.set()
        assert resume.wait(5), "download stream did not resume"
        yield from original_chunks(lease)

    monkeypatch.setattr(DownloadLease, "chunks", paused_chunks)
    result = {}

    def download():
        try:
            result["response"] = client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download")
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=download, daemon=True)
    worker.start()
    try:
        assert streaming.wait(5), "download did not acquire a lease"
        assert path.exists()
        assert client.delete(f"/api/admin/diagnostics/bundles/{bundle_id}").status_code == 202
        assert path.exists()
        assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 410
    finally:
        resume.set()
        worker.join(10)
    assert not worker.is_alive()
    assert "error" not in result, result.get("error")
    response = result["response"]
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
    assert not path.exists()


def test_expiry_and_failed_audit_block_download(api, monkeypatch):
    client, queue = api
    login(client, "demo.admin")
    bundle_id = client.post("/api/admin/diagnostics/bundles", json={}).json()["id"]
    queue.start()
    assert queue.wait_idle(10)
    original = audit.record_in_session
    def reject_download(db, **kwargs):
        if kwargs["action_type"] == "diagnostic_bundle_download_started":
            raise OSError("CANARY_PRIVATE")
        return original(db, **kwargs)
    monkeypatch.setattr(audit, "record_in_session", reject_download)
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 503
    with session_scope() as db:
        db.get(DiagnosticBundle, bundle_id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 410
    assert client.post(f"/api/admin/diagnostics/bundles/{bundle_id}/preview").status_code == 410


def test_independent_flags_and_rate_errors(api, monkeypatch):
    client, queue = api
    login(client, "demo.admin")
    runtime = client.app.state.diagnostics
    runtime.settings = runtime.settings.model_copy(update={
        "diagnostics_capture_enabled": False, "diagnostics_bundle_enabled": False,
        "diagnostics_download_enabled": False})
    assert client.post("/api/admin/diagnostics/sessions", json={}).status_code == 404
    assert client.post("/api/admin/diagnostics/bundles", json={}).status_code == 404
    assert client.get(f"/api/admin/diagnostics/bundles/{uuid4()}/download").status_code == 404
    runtime.settings = runtime.settings.model_copy(update={"diagnostics_bundle_enabled": True})
    monkeypatch.setattr(queue, "submit", lambda *_: (_ for _ in ()).throw(BundleQueueError("diagnostic_bundle_rate_limited")))
    limited = client.post("/api/admin/diagnostics/bundles", json={})
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"


def test_admin_mutation_requires_csrf_in_cookie_auth_mode(api, monkeypatch):
    client, _ = api
    login(client, "demo.admin")
    from app import main
    settings = main.get_settings().model_copy(update={"auth_provider": "keycloak_oidc"})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    assert client.post("/api/admin/diagnostics/bundles", json={}).status_code == 403
    token = client.cookies.get("csrf_token")
    assert client.post("/api/admin/diagnostics/bundles", json={},
                       headers={"X-CSRF-Token": token}).status_code == 202


def test_expiry_sweep_blocks_new_open_but_finishes_existing_lease(api):
    client, queue = api
    login(client, "demo.admin")
    bundle_id = client.post("/api/admin/diagnostics/bundles", json={}).json()["id"]
    queue.start()
    assert queue.wait_idle(10)
    actor = User(user_id="sim-admin", username="demo.admin", roles=["admin"])
    lease = queue.acquire_download(bundle_id, actor)
    path = queue.store.root / "bundles" / (bundle_id + ".zip")
    after_expiry = datetime.now(timezone.utc) + timedelta(days=2)
    assert queue.sweep(after_expiry) == 1
    assert path.exists()
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 410
    assert b"".join(lease.chunks()).startswith(b"PK")
    assert not path.exists()
