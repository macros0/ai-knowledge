"""Owned PostgreSQL schemas: competing API admissions and forward schema upgrade."""
from multiprocessing import get_context
from pathlib import Path
import re
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.db.session import configure_database, get_engine, session_scope
from app.db.models import AuditLog, DiagnosticIngestBatch, DiagnosticInstance, DiagnosticReplicaState, DiagnosticSegment
from app.services.diagnostics.ingest import DiagnosticBatch, ingest_events
from app.services.diagnostics.replica_store import ReplicaDiagnosticStore
from app.services.diagnostics.schema import EventFilter
from app.services.work.store import _now
from tests.support.replica_postgres import disposable_postgres
from tests.test_diagnostics_lifecycle import settings as diagnostic_settings
from tests.test_internal_diagnostics import event as frontend_event


def _settings(root):
    settings = diagnostic_settings(root)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    return settings


def _admit(url, root, value, pipe):
    import app.config
    settings = _settings(Path(root))
    app.config.get_settings = lambda: settings
    configure_database(url)
    store = ReplicaDiagnosticStore(settings, instance_id=str(uuid4()))
    store.open()
    try:
        pipe.send("ready")
        assert pipe.poll(30) and pipe.recv() == "admit"
        result = ingest_events(DiagnosticBatch.model_validate(value), store=store)
        pipe.send((result.accepted, result.duplicate))
    finally:
        store.close()
        get_engine().dispose()
        pipe.close()


def test_two_api_processes_admit_one_durable_frontend_receipt(tmp_path, monkeypatch):
    with disposable_postgres() as url:
        settings = _settings(tmp_path / "reader")
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        value = {"instance_id": str(uuid4()), "batch_id": str(uuid4()), "events": [frontend_event()]}
        context = get_context("spawn")
        processes, pipes = [], []
        try:
            for name in ("api-a", "api-b"):
                parent, child = context.Pipe()
                process = context.Process(target=_admit, args=(url, str(tmp_path / name), value, child))
                process.start()
                child.close()
                processes.append(process)
                pipes.append(parent)
            for pipe in pipes:
                assert pipe.poll(30) and pipe.recv() == "ready"
            for pipe in pipes:
                pipe.send("admit")
            replies = []
            for pipe in pipes:
                assert pipe.poll(20)
                replies.append(pipe.recv())
            assert sorted(replies) == [(1, False), (1, True)]
            for process in processes:
                process.join(15)
                assert process.exitcode == 0
            with session_scope() as session:
                assert len(list(session.scalars(select(DiagnosticIngestBatch)))) == 1
                assert session.scalar(select(DiagnosticIngestBatch)).delivered
                assert len(list(session.scalars(select(DiagnosticSegment)))) == 1
                cutoff = _now(session)
            reader = ReplicaDiagnosticStore(settings, instance_id=str(uuid4()))
            try:
                view = reader.pin_read_view(EventFilter(), cutoff)
                try:
                    events = reader.read_event_page(view, offset=0, limit=100).events
                    assert [item["event_id"] for item in events] == [value["events"][0]["event_id"]]
                finally:
                    view.release()
            finally:
                reader.close()
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            for pipe in pipes:
                pipe.close()


def test_postgres_diagnostic_upgrade_preserves_existing_audit(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from app.services import audit
    with disposable_postgres() as url:
        settings = _settings(tmp_path)
        monkeypatch.setattr("app.config.get_settings", lambda: settings)
        entry = audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
        with session_scope() as session:
            schema = session.scalar(text("SELECT current_schema()"))
        assert re.fullmatch(r"artifact_[0-9a-f]{32}", schema)
        for model in (DiagnosticSegment, DiagnosticInstance, DiagnosticReplicaState):
            model.__table__.drop(get_engine())
        monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema} -clock_timeout=2000 -cstatement_timeout=10000")
        monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(db_url=url))
        root = Path(__file__).resolve().parents[1]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        command.stamp(config, "rpl03a_chat_runs")
        command.upgrade(config, "rpl03b_diagnostics")
        with session_scope() as session:
            assert session.scalar(select(AuditLog.event_id)) == entry["event_id"]
            session.add(DiagnosticReplicaState(id=1, policy_hash="0" * 64, evicted_segments=1))
        with pytest.raises(RuntimeError, match="diagnostic"):
            command.downgrade(config, "rpl03a_chat_runs")
