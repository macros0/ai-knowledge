import json
import pytest
from sqlalchemy import select,func
from starlette.requests import Request
from app.config import Settings
from app.auth.identity import AuthenticatedIdentity
from app.auth.service import store_identity,clear_identity
from app.auth.audit_errors import AuthSessionStoreUnavailable
from app.db.models import AuthSession,AuditOutbox,AuditLog,UserBlock
from app.db.session import session_scope
from app.services import audit

@pytest.fixture
def settings(monkeypatch,tmp_path):
    config=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode='durable',auth_provider='simulation',auth_sim_users=[{'user_id':'u1','username':'demo','groups':['KB_Viewer']}])
    monkeypatch.setattr('app.config.get_settings',lambda:config)
    return config

def request():
    return Request({'type':'http','method':'POST','path':'/api/auth/callback','headers':[],'session':{},'state':{'request_id':'00000000-0000-4000-8000-000000000001'}})

def identity():return AuthenticatedIdentity(external_id='u1',username='demo',groups=['KB_Viewer'],provider='simulation')

def counts():
    with session_scope() as s:return [s.scalar(select(func.count()).select_from(m)) for m in (AuthSession,AuditLog,AuditOutbox)]

def test_login_audit_and_session_commit_together(settings):
    r=request();store_identity(r,identity())
    assert counts()==[1,1,1]
    with session_scope() as s:
        value=json.loads(s.scalar(select(AuditOutbox.payload)))
        assert value['action']=='auth_login_success' and value['method']=='simulation'
        assert value['actor']['id']=='u1' and 'id_token' not in json.dumps(value)

def test_login_audit_failure_sets_no_cookie_and_keeps_previous_session(settings,monkeypatch):
    r=request();store_identity(r,identity());before=dict(r.session)
    def fail(*a,**k):raise ValueError('CANARY')
    monkeypatch.setattr(audit,'record_in_session',fail)
    with pytest.raises(AuthSessionStoreUnavailable):store_identity(r,identity())
    assert r.session==before and counts()==[1,1,1]

def test_logout_audit_and_revoke_commit_together(settings):
    r=request();store_identity(r,identity());clear_identity(r)
    assert counts()==[0,2,2] and not r.session
    clear_identity(r)
    assert counts()==[0,2,2]

def test_logout_failure_rolls_back_revoke(settings,monkeypatch):
    r=request();store_identity(r,identity());before=dict(r.session)
    monkeypatch.setattr(audit,'record_in_session',lambda *a,**k:(_ for _ in ()).throw(ValueError('CANARY')))
    with pytest.raises(AuthSessionStoreUnavailable):clear_identity(r)
    assert r.session==before and counts()==[1,1,1]

def test_one_denial_event_per_request(settings):
    from app.services.audit_security import record_denial_once
    r=request();record_denial_once(r,'session_absent');record_denial_once(r,'no_role')
    assert counts()==[0,1,1]

def test_denial_audit_failure_remains_denied(settings,monkeypatch):
    from app.services.audit_security import record_denial_once,failure_count
    monkeypatch.setattr(audit,'record_in_session',lambda *a,**k:(_ for _ in ()).throw(ValueError('CANARY')))
    before=failure_count();r=request();record_denial_once(r,'session_absent')
    assert failure_count()==before+1 and counts()==[0,0,0]


def test_block_and_unblock_audit_failure_roll_back_mutation(settings,monkeypatch):
    from app.api.users import block_user,unblock_user
    from app.models.schemas import BlockUserRequest
    from app.auth.models import User
    from app.api.errors import ApiError
    user=User(user_id='security',username='security',roles=['security'])
    def fail(*a,**k):raise ValueError('CANARY')
    with monkeypatch.context() as scoped:
        scoped.setattr(audit,'record_in_session',fail)
        with pytest.raises(ApiError) as error:block_user('u1',BlockUserRequest(reason='CANARY'),request(),user)
        assert error.value.status_code==503
    with session_scope() as s:assert s.scalar(select(func.count()).select_from(UserBlock))==0
    block_user('u1',BlockUserRequest(),request(),user)
    with monkeypatch.context() as scoped:
        scoped.setattr(audit,'record_in_session',fail)
        with pytest.raises(ApiError):unblock_user('u1',request(),user)
    with session_scope() as s:assert s.scalar(select(UserBlock.is_active)) is True
