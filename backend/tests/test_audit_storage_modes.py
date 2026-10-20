import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.models import AuditLog, AuditOutbox
from app.db.session import session_scope
from app.services import audit
from app.services.audit_event import AuditContext


@pytest.mark.parametrize('db,stdout,expected', [(True,'disabled','db'), (False,'durable','external'), (True,'durable','both')])
def test_effective_mode(db, stdout, expected):
    from app.services.audit_modes import get_audit_mode
    settings = Settings(_env_file=None, audit_db_enabled=db, audit_stdout_mode=stdout)
    mode = get_audit_mode(settings)
    assert mode.effective_mode == expected
    assert mode.db_enabled is db
    assert mode.stdout_enabled is (stdout == 'durable')


def test_disabling_all_sinks_is_rejected():
    with pytest.raises(ValueError, match='audit|Audit|AUDIT'):
        Settings(_env_file=None, audit_db_enabled=False, audit_stdout_mode='disabled')


@pytest.mark.parametrize('db,stdout,counts', [(True,'disabled',(1,0)), (False,'durable',(0,1)), (True,'durable',(1,1))])
def test_selected_sinks_share_event_identity(monkeypatch, db, stdout, counts):
    settings = Settings(_env_file=None, audit_db_enabled=db, audit_stdout_mode=stdout)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    item = audit.get_audit().append(action_type=audit.DOCUMENT_UPLOAD, user_id='actor', new_value={'size':12})
    with session_scope() as session:
        assert tuple(session.scalar(select(func.count()).select_from(model)) for model in (AuditLog, AuditOutbox)) == counts
        if db:
            row = session.get(AuditLog, item['id'])
            assert row.event_id == item['event_id']
            assert row.created_at is not None
        else:
            assert item['id'] is None
        if stdout == 'durable':
            queued = session.scalar(select(AuditOutbox))
            payload = json.loads(queued.payload)
            assert queued.event_id == payload['event_id'] == item['event_id']
            assert queued.created_at is not None


@pytest.mark.parametrize('db,stdout', [(True,'disabled'), (False,'durable'), (True,'durable')])
def test_security_audit_enabled_independent_of_stdout(monkeypatch,db,stdout):
    settings = Settings(_env_file=None, audit_db_enabled=db, audit_stdout_mode=stdout)
    monkeypatch.setattr('app.config.get_settings',lambda:settings)
    from app.services.audit_security import enabled
    assert enabled()


def test_scope_audit_uses_external_only_queue(monkeypatch):
    settings=Settings(_env_file=None,audit_db_enabled=False,audit_stdout_mode='durable')
    monkeypatch.setattr('app.config.get_settings',lambda:settings)
    from app.services.processing_scope import append_scope_audit
    with session_scope() as session:
        append_scope_audit(session,'doc','actor',audit.DOCUMENT_PROCESSING_SCOPE_UPDATED,
                           SimpleNamespace(id='review',revision=2,selection_hash='hash',request_id='f71155103ed64b5abff1e4caa8ec5e46'))
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) == 0
        queued=session.scalar(select(AuditOutbox))
        assert json.loads(queued.payload)['action']==audit.DOCUMENT_PROCESSING_SCOPE_UPDATED


def test_external_pending_pauses_and_resumes_with_same_identity(monkeypatch,tmp_path):
    from app.services.audit_outbox import AuditDispatcher
    from app.services.audit_runtime import delivery_status
    settings=Settings(_env_file=None,data_dir=tmp_path,audit_db_enabled=False,audit_stdout_mode='durable')
    monkeypatch.setattr('app.config.get_settings',lambda:settings)
    item=audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
    settings.audit_db_enabled=True
    settings.audit_stdout_mode='disabled'
    status=delivery_status()
    assert status['effective_mode']=='db' and status['delivery_paused']
    assert status['counts']['pending']==1
    blocked=[]
    assert AuditDispatcher(settings,write_line=blocked.append).tick()==0
    assert blocked==[]
    settings.audit_db_enabled=False
    settings.audit_stdout_mode='durable'
    written=[]
    dispatcher=AuditDispatcher(settings,write_line=written.append)
    dispatcher.tick()
    assert len(written)==1 and json.loads(written[0])['event_id']==item['event_id']
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog))==0


