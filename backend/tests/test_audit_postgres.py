"""Required PostgreSQL audit contracts use a newly created isolated schema only."""
import os
from uuid import uuid4
import pytest
from sqlalchemy import create_engine,text,select,func
from sqlalchemy.orm import sessionmaker
from app.config import Settings
from app.db.models import AuditLog,AuditOutbox
from app.services import audit
from app.services.audit_schema import upgrade_audit_schema
from app.services.audit_runtime import preflight_audit
from app.services.audit_hold import hold_pending
from app.services.audit_outbox import AuditDispatcher

@pytest.fixture
def postgres(monkeypatch,tmp_path):
    url=os.getenv('AUDIT_TEST_POSTGRES_URL')
    if not url:
        if os.getenv('AUDIT_REQUIRE_POSTGRES')=='1':pytest.fail('Required disposable PostgreSQL unavailable')
        pytest.skip('Disposable AUDIT_TEST_POSTGRES_URL required')
    from sqlalchemy.engine import make_url
    origin=make_url(url)
    if origin.get_backend_name()!='postgresql' or not origin.database or not origin.database.startswith('audit_test_'):
        pytest.fail('Use an explicitly disposable audit_test_ database')
    admin=create_engine(origin)
    schema='audit_'+uuid4().hex
    with admin.begin() as c:c.execute(text('CREATE SCHEMA '+schema))
    engine=create_engine(origin.update_query_dict({'options':'-csearch_path='+schema}))
    config=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode='durable')
    monkeypatch.setattr('app.config.get_settings',lambda:config)
    try:yield engine,config
    finally:engine.dispose();admin.dispose()


def test_postgres_committed_only_exact_payload_and_hold(postgres):
    engine,config=postgres
    AuditLog.__table__.create(engine);AuditOutbox.__table__.create(engine)
    preflight_audit(config,engine=engine)
    sessions=sessionmaker(engine)
    with sessions.begin() as s:
        audit.record_in_session(s,action_type=audit.DOCUMENT_UPLOAD)
        with engine.connect() as other:assert other.scalar(select(func.count()).select_from(AuditOutbox))==0
    emitted=[];dispatcher=AuditDispatcher(config,session_factory=sessions,write_line=emitted.append)
    assert dispatcher.tick()==1
    with sessions.begin() as s:
        row=s.scalar(select(AuditOutbox));assert row.payload.encode()==emitted[0] and row.status=='stdout_written'
        audit.record_in_session(s,action_type=audit.DOCUMENT_DELETE)
    assert hold_pending(engine)==1 and hold_pending(engine)==0
    assert dispatcher.tick()==0


def test_postgres_outer_and_savepoint_rollback(postgres):
    engine,config=postgres
    AuditLog.__table__.create(engine);AuditOutbox.__table__.create(engine)
    sessions=sessionmaker(engine)
    with pytest.raises(RuntimeError):
        with sessions.begin() as s:
            audit.record_in_session(s,action_type=audit.DOCUMENT_UPLOAD)
            raise RuntimeError()
    with sessions.begin() as s:
        nested=s.begin_nested();audit.record_in_session(s,action_type=audit.DOCUMENT_UPLOAD);nested.rollback()
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(AuditLog))==0
        assert c.scalar(select(func.count()).select_from(AuditOutbox))==0


