"""Actual app/entrypoint in an independent process, no live corpus or providers."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

CHILD = r"""
import json,sys,time
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import get_settings
from app.db.session import init_db,session_scope
from app.db.models import AuditLog, AuditOutbox
init_db()
from app import main
from app.diagnostic_entrypoint import main as entrypoint
class NoExternalVectors:
    def ensure_collection(self):pass
    def backfill_sparse(self,*a,**k):return 0
    def backfill_chunks(self,*a,**k):return 0
    def backfill_relations(self,*a,**k):return 0
main.VectorStore=NoExternalVectors

def run(app_path,**options):
    settings=get_settings()
    durable=settings.audit_stdout_mode=='durable'
    if durable:assert options['log_config']['handlers']['access']['stream']=='ext://sys.stderr'
    with TestClient(main.create_app()) as client:
        denied=client.get('/api/audit')
        assert denied.status_code==401
        correlation=denied.headers['x-request-id']
        login=client.post('/api/auth/simulate',json={'username':'audit-demo'},headers={'X-Request-ID':'00000000-0000-4000-8000-000000009999'})
        assert login.status_code==200,login.text
        assert login.headers['x-request-id']!='00000000-0000-4000-8000-000000009999'
        logout=client.post('/api/auth/logout')
        assert logout.status_code==200,logout.text
        if durable:
            deadline=time.monotonic()+8
            while time.monotonic()<deadline:
                with session_scope() as session:
                    rows=list(session.scalars(select(AuditOutbox)))
                    if len(rows)>=3 and all(row.status=='stdout_written' for row in rows):break
                time.sleep(.05)
            else:raise AssertionError('Outbox was not delivered')
        with session_scope() as session:
            logs=list(session.scalars(select(AuditLog)))
            queue=list(session.scalars(select(AuditOutbox)))
            expected={'auth_access_denied','auth_login_success','auth_logout'}
            if settings.audit_db_enabled:
                auth_logs=[row for row in logs if row.action_type.startswith('auth_')]
                assert len(auth_logs)==3 and {row.action_type for row in auth_logs}==expected
            else:
                assert logs==[]
            if not durable:assert queue==[]
            if durable and settings.audit_db_enabled:
                assert {row.event_id for row in logs}=={row.event_id for row in queue}
            events=[json.loads(row.payload) for row in queue]
            if durable:
                assert any(e['request_id']==correlation and e['action']=='auth_access_denied' for e in events)
            else:
                assert any(row.request_id==correlation and row.action_type=='auth_access_denied' for row in logs)
        print(json.dumps(events,ensure_ascii=False),file=sys.stderr)
assert entrypoint([],settings=get_settings(),runner=run)==0
"""

@pytest.mark.parametrize('fmt',['native','logstash'])
@pytest.mark.parametrize('db,stdout',[(True,'disabled'),(False,'durable'),(True,'durable')])
def test_main_operation_database_stdout_in_separate_process(tmp_path,fmt,db,stdout):
    backend=Path(__file__).parents[1]
    env={k:v for k,v in os.environ.items() if not k.startswith(('DATABASE_','AUTH_','AUDIT_','DIAGNOSTICS_','KEYCLOAK_','LLM_','EMBEDDING_'))}
    env.update(DATABASE_URL='sqlite:///'+str(tmp_path/'app.db'),DATA_DIR=str(tmp_path/'data'),
        ENVIRONMENT='development',AUDIT_DB_ENABLED=str(db).lower(),AUDIT_STDOUT_MODE=stdout,AUDIT_STDOUT_FORMAT=fmt,
        AUTH_PROVIDER='simulation',AUTH_SIM_USERS=json.dumps([{'user_id':'test-user','username':'audit-demo','groups':['KB_Viewer']}]),
        SOURCE_ASSESSMENT_ENABLED='false',QDRANT_URL='http://127.0.0.1:9')
    result=subprocess.run([sys.executable,'-c',CHILD],cwd=backend,env=env,capture_output=True,timeout=45)
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')[-5000:]
    frames=[json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    auth_frames=[e for e in frames if e['action'].startswith('auth_')]
    assert len(auth_frames)==(3 if stdout=='durable' else 0)
    assert {e['action'] for e in auth_frames}==({'auth_access_denied','auth_login_success','auth_logout'} if stdout=='durable' else set())
    assert len({e['event_id'] for e in frames})==len(frames)
    for event in frames:
        if fmt=='logstash':
            assert event['application']=='ai-knowledge' and event['@timestamp']==event['timestamp']
        else:assert 'application' not in event



def test_db_only_logout_requires_committed_security_audit(monkeypatch):
    from contextlib import contextmanager
    from sqlalchemy.exc import OperationalError
    from starlette.requests import Request
    from app.auth import service
    from app.config import Settings
    monkeypatch.setattr("app.config.get_settings",lambda:Settings(_env_file=None,audit_stdout_mode="disabled"))
    @contextmanager
    def unavailable():raise OperationalError("test",{},Exception("CANARY"));yield
    monkeypatch.setattr(service,"session_scope",unavailable)
    request=Request({"type":"http","method":"POST","path":"/api/auth/logout","headers":[],"session":{"identity":{"session_id":"abc"}}})
    from app.auth.audit_errors import AuthSessionStoreUnavailable
    with pytest.raises(AuthSessionStoreUnavailable):
        service.clear_identity(request)
    assert request.session != {}


def test_failed_preflight_never_starts_workers(monkeypatch,tmp_path):
    import asyncio
    from types import SimpleNamespace
    from app import main
    from app.config import Settings
    settings=Settings(_env_file=None,data_dir=tmp_path,audit_stdout_mode="durable")
    monkeypatch.setattr("app.config.get_settings",lambda:settings)
    monkeypatch.setattr(main,"get_settings",lambda:settings)
    calls=[]
    runtime=SimpleNamespace(available=False,start=lambda:calls.append("start"),stop=lambda:calls.append("stop"))
    monkeypatch.setattr("app.services.diagnostics.runtime.initialize_diagnostics",lambda:runtime)
    monkeypatch.setattr("app.services.audit_runtime.validate_existing_audit",lambda:None)
    monkeypatch.setattr("app.services.audit_runtime.validate_audit_transport_profile",lambda *a:None)
    def denied(*a):raise ValueError("Audit permissions required")
    monkeypatch.setattr("app.services.audit_runtime.preflight_audit",denied)
    monkeypatch.setattr("app.services.job_queue.get_job_queue",lambda:SimpleNamespace(recover_after_restart=lambda:None))
    monkeypatch.setattr("app.services.export_queue.get_export_queue",lambda:SimpleNamespace(recover_after_restart=lambda:None))
    monkeypatch.setattr(main,"VectorStore",lambda:SimpleNamespace(ensure_collection=lambda:None))
    async def start():
        async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):pass
    with pytest.raises(ValueError,match="Audit permissions"):
        asyncio.run(start())
    assert "start" not in calls
    assert calls==["stop"]