@pytest.mark.parametrize('db,stdout', [(True,'disabled'), (False,'durable'), (True,'durable')])
def test_rollback_publishes_neither_sink(monkeypatch,db,stdout):
    settings=Settings(_env_file=None,audit_db_enabled=db,audit_stdout_mode=stdout)
    monkeypatch.setattr('app.config.get_settings',lambda:settings)
    with pytest.raises(RuntimeError):
        with session_scope() as session:
            audit.record_in_session(session,action_type='auth_access_denied',event_context=AuditContext(outcome='denied'))
            raise RuntimeError()
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog))==0
        assert session.scalar(select(func.count()).select_from(AuditOutbox))==0


def test_config_endpoint_is_security_only_and_not_cached(tmp_path,monkeypatch):
    from tests.test_audit import make_client, login
    client=make_client(tmp_path,monkeypatch,audit_db_enabled=False,audit_stdout_mode='durable',
        auth_sim_users=[{'user_id':'security','username':'security','groups':['KB_Security']},
                        {'user_id':'viewer','username':'viewer','groups':['KB_Viewer']}])
    assert client.get('/api/audit/config').status_code==401
    login(client,'viewer')
    assert client.get('/api/audit/config').status_code==403
    login(client,'security')
    response=client.get('/api/audit/config')
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert response.json()=={'effective_mode':'external','db_enabled':False,'stdout_mode':'durable','stdout_format':'native'}


@pytest.mark.parametrize('db,stdout,inserted', [(True,'disabled',{'audit_log'}),(False,'durable',{'audit_outbox'}),(True,'durable',{'audit_log','audit_outbox'})])
def test_preflight_requires_only_selected_sink_writes(db,stdout,inserted):
    from sqlalchemy import event
    from app.db.session import get_engine
    from app.services.audit_runtime import preflight_audit
    engine=get_engine()
    writes=[]
    def observe(conn,cursor,statement,parameters,context,executemany):
        if statement.upper().startswith('INSERT INTO '):
            writes.append(statement.split()[2])
    event.listen(engine,'before_cursor_execute',observe)
    try:
        preflight_audit(Settings(_env_file=None,audit_db_enabled=db,audit_stdout_mode=stdout))
    finally:
        event.remove(engine,'before_cursor_execute',observe)
    assert set(writes)==inserted


@pytest.mark.parametrize('db,stdout',[(True,'disabled'),(False,'durable'),(True,'durable')])
def test_actual_auth_scenarios_use_selected_channels(tmp_path,monkeypatch,db,stdout):
    from tests.test_audit import make_client,login
    client=make_client(tmp_path,monkeypatch,audit_db_enabled=db,audit_stdout_mode=stdout)
    assert client.get('/api/documents').status_code==401
    login(client)
    assert client.post('/api/auth/logout').status_code==200
    with session_scope() as session:
        local=list(session.scalars(select(AuditLog)))
        queued=list(session.scalars(select(AuditOutbox)))
        expected={'auth_access_denied','auth_login_success','auth_logout'}
        assert {row.action_type for row in local} == (expected if db else set())
        assert {json.loads(row.payload)['action'] for row in queued} == (expected if stdout=='durable' else set())
        assert len(local)==(3 if db else 0)
        assert len(queued)==(3 if stdout=='durable' else 0)


@pytest.mark.parametrize('failed_model',[AuditLog,AuditOutbox])
def test_failed_selected_sink_rolls_back_pair_and_counter(monkeypatch,failed_model):
    from sqlalchemy import event
    from app.services.audit_metrics import snapshot
    settings=Settings(_env_file=None,audit_stdout_mode='durable')
    monkeypatch.setattr('app.config.get_settings',lambda:settings)
    before=snapshot()
    def reject(mapper,connection,target):
        raise RuntimeError('injected sink failure')
    event.listen(failed_model,'before_insert',reject)
    try:
        with pytest.raises(RuntimeError,match='injected sink failure'):
            audit.get_audit().append(action_type=audit.DOCUMENT_DELETE)
    finally:
        event.remove(failed_model,'before_insert',reject)
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog))==0
        assert session.scalar(select(func.count()).select_from(AuditOutbox))==0
    assert snapshot()==before
