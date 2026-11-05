"""Bounded own SQL row gate; unknown acknowledgements cannot duplicate actors."""
import copy
import importlib.util
from pathlib import Path
import sys
from uuid import UUID

import pytest


def tool():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_provider_gate.py'
    assert path.is_file(),'Bounded owned provider gate missing'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('provider_gate_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def inputs():
    value=dict(recipe='owned-managed-s3-dependencies-v1',profile='basic',deployment_mode='distributed',artifact_backend='s3',
        context='kind-okf-policy',run_id='gate20261105',namespace='replica-test-gate20261105',namespace_uid=str(UUID(int=1)),
        installation_id='a'*32,runtime_fingerprint='b'*64,application_release='app',storage_release='storage')
    api=dict(metadata=dict(name='api-owned',namespace=value['namespace'],uid=str(UUID(int=2)),
        labels={'okf-role':'api','app.kubernetes.io/instance':'app'}),spec={'nodeName':'okf-policy-control-plane'},
        status=dict(phase='Running',containerStatuses=[dict(containerID='containerd://'+'c'*64,imageID='sha256:'+'d'*64,ready=True,restartCount=0)]))
    return value,'e'*32,api,5


class Backend:
    def __init__(self):self.calls=[];self.record=None;self.lost=None
    def __call__(self,action,binding):
        self.calls.append(action)
        if action=='start':
            assert self.record is None
            proof=dict(native_process=dict(pid=41,start_ticks='99',uid=1000,argv_sha256='f'*64),
                pg_pid=77,pg_started_at='2026-11-05T10:00:00+00:00',maximum_seconds=180)
            self.record=dict(binding=copy.deepcopy(binding),proof=proof,phase='ready',remaining_seconds=90,native_alive=True)
        if action=='release':
            self.record.update(phase='closed',native_alive=False,sql_backend_gone=True,rolled_back=True,
                reason='explicit-release',closed_at='2026-11-05T10:00:45+00:00')
        if action==self.lost:raise OSError('PRIVATE_GATE_CANARY')
        return copy.deepcopy(self.record)


def gate(tmp_path):
    m=tool();value,operation,api,epoch=inputs();backend=Backend()
    actions=m.GateActions(value,tmp_path,operation,lambda:True,backend=backend)
    return m,actions,backend,api,epoch


@pytest.mark.parametrize('change',['namespace','context','profile','mode','role','restart','epoch'])
def test_gate_scope_refuses_foreign_or_unstable_inputs_before_actor_creation(change):
    m=tool();value,operation,api,epoch=inputs()
    if change=='namespace':value['namespace']='production'
    if change=='context':value['context']='customer-cluster'
    if change=='profile':value['profile']='resilient'
    if change=='mode':value['artifact_backend']='local'
    if change=='role':api['metadata']['labels']['okf-role']='worker'
    if change=='restart':api['status']['containerStatuses'][0]['restartCount']=1
    if change=='epoch':epoch=True
    with pytest.raises(ValueError):m.gate_scope(value,operation,api,epoch)


def test_gate_uses_literal_bounded_scope_and_never_accepts_arbitrary_sql():
    m=tool();value,operation,api,epoch=inputs();before=copy.deepcopy(api)
    result=m.gate_scope(value,operation,api,epoch)
    assert api==before and result['maximum_seconds']==180 and result['namespace_uid']==value['namespace_uid']
    assert result['api_pod_uid']==api['metadata']['uid'] and result['epoch']==5
    assert not {'sql','module','command'}&set(result)


def test_unknown_gate_start_ack_only_adopts_the_same_live_actor(tmp_path):
    _,g,b,api,epoch=gate(tmp_path);b.lost='start'
    with pytest.raises(OSError):g.start(api,epoch)
    b.lost=None
    assert g.start(api,epoch)['passed'] is True
    assert b.calls==['start','observe']


def test_unknown_gate_release_ack_never_sends_another_release(tmp_path):
    _,g,b,api,epoch=gate(tmp_path);g.start(api,epoch);b.lost='release'
    with pytest.raises(OSError):g.release()
    b.lost=None
    assert g.release()['passed'] is True
    assert b.calls==['start','release','observe']


def test_a_started_gate_can_be_observed_when_controlled_outage_removes_readiness(tmp_path):
    _,g,b,api,epoch=gate(tmp_path);g.start(api,epoch)
    api['status']['containerStatuses'][0]['ready']=False
    assert g.start(api,epoch)['passed'] is True
    assert b.calls==['start','observe']


def test_a_new_gate_on_an_unready_api_has_no_intent_or_actor(tmp_path):
    _,g,b,api,epoch=gate(tmp_path);api['status']['containerStatuses'][0]['ready']=False
    with pytest.raises(ValueError):g.start(api,epoch)
    assert b.calls==[] and not g.files.path('qdrant.gate.start.intent.json').exists()


@pytest.mark.parametrize('change',['process','expired','rollback','reason','scope'])
def test_gate_refuses_replacement_expiry_or_unproved_rollback(tmp_path,change):
    _,g,b,api,epoch=gate(tmp_path);g.start(api,epoch)
    if change=='process':b.record['proof']['native_process']['pid']=42
    if change=='expired':b.record['remaining_seconds']=0
    if change=='scope':b.record['binding']['namespace_uid']=str(UUID(int=999))
    if change in {'rollback','reason'}:
        g.release()
        if change=='rollback':b.record['rolled_back']=False
        else:b.record['reason']='expired'
        with pytest.raises(ValueError):g.release()
    else:
        with pytest.raises(ValueError):g.observe()


def claim_inputs():
    work=dict(id='1'*32,doc_id='2'*16,kind='document',status='running',attempts=1,fence=1,
        owner_id='worker-owner/0',lease_until='2026-11-05T10:02:00+00:00',cancel_requested=False)
    proof=dict(pg_pid=77,pg_started_at='2026-11-05T10:00:00+00:00')
    probe=dict(work=copy.deepcopy(work),now='2026-11-05T10:00:30+00:00',model_owners=0,running_documents=1,running_chats=0,
        gate_pid=77,gate_started_at=proof['pg_started_at'],blocked=[dict(client_addr='10.246.0.10',blocked_by=[77])])
    return work,probe,proof,{'10.246.0.10','10.246.0.11'}


def test_only_a_real_owned_claim_blocked_by_the_exact_gate_can_precede_a_fault():
    m=tool();args=claim_inputs();result=m.assert_admitted_claim(*args)
    assert result['work_id']==args[0]['id'] and result['gate_pg_pid']==77


@pytest.mark.parametrize('change',['queued','expired','foreign-waiter','no-waiter','wrong-gate','changed-owner','model-owner','chat'])
def test_unclaimed_stale_or_unrelated_work_cannot_be_called_an_active_outage(change):
    m=tool();work,probe,proof,ips=claim_inputs()
    if change=='queued':work.update(status='queued',attempts=0,fence=0)
    if change=='expired':probe['now']='2026-11-05T10:02:00+00:00'
    if change=='foreign-waiter':probe['blocked'][0]['client_addr']='10.246.0.99'
    if change=='no-waiter':probe['blocked']=[]
    if change=='wrong-gate':probe['gate_started_at']='2026-11-05T09:00:00+00:00'
    if change=='changed-owner':probe['work']['owner_id']='other-owner'
    if change=='model-owner':probe['model_owners']=1
    if change=='chat':probe['running_chats']=1
    with pytest.raises(ValueError):m.assert_admitted_claim(work,probe,proof,ips)


@pytest.mark.parametrize('change',[None,'late','not-rollback','wrong-gate'])
def test_gate_must_really_rollback_before_the_captured_work_lease_expires(change):
    m=tool();work,_,proof,_=claim_inputs()
    claim=dict(work_id=work['id'],owner_id=work['owner_id'],fence=1,lease_until=work['lease_until'],gate_pg_pid=proof['pg_pid'])
    closed=dict(proof=proof,closure=dict(closed_at='2026-11-05T10:01:00+00:00',rolled_back=True,
        reason='explicit-release',sql_backend_gone=True,native_process_gone=True))
    if change=='late':closed['closure']['closed_at']=work['lease_until']
    if change=='not-rollback':closed['closure']['rolled_back']=False
    if change=='wrong-gate':closed['proof']['pg_pid']=78
    if change is None:assert m.assert_released_claim(claim,closed) is None
    else:
        with pytest.raises(ValueError):m.assert_released_claim(claim,closed)


def test_observing_a_release_that_never_started_cannot_create_a_marker(tmp_path):
    _,g,b,api,epoch=gate(tmp_path);g.start(api,epoch);before=list(b.calls)
    assert g.observe_release() is None
    assert b.calls==before and not g.files.path('qdrant.gate.release.intent.json').exists()


def test_release_observer_adopts_only_an_existing_explicit_release(tmp_path):
    _,g,b,api,epoch=gate(tmp_path);g.start(api,epoch);g.release()
    assert g.observe_release()['passed'] is True
    assert b.calls==['start','release','observe']