def test_postgres_nonempty_legacy_additive_upgrade(postgres):
    engine,config=postgres
    from tests.test_audit_schema import LEGACY
    with engine.begin() as c:
        c.execute(text(LEGACY.replace('id INTEGER PRIMARY KEY','id SERIAL PRIMARY KEY').replace('DATETIME','TIMESTAMP WITH TIME ZONE')))
        c.execute(text("INSERT INTO audit_log(created_at,action_type,username) VALUES(CURRENT_TIMESTAMP,'document_upload','legacy')"))
        upgrade_audit_schema(c);upgrade_audit_schema(c)
        assert c.execute(text('SELECT username,event_id FROM audit_log')).one()==('legacy',None)
        assert c.execute(text('SELECT count(*) FROM audit_outbox')).scalar()==0

    preflight_audit(config,engine=engine)
    with sessionmaker(engine).begin() as session:
        item=audit.record_in_session(session,action_type=audit.DOCUMENT_UPLOAD)
        assert item['id']==2


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process/SIGKILL proof')
@pytest.mark.parametrize('mode', ['crash', 'partial', 'lock'])
def test_postgres_process_faults_keep_committed_payload(postgres, tmp_path, monkeypatch, mode):
    import json, subprocess, sys, time
    from tests.test_audit_process_faults import CHILD, run_child
    engine, config = postgres
    monkeypatch.setenv('AUDIT_PROCESS_DATABASE_URL', engine.url.render_as_string(hide_password=False))
    seed = run_child(tmp_path, 'seed')
    assert seed.returncode == 0, seed.stderr.decode()
    with engine.connect() as connection:
        payload = connection.scalar(select(AuditOutbox.payload)).encode()
    if mode == 'lock':
        owner = subprocess.Popen([sys.executable, '-c', CHILD, str(tmp_path), 'lock'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 8
            while not (tmp_path / 'lock-ready').exists() and time.monotonic() < deadline:
                assert owner.poll() is None
                time.sleep(.02)
            assert (tmp_path / 'lock-ready').exists()
            assert run_child(tmp_path, 'deliver').returncode == 4
        finally:
            owner.kill(); owner.communicate(timeout=3)
        assert owner.returncode == -9
    else:
        stopped = run_child(tmp_path, mode)
        assert stopped.returncode == (73 if mode == 'crash' else 74), stopped.stderr.decode()
        if mode == 'crash': assert stopped.stdout.strip() == payload
        else: assert stopped.stdout == b'\n' + payload[:20]
    delivered = run_child(tmp_path, 'deliver')
    assert delivered.returncode == 0, delivered.stderr.decode()
    assert delivered.stdout == b'\n' + payload + b'\n'
    assert json.loads(delivered.stdout)['schema_version'] == 1
    with engine.connect() as connection:
        assert connection.execute(select(AuditOutbox.status, AuditOutbox.attempt_count)).one() == ('stdout_written', 1 if mode == 'lock' else 2)


def test_postgres_denial_preserves_schema_options_and_lock_budget(postgres, monkeypatch):
    import time
    from app.services.audit_security import record_denial_once, failure_count
    from tests.test_platform_audit_contract import request
    engine, config = postgres
    AuditLog.__table__.create(engine); AuditOutbox.__table__.create(engine)
    monkeypatch.setattr('app.services.audit_security.get_engine', lambda: engine)
    record_denial_once(request(), 'session_absent')
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(AuditOutbox)) == 1
    with engine.begin() as holder:
        holder.execute(text('LOCK TABLE audit_log IN ACCESS EXCLUSIVE MODE'))
        before = failure_count(); started = time.monotonic()
        record_denial_once(request(), 'session_absent')
        assert time.monotonic() - started < 5
        assert failure_count() == before + 1
    with engine.connect() as c:
        assert c.scalar(select(func.count()).select_from(AuditOutbox)) == 1


def test_postgres_concurrent_unblock_audits_actual_row_counts(postgres):
    from concurrent.futures import ThreadPoolExecutor
    import json, threading
    from app.db.models import UserBlock
    from app.services.blocklist import Blocklist
    engine, config = postgres
    AuditLog.__table__.create(engine); AuditOutbox.__table__.create(engine); UserBlock.__table__.create(engine)
    sessions = sessionmaker(engine)
    blocks = Blocklist()
    with sessions.begin() as s: blocks.block_in_session(s, 'synthetic-user', blocked_by='synthetic-security')
    barrier = threading.Barrier(2)
    def unblock():
        with sessions.begin() as s:
            barrier.wait(timeout=3)
            count = blocks.unblock_in_session(s, 'synthetic-user')
            audit.record_in_session(s, action_type=audit.USER_UNBLOCK, new_value={'count': count})
            return count
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(unblock) for _ in range(2)]
        counts = sorted(f.result(timeout=8) for f in futures)
    assert counts == [0, 1]
    with engine.connect() as c:
        values = [json.loads(p)['new_value']['count'] for p in c.scalars(select(AuditOutbox.payload))]
        assert sorted(values) == counts


def test_postgres_readonly_role_fails_preflight_without_fake_events(postgres):
    from sqlalchemy import event
    from sqlalchemy.exc import DBAPIError
    engine, config = postgres
    AuditLog.__table__.create(engine); AuditOutbox.__table__.create(engine)
    role = 'audit_reader_' + uuid4().hex
    schema = engine.url.query['options'].split('=', 1)[1]
    with engine.begin() as c:
        c.execute(text('CREATE ROLE ' + role))
        c.execute(text('GRANT USAGE ON SCHEMA ' + schema + ' TO ' + role))
        c.execute(text('GRANT SELECT ON ALL TABLES IN SCHEMA ' + schema + ' TO ' + role))
    restricted = create_engine(engine.url)
    @event.listens_for(restricted, 'connect')
    def use_role(connection, record):
        with connection.cursor() as cursor: cursor.execute('SET ROLE ' + role)
        connection.commit()
    try:
        with pytest.raises(DBAPIError) as error: preflight_audit(config, engine=restricted)
        assert error.value.orig.sqlstate == '42501'
        with engine.begin() as c:
            c.execute(text('GRANT INSERT ON audit_log,audit_outbox TO ' + role))
            c.execute(text('GRANT USAGE ON ALL SEQUENCES IN SCHEMA ' + schema + ' TO ' + role))
            c.execute(text('GRANT UPDATE(attempt_count) ON audit_outbox TO ' + role))
        with pytest.raises(DBAPIError) as incomplete: preflight_audit(config, engine=restricted)
        assert incomplete.value.orig.sqlstate == '42501'
        with engine.begin() as c:
            c.execute(text('GRANT UPDATE(status,next_attempt_at,last_error_code,stdout_written_at) ON audit_outbox TO ' + role))
        preflight_audit(config, engine=restricted)
        with engine.begin() as c: c.execute(text('REVOKE SELECT ON audit_log FROM ' + role))
        with pytest.raises(DBAPIError) as unreadable: preflight_audit(config, engine=restricted)
        assert unreadable.value.orig.sqlstate == '42501'
        with engine.connect() as c:
            assert c.scalar(select(func.count()).select_from(AuditLog)) == 0
            assert c.scalar(select(func.count()).select_from(AuditOutbox)) == 0
    finally: restricted.dispose()
