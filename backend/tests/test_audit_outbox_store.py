import json
import pytest
from sqlalchemy import func, select
from app.config import Settings
from app.db.models import AuditLog, AuditOutbox
from app.db.session import session_scope
from app.services import audit
from app.services.audit_event import AuditContext

@pytest.fixture(autouse=True)
def mode(monkeypatch,tmp_path):
    settings=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode="durable")
    monkeypatch.setattr("app.config.get_settings",lambda:settings)
    return settings

def totals():
    with session_scope() as s:return [s.scalar(select(func.count()).select_from(m)) for m in (AuditLog,AuditOutbox)]

def test_commit_creates_audit_and_outbox_same_event():
    item=audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD,new_value={"size":4},user_id="u1")
    with session_scope() as s:
        row=s.scalar(select(AuditOutbox));log=s.get(AuditLog,item["id"])
        assert json.loads(row.payload)["event_id"]==log.event_id
        assert row.audit_id==log.id and row.status=="pending"
    assert totals()==[1,1]

def test_outer_and_savepoint_rollback_publish_nothing():
    with pytest.raises(RuntimeError):
        with session_scope() as s:
            audit.record_in_session(s,action_type=audit.DOCUMENT_DELETE)
            assert totals()==[0,0]
            raise RuntimeError()
    assert totals()==[0,0]
    with session_scope() as s:
        savepoint=s.begin_nested()
        audit.record_in_session(s,action_type=audit.DOCUMENT_DELETE)
        savepoint.rollback()
    assert totals()==[0,0]

def test_invalid_event_rolls_back_audit():
    with pytest.raises(ValueError):audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD,username="я"*9000,event_context=AuditContext(actor_type="user"))
    assert totals()==[0,0]

def test_disabled_durable_disabled_transition(mode):
    mode.audit_stdout_mode="disabled"
    audit.get_audit().append(action_type=audit.DOCUMENT_DELETE,old_value={"filename":"legacy"})
    assert totals()==[1,0]
    assert audit.get_audit().query()[0]["old_value"]=={"filename":"legacy"}
    mode.audit_stdout_mode="durable"
    audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
    mode.audit_stdout_mode="disabled"
    audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
    assert totals()==[3,1]


def test_serialization_failure_caught_by_owner_leaves_no_audit(mode):
    with session_scope() as session:
        try:audit.record_in_session(session,action_type=audit.DOCUMENT_UPLOAD,username='я'*9000)
        except ValueError:pass
        audit.record_in_session(session,action_type=audit.DOCUMENT_DELETE)
    assert totals()==[1,1]


def test_runtime_counters_count_only_root_commits(mode):
    from app.services.audit_outbox import AuditDispatcher
    dispatcher = AuditDispatcher(mode, write_line=lambda payload: None)
    before = dispatcher.status()["counters"]
    with session_scope() as session:
        audit.record_in_session(session, action_type="auth_access_denied", event_context=AuditContext(outcome="denied"))
        assert dispatcher.status()["counters"]["recorded"] == before["recorded"]
        committed = session.begin_nested()
        audit.record_in_session(session, action_type=audit.DOCUMENT_UPLOAD)
        committed.commit()
        rolled_back = session.begin_nested()
        audit.record_in_session(session, action_type="auth_access_denied", event_context=AuditContext(outcome="denied"))
        rolled_back.rollback()
        assert dispatcher.status()["counters"]["recorded"] == before["recorded"]
    after = dispatcher.status()["counters"]
    assert after["recorded"] == before["recorded"] + 2
    assert after["denied"] == before["denied"] + 1
    with pytest.raises(RuntimeError):
        with session_scope() as session:
            audit.record_in_session(session, action_type=audit.DOCUMENT_UPLOAD)
            raise RuntimeError()
    assert dispatcher.status()["counters"] == after


def test_failed_owner_commit_does_not_increment_counters(mode, monkeypatch):
    from app.services.audit_outbox import AuditDispatcher
    from app.db.session import get_session_factory
    dispatcher = AuditDispatcher(mode, write_line=lambda payload: None)
    before = dispatcher.status()["counters"]
    with get_session_factory()() as session:
        audit.record_in_session(session, action_type=audit.DOCUMENT_UPLOAD)
        monkeypatch.setattr(session, "commit", lambda: (_ for _ in ()).throw(RuntimeError()))
        with pytest.raises(RuntimeError): session.commit()
        session.rollback()
    assert dispatcher.status()["counters"] == before


def test_database_unique_id_allows_legacy_nulls_but_not_duplicate_events(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.exc import IntegrityError
    engine = create_engine(f"sqlite:///{tmp_path / 'owned-unique.db'}")
    try:
        AuditLog.__table__.create(engine); AuditOutbox.__table__.create(engine)
        sessions = sessionmaker(engine)
        with sessions.begin() as session:
            session.add_all([AuditLog(action_type=audit.DOCUMENT_UPLOAD), AuditLog(action_type=audit.DOCUMENT_UPLOAD)])
        with sessions.begin() as session:
            item = audit.record_in_session(session, action_type=audit.DOCUMENT_UPLOAD)
            event_id = session.get(AuditLog, item['id']).event_id
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(AuditLog(action_type=audit.DOCUMENT_UPLOAD, event_id=event_id))
                    session.flush()
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(AuditLog)) == 3
            assert connection.scalar(select(func.count()).select_from(AuditOutbox)) == 1
    finally: engine.dispose()


def test_failed_outbox_insert_rolls_back_flushed_audit(tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.exc import IntegrityError
    engine = create_engine(f"sqlite:///{tmp_path / 'owned-failed-insert.db'}")
    try:
        AuditLog.__table__.create(engine); AuditOutbox.__table__.create(engine)
        with engine.begin() as connection:
            connection.execute(text("CREATE TRIGGER refuse_outbox BEFORE INSERT ON audit_outbox BEGIN SELECT RAISE(ABORT, 'CANARY'); END"))
        with pytest.raises(IntegrityError):
            with sessionmaker(engine).begin() as session:
                audit.record_in_session(session, action_type=audit.DOCUMENT_UPLOAD)
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(AuditLog)) == 0
            assert connection.scalar(select(func.count()).select_from(AuditOutbox)) == 0
    finally: engine.dispose()
