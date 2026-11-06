"""A replacement worker builds a portable ZIP from all durable instance events."""
from io import BytesIO
import json
from uuid import uuid4
from zipfile import ZipFile

from app.db.models import DiagnosticBundle, WorkItem
from app.db.session import session_scope
from app.models.diagnostics import BundleRequest
from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
from app.services.diagnostics.ingest import DiagnosticBatch, ingest_events
from app.services.diagnostics.replica_store import ReplicaDiagnosticStore
from app.services.diagnostics.scratch import clean_instances
from app.services.work.store import claim
from tests.test_diagnostics_lifecycle import settings as diagnostic_settings
from tests.test_diagnostics_sessions import actor
from tests.test_diagnostics_store import encoded
from tests.test_internal_diagnostics import event as frontend_event


def test_cold_worker_builds_and_cold_api_downloads_all_instance_events(tmp_path, monkeypatch):
    configurations = []
    for name, role in (("api-a", "api"), ("worker-b", "worker"), ("api-c", "api")):
        configured = diagnostic_settings(tmp_path / name, artifact_root=tmp_path / "artifacts",
            diagnostics_dir=tmp_path / "diagnostics", artifact_uploads_enabled=True)
        configured.okf_deployment_schema, configured.app_role = "replicas-v1", role
        configurations.append(configured)
    current = [configurations[0]]
    monkeypatch.setattr("app.config.get_settings", lambda: current[0])
    original = ReplicaDiagnosticStore(configurations[0], instance_id=str(uuid4()))
    original.open()
    backend, frontend = encoded(), frontend_event()
    try:
        assert original.append(backend, stream="baseline")
        ingest_events(DiagnosticBatch(instance_id=str(uuid4()), batch_id=str(uuid4()), events=[frontend]), store=original)
        result = DiagnosticBundleQueue(original).submit(BundleRequest(), actor())
    finally:
        original.close()
    clean_instances(configurations[0])
    assert not original.root.exists()
    current[0] = configurations[1]
    with ReplicaDiagnosticStore(configurations[1], instance_id=str(uuid4())) as worker:
        with session_scope() as session:
            work = claim(session, owner_id=str(uuid4()), supported={"diagnostic_bundle": {1}}, lease_seconds=120)
        assert work is not None
        DiagnosticBundleQueue(worker).execute_work(work)
        with session_scope() as session:
            assert session.get(WorkItem, work.work_id).status == "succeeded"
            assert session.get(DiagnosticBundle, result.id).status == "ready"
        assert not list((worker.root / "inputs").glob("*"))
        assert not list((worker.root / "snapshots").glob("*"))
    current[0] = configurations[2]
    with ReplicaDiagnosticStore(configurations[2], instance_id=str(uuid4())) as reader:
        lease = DiagnosticBundleQueue(reader).acquire_download(result.id, actor())
        with ZipFile(BytesIO(b"".join(lease.chunks()))) as archive:
            assert archive.testzip() is None
            events = [json.loads(line) for name in ("events/backend.jsonl", "events/frontend.jsonl")
                for line in archive.read(name).splitlines()]
            assert {item["event_id"] for item in events} == {json.loads(backend)["event_id"], frontend["event_id"]}
