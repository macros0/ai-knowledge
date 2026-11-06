"""Durable diagnostic segments survive API disk replacement and share quotas."""
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.db.session import session_scope
from app.services.diagnostics.ingest import DiagnosticBatch, ingest_events
from app.services.diagnostics.schema import EventFilter
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.replica_control import ReplicaDiagnosticSessionService
from tests.test_diagnostics_lifecycle import settings as diagnostic_settings
from tests.test_diagnostics_sessions import actor
from tests.test_diagnostics_store import encoded, limits
from tests.test_internal_diagnostics import event as frontend_event


@pytest.fixture
def stores(tmp_path, monkeypatch):
    from app.services.diagnostics.replica_store import ReplicaDiagnosticStore
    settings = diagnostic_settings(tmp_path)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    bounded = limits(total_bytes=270336, backend_bytes=262144, bundle_bytes=131072, session_bytes=1024)
    monkeypatch.setattr(type(settings), "diagnostics_limits", lambda _self: bounded)
    first = ReplicaDiagnosticStore(settings, instance_id=str(uuid4()), limits=bounded)
    second = ReplicaDiagnosticStore(settings, instance_id=str(uuid4()), limits=bounded)
    first.open()
    second.open()
    try:
        yield first, second, settings
    finally:
        first.close()
        second.close()


def _read(store):
    view = store.pin_read_view(EventFilter(), datetime.now(timezone.utc))
    try:
        return store.read_event_page(view, offset=0, limit=100)
    finally:
        view.release()


def test_backend_segment_readable_from_a_cold_replica_with_private_scratch(stores):
    first, second, _ = stores
    value = encoded()
    assert first.root != second.root
    assert first.append(value, stream="baseline")
    first.close()
    page = _read(second)
    assert [item["event_id"] for item in page.events] == [json.loads(value)["event_id"]]
    assert not page.partial
    assert not list((second.root / "inputs").glob("*"))


def test_frontend_receipt_clears_payload_only_after_durable_segment(stores):
    from app.db.models import DiagnosticIngestBatch, DiagnosticSegment
    first, second, _ = stores
    batch = DiagnosticBatch(instance_id=str(uuid4()), batch_id=str(uuid4()), events=[frontend_event()])
    assert ingest_events(batch, store=first).accepted == 1
    with session_scope() as session:
        row = session.get(DiagnosticIngestBatch, (batch.instance_id, batch.batch_id))
        assert row.delivered and row.payload == []
        assert session.scalar(select(DiagnosticSegment)) is not None
    first.close()
    page = _read(second)
    assert [item["event_id"] for item in page.events] == [batch.events[0]["event_id"]]
    assert ingest_events(batch, store=second).duplicate
    assert len(_read(second).events) == 1


def test_capture_byte_budget_is_shared_between_replica_writers(stores):
    from app.db.models import DiagnosticSession
    first, second, settings = stores
    control = ReplicaDiagnosticSessionService(first, settings)
    capture = control.start("system", 5, actor())
    def value():
        event = json.loads(encoded())
        event["diagnostic_session_id"] = str(capture.id)
        return encode_event(event)
    accepted = 0
    for _ in range(10):
        if not first.append(value(), stream=str(capture.id)):
            break
        accepted += 1
    assert accepted > 0
    assert not second.append(value(), stream=str(capture.id))
    with session_scope() as session:
        assert session.get(DiagnosticSession, str(capture.id)).bytes_written <= first.limits.session_bytes


def test_stopped_capture_cannot_publish_from_a_stale_replica(stores):
    first, second, settings = stores
    control = ReplicaDiagnosticSessionService(first, settings)
    capture = control.start("system", 5, actor())
    event = json.loads(encoded())
    event["diagnostic_session_id"] = str(capture.id)
    control.stop(capture.id, actor())
    assert not second.append(encode_event(event), stream=str(capture.id))
    assert not _read(second).events


def test_changed_segment_is_reported_as_a_coverage_gap(stores):
    from app.db.models import DiagnosticSegment
    first, second, _ = stores
    assert first.append(encoded(), stream="baseline")
    with session_scope() as session:
        session.scalar(select(DiagnosticSegment)).payload = "changed"
    page = _read(second)
    assert not page.events and page.partial and "segment_unavailable" in page.gaps


def test_prepare_child_reads_durable_backend_and_frontend_from_private_inputs(stores):
    from app.models.diagnostics import BundleRequest
    from app.services.diagnostics.snapshot import collect_prepared_snapshot
    first, second, _ = stores
    backend = encoded()
    frontend = frontend_event()
    assert first.append(backend, stream="baseline")
    ingest_events(DiagnosticBatch(instance_id=str(uuid4()), batch_id=str(uuid4()), events=[frontend]), store=first)
    first.close()
    snapshot = collect_prepared_snapshot(BundleRequest(), cutoff_at=datetime.now(timezone.utc), store=second,
        job_id=str(uuid4()), metadata_provider=lambda _r: ({"status": "unknown", "dependencies": {}}, [], []))
    try:
        events = [json.loads(line) for path in snapshot.paths for line in path.read_bytes().splitlines()]
        assert {item["event_id"] for item in events} == {json.loads(backend)["event_id"], frontend["event_id"]}
    finally:
        snapshot.release()
    assert not list((second.root / "inputs").glob("*"))


