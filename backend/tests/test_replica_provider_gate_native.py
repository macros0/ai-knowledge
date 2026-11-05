"""Native gate boundary admits only fixed own scope and literal actor sources."""
import importlib.util
import json
from pathlib import Path
import sys
from uuid import UUID

import pytest

def inputs():
    value=dict(recipe='owned-managed-s3-dependencies-v1',profile='basic',deployment_mode='distributed',artifact_backend='s3',
        context='kind-okf-policy',run_id='gate20261105',namespace='replica-test-gate20261105',namespace_uid=str(UUID(int=1)),
        installation_id='a'*32,runtime_fingerprint='b'*64,application_release='app',storage_release='storage')
    api=dict(metadata=dict(name='api-owned',namespace=value['namespace'],uid=str(UUID(int=2)),
        labels={'okf-role':'api','app.kubernetes.io/instance':'app'}),spec={'nodeName':'okf-policy-control-plane'},
        status=dict(phase='Running',containerStatuses=[dict(containerID='containerd://'+'c'*64,imageID='sha256:'+'d'*64,ready=True,restartCount=0)]))
    return value,'e'*32,api,5


def tool():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_provider_gate_native.py'
    assert path.is_file(),'Fixed native gate bridge missing'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('provider_gate_native_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def fixture():
    m=tool();value,operation,api,epoch=inputs();calls=[]
    class Kube:
        def run(self,*args,**kwargs):
            assert args==('get','pod',api['metadata']['name'],'-o','json')
            return json.dumps(api)
    def remote(command,*,source):
        assert command==['python3','-c',m.REMOTE_SOURCE]
        payload=json.loads(source);assert set(payload)=={'action','binding'}
        calls.append(payload);return {'native_boundary':'observed'}
    bridge=m.NativeGate(Kube(),value,operation,remote_call=remote)
    return m,bridge,value,operation,api,epoch,calls


def test_native_bridge_uses_literal_remote_source_and_bound_metadata():
    m,b,value,operation,api,epoch,calls=fixture()
    binding=m.gate_scope(value,operation,api,epoch)
    assert b('start',binding)=={'native_boundary':'observed'}
    assert calls==[{'action':'start','binding':binding}]


@pytest.mark.parametrize('change',['namespace','uid','image','role','restart','arbitrary-sql','action'])
def test_foreign_gate_inputs_are_refused_before_remote_process_creation(change):
    m,b,value,operation,api,epoch,calls=fixture();binding=m.gate_scope(value,operation,api,epoch);action='start'
    if change=='namespace':binding['namespace']='production'
    if change=='uid':binding['api_pod_uid']='00000000-0000-0000-0000-000000000999'
    if change=='image':binding['api_image_id']='sha256:'+'9'*64
    if change=='role':api['metadata']['labels']['okf-role']='worker'
    if change=='restart':api['status']['containerStatuses'][0]['restartCount']=1
    if change=='arbitrary-sql':binding['sql']='DELETE FROM documents'
    if change=='action':action='arbitrary-command'
    with pytest.raises(ValueError):b(action,binding)
    assert calls==[]


def test_unready_observation_cannot_be_misused_as_a_new_actor_start():
    m,b,value,operation,api,epoch,calls=fixture();binding=m.gate_scope(value,operation,api,epoch)
    api['status']['containerStatuses'][0]['ready']=False
    with pytest.raises(ValueError):b('start',binding)
    b('observe',binding);b('release',binding)
    assert [v['action'] for v in calls]==['observe','release']


def test_all_native_sources_compile_and_the_actor_is_bounded_rollback_only():
    m=tool()
    for name in ['ACTOR_SOURCE','SUPERVISOR_SOURCE','REMOTE_SOURCE','CLAIM_SOURCE']:
        compile(getattr(m,name),name,'exec')
    actor=m.ACTOR_SOURCE
    assert "idle_in_transaction_session_timeout='180s'" in actor
    assert 'FOR UPDATE' in actor and 'tx.rollback()' in actor
    assert 'tx.commit()' not in actor and 'session.commit()' not in actor
    assert "'controller-EOF'" in actor and "'expired'" in actor
    assert 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY' in m.CLAIM_SOURCE
    assert 'pg_blocking_pids' in m.CLAIM_SOURCE
