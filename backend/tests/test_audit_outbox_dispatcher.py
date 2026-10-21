import threading
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from app.config import Settings
from app.db.models import AuditOutbox
from app.db.session import session_scope
from app.services import audit
from app.services.audit_outbox import AuditDispatcher

@pytest.fixture
def settings(monkeypatch,tmp_path):
    config=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode="durable")
    monkeypatch.setattr("app.config.get_settings",lambda:config)
    return config

def state():
    with session_scope() as s:
        row=s.scalar(select(AuditOutbox))
        return row.status,row.attempt_count,row.last_error_code,row.payload

def test_flush_then_marker_preserves_exact_payload(settings):
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    written=[]
    d=AuditDispatcher(settings,write_line=written.append)
    assert d.tick()==1
    assert state()[0]=='stdout_written'
    assert written==[state()[3].encode('utf-8')]

def test_marker_failure_retries_same_event_id(settings,monkeypatch):
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    written=[]
    d=AuditDispatcher(settings,write_line=written.append)
    original=d._mark_written
    monkeypatch.setattr(d,'_mark_written',lambda *a:(_ for _ in ()).throw(RuntimeError('CANARY')))
    d.tick()
    assert state()[0]=='pending' and state()[2]=='marker_failure'
    monkeypatch.setattr(d,'_mark_written',original)
    with session_scope() as s:s.scalar(select(AuditOutbox)).next_attempt_at=datetime.now(timezone.utc)-timedelta(seconds=1)
    d.tick()
    assert len(written)==2 and written[0]==written[1]
    assert state()[0]=='stdout_written'

def test_broken_pipe_uses_bounded_backoff(settings):
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    d=AuditDispatcher(settings,write_line=lambda b:(_ for _ in ()).throw(BrokenPipeError('CANARY')))
    assert d.tick()==0
    assert state()[:3]==('pending',1,'stdout_failure')
    assert d.tick()==0 and state()[1]==1

def test_invalid_payload_is_held_without_starving_valid_rows(settings):
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
    with session_scope() as s:s.scalars(select(AuditOutbox).order_by(AuditOutbox.id)).first().payload='invalid'
    written=[];d=AuditDispatcher(settings,write_line=written.append)
    d.tick()
    with session_scope() as s:assert list(s.scalars(select(AuditOutbox.status).order_by(AuditOutbox.id)))==['held','stdout_written']
    assert len(written)==1

def test_blocked_writer_and_shutdown_retain_lock(settings):
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    started=threading.Event();release=threading.Event()
    def sink(b):started.set();release.wait(5)
    d=AuditDispatcher(settings,write_line=sink);d.start()
    try:
        assert started.wait(3)
        assert d.stop(timeout_seconds=.01) is False
        with pytest.raises(ValueError):AuditDispatcher(settings,write_line=lambda b:None).start()
        assert d.status()['state']=='stopping'
        audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
    finally:
        release.set();assert d.stop(timeout_seconds=3)
    next_dispatcher=AuditDispatcher(settings,write_line=lambda b:None)
    next_dispatcher.start();assert next_dispatcher.stop(timeout_seconds=3)


def test_short_write_is_completed_and_partial_frame_is_separated(monkeypatch):
    from app.services.audit_outbox import stdout_line
    pieces=[]
    def short_write(fd,data):
        part=bytes(data[:3]);pieces.append(part);return len(part)
    monkeypatch.setattr('app.services.audit_outbox.os.write',short_write)
    stdout_line(b'{"event_id":"test"}')
    assert b''.join(pieces)==b'\n{"event_id":"test"}\n'


def test_database_outage_recovers_without_hot_loop(settings):
    class Offline:
        def __call__(self):raise RuntimeError('CANARY')
    d=AuditDispatcher(settings,session_factory=Offline())
    assert d.tick()==0 and d.status()['last_error_code']=='database_failure'
    assert d.tick()==0 and d.status()['counters']['database_failure']==1


def test_boolean_schema_is_held_and_does_not_starve_valid_event(settings):
    import json
    audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD)
    audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
    with session_scope() as session:
        first = session.scalars(select(AuditOutbox).order_by(AuditOutbox.id)).first()
        event = json.loads(first.payload); event['schema_version'] = True
        first.payload = json.dumps(event, ensure_ascii=False, separators=(',', ':'))
    written = []; dispatcher = AuditDispatcher(settings, write_line=written.append)
    assert dispatcher.tick() == 1
    with session_scope() as session:
        assert list(session.scalars(select(AuditOutbox.status).order_by(AuditOutbox.id))) == ['held', 'stdout_written']
    assert len(written) == 1