def test_frontend_publication_and_receipt_marker_roll_back_together(stores):
    from app.db.models import DiagnosticIngestBatch, DiagnosticSegment
    from app.services.diagnostics.ingest import drain_ingest
    first, second, _ = stores
    batch = DiagnosticBatch(instance_id=str(uuid4()), batch_id=str(uuid4()), events=[frontend_event()])

    def fail_marker(_mapper, _connection, row):
        if row.delivered:
            raise OSError("receipt marker unavailable")

    event.listen(DiagnosticIngestBatch, "before_update", fail_marker)
    try:
        assert ingest_events(batch, store=first).accepted == 1
    finally:
        event.remove(DiagnosticIngestBatch, "before_update", fail_marker)
    with session_scope() as session:
        row = session.get(DiagnosticIngestBatch, (batch.instance_id, batch.batch_id))
        assert not row.delivered and row.payload == batch.events
        assert session.scalar(select(DiagnosticSegment)) is None
        row.next_attempt_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert drain_ingest(second)["delivered"] == 1
    assert len(_read(second).events) == 1


def test_admitted_frontend_capture_batch_can_finish_after_stop(stores, monkeypatch):
    from app.db.models import DiagnosticIngestBatch
    from app.services.diagnostics.ingest import drain_ingest
    first, second, settings = stores
    control = ReplicaDiagnosticSessionService(first, settings)
    capture = control.start("interface", 5, actor())
    item = frontend_event()
    item["diagnostic_session_id"] = str(capture.id)
    batch = DiagnosticBatch(instance_id=str(uuid4()), batch_id=str(uuid4()), events=[item])
    original = first.publish_remote
    monkeypatch.setattr(first, "publish_remote", lambda *_a, **_k: (_ for _ in ()).throw(OSError("deferred")))
    assert ingest_events(batch, store=first).accepted == 1
    control.stop(capture.id, actor())
    monkeypatch.setattr(first, "publish_remote", original)
    with session_scope() as session:
        session.get(DiagnosticIngestBatch, (batch.instance_id, batch.batch_id)).next_attempt_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
    assert drain_ingest(second)["delivered"] == 1
    assert len(_read(second).events) == 1


def test_shared_retention_removes_expired_segments_and_old_unused_metadata(stores):
    from app.db.models import DiagnosticSegment, DiagnosticInstance
    first, second, _ = stores
    assert first.append(encoded(), stream="baseline")
    with session_scope() as session:
        session.scalar(select(DiagnosticSegment)).expires_at = datetime.now(timezone.utc) - timedelta(days=8)
        session.get(DiagnosticInstance, first.instance_id).heartbeat_at = datetime.now(timezone.utc) - timedelta(days=8)
    result = second.maintain()
    assert result["segments"] == 1 and result["instances"] == 1
    assert not _read(second).events


def test_instance_loss_and_counters_are_visible_without_exporting_arbitrary_fields(stores):
    from app.db.models import DiagnosticInstance
    first, second, _ = stores
    first.heartbeat({"dropped": 3, "secret": "CANARY"})
    with session_scope() as session:
        row = session.get(DiagnosticInstance, first.instance_id)
        assert row.counters == {"dropped": 3}
        row.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    counts, gaps = second.coverage(datetime.now(timezone.utc) - timedelta(minutes=2))
    assert counts["recorder_dropped"] == 3
    assert "recorder_loss" in gaps


def test_maintenance_worker_recovers_deferred_inbox_without_source_disk(stores, monkeypatch):
    from app.db.models import DiagnosticIngestBatch
    from app.services.work.diagnostics import maintain_diagnostics
    from app.services.work.models import WorkSpec
    from app.services.work.store import claim, enqueue
    first, second, _ = stores
    monkeypatch.setattr(first, "publish_remote", lambda *_a, **_k: (_ for _ in ()).throw(OSError("deferred")))
    batch = DiagnosticBatch(instance_id=str(uuid4()), batch_id=str(uuid4()), events=[frontend_event()])
    assert ingest_events(batch, store=first).accepted == 1
    first.close()
    with session_scope() as session:
        session.get(DiagnosticIngestBatch, (batch.instance_id, batch.batch_id)).next_attempt_at = datetime(2000, 1, 1, tzinfo=timezone.utc)
        enqueue(session, WorkSpec("diagnostic_maintenance", 1, {}, "recover-inbox"))
        work = claim(session, owner_id="replacement-worker", supported={"diagnostic_maintenance": {1}}, lease_seconds=120)
    maintain_diagnostics(work)
    with session_scope() as session:
        assert session.get(DiagnosticIngestBatch, (batch.instance_id, batch.batch_id)).delivered
    assert len(_read(second).events) == 1
