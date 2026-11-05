"""Admit an executing owned document before readiness denies new Qdrant work."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest


def tool():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_provider_native.py'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('gate_admission_native_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def admitted_adapter(tmp_path,monkeypatch):
    m=tool();a=m.NativeAdapter.__new__(m.NativeAdapter);calls=[]
    original=dict(doc_id='1'*16,active_generation_id='2'*32,concept_sha256='3'*64,original_sha256='4'*64,active_count=1)
    old=dict(id='5'*32,doc_id='1'*16,kind='document',status='succeeded',attempts=1,fence=1,error_code=None,
        owner_id=None,lease_until=None,cancel_requested=False)
    work=dict(old,id='6'*32,status='running',owner_id='worker-owner/0',lease_until='2026-11-05T10:02:00+00:00')
    baseline=dict(publication=original,works=[old],model_owners=0,running=0,chats=0,maintenance={'epoch':5,'enabled':False})
    current=copy.deepcopy(baseline)
    state={'completed':{'baseline':{'outcome':{'database':baseline}}}}
    proof=dict(pg_pid=77,pg_started_at='2026-11-05T10:00:00+00:00')
    probe=dict(work=work,now='2026-11-05T10:00:30+00:00',model_owners=0,running_documents=1,running_chats=0,
        gate_pid=77,gate_started_at=proof['pg_started_at'],blocked=[dict(client_addr='10.246.0.10',blocked_by=[77])])
    closed=dict(passed=True,proof=proof,closure=dict(closed_at='2026-11-05T10:01:00+00:00',rolled_back=True,
        reason='explicit-release',sql_backend_gone=True,native_process_gone=True))
    class Gate:
        def start(self,api,epoch):calls.append('gate.start');assert epoch==5;return dict(passed=True,proof=proof)
        def observe(self):calls.append('gate.observe');return dict(passed=True,proof=proof)
        def binding(self):return {'api_pod':'owned-api'}
        def release(self):calls.append('gate.release');return closed
        def observe_release(self):calls.append('gate.observe-release');return closed
    class Backend:
        def claim_probe(self,binding,gate,work_id):
            calls.append('claim.probe');assert work_id==work['id'];return copy.deepcopy(probe)
    class Policy:
        def create(self,provider):calls.append('policy.create');return dict(evidence={'provider':provider})
        def observe_create(self,provider):calls.append('policy.observe-create');return dict(evidence={'provider':provider})
    a.gate=Gate();a.gate_backend=Backend();a.policy=Policy();a.files=m.PrivateFiles(tmp_path)
    a.database=lambda:copy.deepcopy(current);a.model=lambda:{'active':0};a.positive=lambda:calls.append('positive') or []
    a.network=lambda:calls.append('denial-controls') or [];a.expected_pods={}
    a.gate_api=lambda:{'metadata':{'name':'owned-api'}};a.worker_ips=lambda:{'10.246.0.10','10.246.0.11'}
    def rpc(action,state,*,observe):
        calls.append(('rpc',action,observe))
        if action=='qdrant.submit' and not observe:
            current['works'].append(copy.deepcopy(work));current['running']=1
        return dict(passed=True)
    a.rpc=rpc
    monkeypatch.setattr(m,'assert_network',lambda rows,expected,provider:{'provider':provider})
    return a,state,current,work,probe,closed,calls


def test_qdrant_gate_submit_claim_deny_and_explicit_rollback_order(tmp_path,monkeypatch):
    a,state,current,work,probe,closed,calls=admitted_adapter(tmp_path,monkeypatch)
    state['completed']['qdrant.gate']={'outcome':a.dispatch('qdrant.gate',state,observe=False)}
    admitted=a.dispatch('qdrant.submit',state,observe=False)
    state['completed']['qdrant.submit']={'outcome':admitted};calls.clear()
    result=a.dispatch('qdrant.inject',state,observe=False)
    assert result['passed'] is True and result['admitted_work']['work_id']==work['id']
    assert calls.index('claim.probe')<calls.index('policy.create')<calls.index('denial-controls')<calls.index('gate.release')
    assert not any(isinstance(v,tuple) and v[0]=='rpc' for v in calls)


@pytest.mark.parametrize('fault',['queued','foreign-waiter','model-owner','stale-lease'])
def test_qdrant_policy_is_not_created_without_an_actual_owned_claim(tmp_path,monkeypatch,fault):
    a,state,current,work,probe,_,calls=admitted_adapter(tmp_path,monkeypatch)
    current['works'].append(work);current['running']=1
    state['completed']['qdrant.submit']={'outcome':{'work_id':work['id']}}
    if fault=='queued':work.update(status='queued',attempts=0,fence=0)
    if fault=='foreign-waiter':probe['blocked'][0]['client_addr']='10.246.0.99'
    if fault=='model-owner':current['model_owners']=1
    if fault=='stale-lease':probe['now']=work['lease_until']
    with pytest.raises(ValueError):a.dispatch('qdrant.inject',state,observe=False)
    assert 'policy.create' not in calls and 'gate.release' not in calls


def test_unknown_qdrant_injection_only_observes_policy_and_previous_release(tmp_path,monkeypatch):
    a,state,_,work,_,_,calls=admitted_adapter(tmp_path,monkeypatch)
    a.files.once('qdrant.claim.before-deny.json',dict(work_id=work['id'],owner_id=work['owner_id'],fence=1,
        lease_until=work['lease_until'],gate_pg_pid=77))
    state['completed']['qdrant.submit']={'outcome':{'work_id':work['id']}}
    assert a.dispatch('qdrant.inject',state,observe=True)['passed'] is True
    assert 'policy.observe-create' in calls and 'gate.observe-release' in calls
    assert 'policy.create' not in calls and 'gate.release' not in calls and 'claim.probe' not in calls


def test_expired_gate_release_is_not_accepted_as_a_successful_active_outage(tmp_path,monkeypatch):
    a,state,current,work,_,closed,calls=admitted_adapter(tmp_path,monkeypatch)
    current['works'].append(work);state['completed']['qdrant.submit']={'outcome':{'work_id':work['id']}}
    closed['closure']['closed_at']=work['lease_until']
    with pytest.raises(ValueError):a.dispatch('qdrant.inject',state,observe=False)
    assert 'policy.create' in calls and 'gate.release' in calls
