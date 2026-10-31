import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.internal_diagnostics import router
from app.config import get_settings
from app.db.models import DiagnosticIngestBatch
from app.db.session import session_scope
from app.services.diagnostics.schema import DiagnosticLimits, EventFilter
from app.services.diagnostics.store import DiagnosticStore
from app.services.diagnostics.ingest import drain_ingest

TOKEN = "private-token-" * 4


def event():
    return dict(schema_version=1, event_id=str(uuid4()), boot_id=str(uuid4()),
                timestamp_utc=datetime.now(timezone.utc).isoformat(), component="frontend",
                origin="server", level="ERROR", event_code="render_failed", exception_type="Error")


@pytest.fixture
def api(tmp_path):
    app = FastAPI()
    app.include_router(router)
    settings = SimpleNamespace(diagnostics_ingest_token=TOKEN, diagnostics_baseline_enabled=True)
    app.dependency_overrides[get_settings] = lambda: settings
    with DiagnosticStore(tmp_path / "diagnostics", DiagnosticLimits(min_free_bytes=0)) as store:
        app.state.diagnostics = SimpleNamespace(available=True, store=store)
        with TestClient(app) as client:
            yield client, store


def batch():
    return {"instance_id": str(uuid4()), "batch_id": str(uuid4()), "events": [event()]}


def test_ingest_requires_internal_identity(api):
    client, _ = api
    assert client.post("/internal/diagnostics/events", json=batch()).status_code == 401
    with session_scope() as session:
        assert session.query(DiagnosticIngestBatch).count() == 0


def test_batch_limits_and_schema(api):
    client, _ = api
    headers = {"Authorization": f"Bearer {TOKEN}"}
    value = batch()
    value["events"][0]["message"] = "private-content"
    response = client.post("/internal/diagnostics/events", json=value, headers=headers)
    assert response.status_code == 422
    assert "private-content" not in response.text
    assert client.post("/internal/diagnostics/events", content=b"x" * (256 * 1024 + 1), headers=headers).status_code == 413


def test_duplicate_batch_id(api):
    client, store = api
    headers = {"Authorization": f"Bearer {TOKEN}"}
    value = batch()
    first = client.post("/internal/diagnostics/events", json=value, headers=headers)
    assert first.status_code == 200
    assert first.json() == {"accepted": 1, "duplicate": False}
    assert client.post("/internal/diagnostics/events", json=value, headers=headers).json()["duplicate"]
    files = list((store.root / "events" / "baseline").glob("*.jsonl"))
    assert sum(len(path.read_text().splitlines()) for path in files) == 1
    view = store.pin_read_view(EventFilter(), datetime.now(timezone.utc))
    try:
        page = store.read_event_page(view, offset=0, limit=100)
        assert [item["event_id"] for item in page.events] == [value["events"][0]["event_id"]]
    finally:
        view.release()
    value["events"][0]["level"] = "WARN"
    assert client.post("/internal/diagnostics/events", json=value, headers=headers).status_code == 409


def test_expired_control_fails_closed(api):
    client, store = api
    (store.root / "control" / "capture.json").write_text(json.dumps({
        "schema_version": 2, "boot_id": str(uuid4()), "revision": 0,
        "active": True, "lease_until": 0,
    }))
    response = client.get("/internal/diagnostics/control", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 503
    assert "private" not in response.text


def test_backend_and_browser_impersonation_rejected(api):
    client, _ = api
    value = batch()
    value["events"][0]["component"] = "backend"
    assert client.post("/internal/diagnostics/events", json=value,
                       headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 422


def test_spool_failure_keeps_durable_admission_for_retry(api, monkeypatch):
    client, store = api
    original = store.write_remote_batch
    def unavailable(*args, **kwargs):
        raise OSError("Unavailable")
    monkeypatch.setattr(store, "write_remote_batch", unavailable)
    value = batch()
    response = client.post("/internal/diagnostics/events", json=value,
                           headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    with session_scope() as session:
        row = session.get(DiagnosticIngestBatch, (value["instance_id"], value["batch_id"]))
        assert row is not None and not row.delivered and row.accepted == 1
    monkeypatch.setattr(store, "write_remote_batch", original)
    drain_ingest(store, now=datetime.now(timezone.utc) + timedelta(seconds=61))
    with session_scope() as session:
        assert session.get(DiagnosticIngestBatch, (value["instance_id"], value["batch_id"])).delivered


def test_spool_written_before_sql_commit_is_not_duplicated(api, monkeypatch):
    client, store = api
    from app.services.diagnostics import ingest
    original = ingest._deliver
    def interrupted(target, row):
        original(target, row)
        raise OSError("Process interrupted before commit")
    monkeypatch.setattr(ingest, "_deliver", interrupted)
    value = batch()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/internal/diagnostics/events", json=value, headers=headers).status_code == 200
    monkeypatch.setattr(ingest, "_deliver", original)
    assert client.post("/internal/diagnostics/events", json=value, headers=headers).json()["duplicate"]
    files = list((store.root / "events" / "baseline").glob("*.jsonl"))
    assert sum(len(path.read_text().splitlines()) for path in files) == 1


def test_ingest_migration_preserves_other_tables(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, inspect
    path = Path(__file__).resolve().parents[1] / "alembic/versions/rpl00_diagnostic_ingest.py"
    spec = importlib.util.spec_from_file_location("ingest_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE marker (value INTEGER)")
            connection.exec_driver_sql("INSERT INTO marker VALUES (42)")
            monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(connection)))
            migration.upgrade()
            assert "diagnostic_ingest_batches" in inspect(connection).get_table_names()
            assert connection.exec_driver_sql("SELECT value FROM marker").scalar() == 42
            migration.downgrade()
            assert "marker" in inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_failed_batch_does_not_starve_following_baseline(api, monkeypatch):
    client, store = api
    original = store.write_remote_batch
    blocked = batch()
    following = batch()
    def sometimes_unavailable(identity, events, *, stream):
        if events[0]["event_id"] == blocked["events"][0]["event_id"]:
            raise OSError("One stream unavailable")
        return original(identity, events, stream=stream)
    monkeypatch.setattr(store, "write_remote_batch", sometimes_unavailable)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    assert client.post("/internal/diagnostics/events", json=blocked, headers=headers).status_code == 200
    assert client.post("/internal/diagnostics/events", json=following, headers=headers).status_code == 200
    with session_scope() as session:
        row = session.get(DiagnosticIngestBatch, (following["instance_id"], following["batch_id"]))
        assert row.delivered
