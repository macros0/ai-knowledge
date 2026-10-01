"""Disposable native-filesystem acceptance; never advances the host clock."""
from datetime import datetime, timedelta, timezone
import io
import json
from uuid import uuid4
from zipfile import ZipFile

import pytest

from app.auth.models import User
from app.services.diagnostics.bundle import BundleTooLarge
from app.services.diagnostics.prepare import prepare_in_child
from app.services.diagnostics.read_view import read_event_page
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits, EventFilter
from app.services.diagnostics.store import DiagnosticStore
from tests.test_authz import login
from tests.test_diagnostics_api import api as shared_api
from tests.test_generation_pipeline import pipeline_env as shared_pipeline, DOC_ID


@pytest.fixture
def api(tmp_path, monkeypatch):
    yield from shared_api.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    yield from shared_pipeline.__wrapped__(tmp_path, monkeypatch)


def test_canonical_generation_retry_only_repeats_failed_chunk(pipeline_env):
    from app.models.schemas import Concept
    from app.services.llm_client import LLMTimeoutError
    from app.db.models import OkfConcept
    from app.db.session import session_scope

    pipeline, source, write_source = pipeline_env
    write_source("Updated synthetic evidence " * 50)
    pipeline.settings.llm_chunk_retry_backoff_seconds = .01
    pipeline.okf_generator.chunk_text = lambda _: ["first synthetic chunk", "second synthetic chunk"]
    calls = []

    def generate(text, filename, index, total, **kwargs):
        calls.append(index)
        if index == 2 and calls.count(2) == 1:
            raise LLMTimeoutError("CANARY_PRIVATE_PROVIDER")
        return [Concept(id=f"chunk-{index}", title=f"Chunk {index}", content=text)]

    pipeline.okf_generator.generate_chunk = generate
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert calls == [1, 2, 2]
    doc = pipeline.registry.get(DOC_ID)
    assert doc["status"] == "done" and doc["problem"] is None
    assert doc["error"] is None and doc["error_code"] is None
    with session_scope() as db:
        concepts = db.query(OkfConcept).filter_by(doc_id=DOC_ID).all()
        assert sorted(row.content for row in concepts) == ["first synthetic chunk", "second synthetic chunk"]


def _event(now, doc_id, session_id=None):
    event = {"schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
             "timestamp_utc": now.isoformat(), "component": "backend", "origin": "server",
             "level": "ERROR", "event_code": "operation_failed", "error_code": "internal_error",
             "doc_id": doc_id}
    if session_id:
        event["diagnostic_session_id"] = session_id
    return encode_event(event)


def test_global_near_quota_narrow_prepare_and_wide_failure(tmp_path):
    limits = DiagnosticLimits(total_bytes=1088 * 1024, backend_bytes=1024 * 1024,
                              frontend_bytes=64 * 1024, baseline_bytes=128 * 1024,
                              session_bytes=128 * 1024, segment_bytes=64 * 1024,
                              bundle_bytes=128 * 1024, min_free_bytes=0)
    now = datetime.now(timezone.utc)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        for _ in range(8):
            stream = str(uuid4())
            block = _event(now, "b" * 16, stream)
            # Each unrelated stream remains within its ordinary 128 KiB budget.
            count = 120 * 1024 // len(block)
            validated = store.validate_event(block)
            assert store.append_batch((validated,) * count, stream=stream).written_events == count
            store.retire_capture(stream, now)
        assert store.append(_event(now, "a" * 16), stream="baseline")
        assert store.used_bytes >= limits.backend_bytes * .90
        for narrow in (True, False):
            job = str(uuid4())
            before_used, before_reserved = store.used_bytes, store.reserved_bytes
            view = store.pin_read_view(EventFilter(doc_id="a" * 16) if narrow else EventFilter(),
                                       now + timedelta(seconds=1))
            try:
                if narrow:
                    result = prepare_in_child(store, view, job)
                    assert result.counts["events"] == 1 and not result.gaps
                else:
                    with pytest.raises(BundleTooLarge):
                        prepare_in_child(store, view, job)
            finally:
                view.release()
                store.delete_tree(store.root / "snapshots" / job)
            assert not store._pins
            assert store.reserved_bytes == before_reserved
            assert store.used_bytes == before_used
            assert not list((store.root / "bundles").iterdir())


def test_accelerated_capture_and_bundle_ttl_with_existing_download(api, monkeypatch):
    client, queue = api
    login(client, "demo.admin")
    runtime = client.app.state.diagnostics
    now = datetime.now(timezone.utc)
    started = client.post("/api/admin/diagnostics/sessions", json={
        "scope": "system", "minutes": 5, "capture_level": "detailed"})
    assert started.status_code == 201
    session_id = started.json()["id"]
    assert queue.store.append(_event(now, "a" * 16, session_id), stream=session_id)
    runtime.sessions.tick(runtime.sessions.utcnow() + timedelta(minutes=6),
                          runtime.sessions.monotonic() + 361)
    assert runtime.sessions.active_for(DiagnosticContext(), "operation_failed") is None
    marker = queue.store.root / "capture-expiry" / (session_id + ".json")
    expiry = datetime.fromtimestamp(json.loads(marker.read_bytes())["expires_at"], timezone.utc)
    view = queue.store.pin_read_view(EventFilter(session_id=session_id), expiry + timedelta(seconds=1))
    try:
        from app.services.diagnostics import read_view

        class FutureDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return (expiry + timedelta(seconds=1)).astimezone(tz)

        with monkeypatch.context() as clocks:
            clocks.setattr(read_view, "datetime", FutureDatetime)
            page = read_event_page(view, offset=0, limit=50)
            assert not page.events and page.counts["expired"] == 1
            response = client.post("/api/admin/diagnostics/events/query", json={"session_id": session_id})
            assert response.status_code == 200 and not response.json()["events"]
        queue.store.sweep(expiry + timedelta(seconds=1))
        assert (queue.store.root / "events" / session_id).exists()
    finally:
        view.release()
    queue.store.sweep(expiry + timedelta(seconds=1))
    assert not (queue.store.root / "events" / session_id).exists()
    assert not marker.exists()
    created = client.post("/api/admin/diagnostics/bundles", json={})
    assert created.status_code == 202
    bundle_id = created.json()["id"]
    queue.start()
    assert queue.wait_idle(15)
    assert client.post(f"/api/admin/diagnostics/bundles/{bundle_id}/preview").status_code == 200
    actor = User(user_id="sim-admin", username="demo.admin", roles=["admin"])
    lease = queue.acquire_download(bundle_id, actor)
    path = queue.store.root / "bundles" / (bundle_id + ".zip")
    assert queue.sweep(now + timedelta(days=2)) == 1
    assert client.get(f"/api/admin/diagnostics/bundles/{bundle_id}/download").status_code == 410
    assert client.post(f"/api/admin/diagnostics/bundles/{bundle_id}/preview").status_code == 410
    assert path.exists()
    with ZipFile(io.BytesIO(b"".join(lease.chunks()))) as archive:
        assert archive.testzip() is None
    assert not path.exists()
    assert not queue.store._pins
