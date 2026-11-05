"""A lost provider-fault acknowledgement must never start another mutation."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest


STAGES=('preflight','baseline','qdrant.inject','qdrant.submit','qdrant.observe','qdrant.heal','qdrant.verify',
    'model.inject','model.submit','model.observe','model.heal','model.verify','final')


def module():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_provider_outage.py'
    assert path.is_file(),'Owned provider coordinator is missing'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('provider_outage_test',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    assert result.STAGES==STAGES
    return result


class Adapter:
    def __init__(self,private):
        self.private=private;self.calls=[];self.observations=[];self.fail=None;self.pending=None
        self.bound='scope-a';self.block=False;self.seen=None

    def bind(self,state):return {'scope':self.bound,'admission':{'backup_id':'f'*32}}

    def before(self,stage,state):
        if self.block:raise ValueError('PRIVATE_BINDING_CANARY')

    def execute(self,stage,state):
        self.calls.append((stage,state['active']['intent_id']))
        if stage==self.fail:raise OSError('PRIVATE_PROVIDER_CANARY')
        if stage==self.pending:return None
        return {'passed':True,'evidence':{'stage':stage,'private':'PRIVATE_PROVIDER_CANARY'}}

    def observe(self,stage,state):
        self.observations.append((stage,state['active']['intent_id']))
        return copy.deepcopy(self.seen)


def run(tmp_path):
    tool=module();a=Adapter(tmp_path)
    return tool,a,tool.ProviderRun(tmp_path/'provider.json',a,code_sha256='a'*64)


def through(current,last):
    for stage in STAGES[:STAGES.index(last)+1]:current.execute(stage,apply=True)


def test_every_fixed_stage_is_required_and_public_output_excludes_private_results(tmp_path):
    _,a,current=run(tmp_path)
    for stage in STAGES:
        report=current.execute(stage,apply=True)
        assert report['selected_provider_case_passed'] is (stage=='final')
    assert [s for s,_ in a.calls]==list(STAGES)
    assert report['failed']==report['skipped_required']==0
    assert not report['qualification_complete'] and report['certification'] is None
    assert 'PRIVATE_PROVIDER_CANARY' not in json.dumps(report)


@pytest.mark.parametrize('stage',['qdrant.inject','model.submit','final','../inject'])
def test_out_of_order_stage_has_no_side_effect(tmp_path,stage):
    _,a,current=run(tmp_path)
    with pytest.raises(ValueError):current.execute(stage,apply=True)
    assert not a.calls


@pytest.mark.parametrize('stage',['baseline','qdrant.inject','qdrant.submit','qdrant.heal','final'])
def test_new_mutation_requires_explicit_apply(tmp_path,stage):
    _,a,current=run(tmp_path)
    if stage!='baseline':through(current,STAGES[STAGES.index(stage)-1])
    else:current.execute('preflight')
    before=list(a.calls)
    with pytest.raises(ValueError):current.execute(stage)
    assert a.calls==before


@pytest.mark.parametrize('stage',['qdrant.inject','qdrant.submit','qdrant.heal'])
def test_unknown_create_post_or_delete_ack_only_observes_the_original_intent(tmp_path,stage):
    tool,a,current=run(tmp_path);through(current,STAGES[STAGES.index(stage)-1]);a.fail=stage
    with pytest.raises(ValueError):current.execute(stage,apply=True)
    stored=json.loads(current.path.read_text());intent=stored['active']['intent_id'];calls=list(a.calls)
    assert stored['active']['error_type']=='OSError' and 'PRIVATE_PROVIDER_CANARY' not in json.dumps(stored['active'])
    a.fail=None
    for _ in range(2):
        current=tool.ProviderRun(current.path,a,code_sha256='a'*64)
        report=current.execute(stage,apply=True)
        assert not report['selected_provider_case_passed'] and a.calls==calls
    assert a.observations[-1]==(stage,intent)
    a.seen={'passed':True,'evidence':{'observed_same_intent':True}}
    report=current.execute(stage,observe=True)
    assert report['checks'][STAGES.index(stage)]['status']=='passed' and a.calls==calls


def test_readonly_pending_observation_is_not_reexecuted(tmp_path):
    tool,a,current=run(tmp_path);through(current,'qdrant.submit');a.pending='qdrant.observe'
    current.execute('qdrant.observe');calls=list(a.calls)
    current=tool.ProviderRun(current.path,a,code_sha256='a'*64)
    current.execute('qdrant.observe',observe=True)
    assert a.calls==calls and a.observations[-1][0]=='qdrant.observe'


def test_completed_action_returns_recorded_result_without_new_dispatch(tmp_path):
    _,a,current=run(tmp_path);through(current,'qdrant.inject');calls=list(a.calls)
    current.execute('qdrant.inject',apply=True)
    assert a.calls==calls


@pytest.mark.parametrize('change',['binding','code'])
def test_changed_input_or_source_cannot_resume_an_old_operation(tmp_path,change):
    tool,a,current=run(tmp_path);through(current,'baseline')
    if change=='binding':a.bound='other-namespace'
    with pytest.raises(ValueError):tool.ProviderRun(current.path,a,code_sha256=('b' if change=='code' else 'a')*64)


def test_live_guard_refusal_precedes_intent_or_action(tmp_path):
    _,a,current=run(tmp_path);through(current,'baseline');before=current.path.read_bytes();a.block=True
    with pytest.raises(ValueError):current.execute('qdrant.inject',apply=True)
    assert current.path.read_bytes()==before and [s for s,_ in a.calls]==['preflight','baseline']


def test_observation_without_a_started_intent_cannot_execute_anything(tmp_path):
    _,a,current=run(tmp_path)
    with pytest.raises(ValueError):current.execute('preflight',observe=True)
    assert not a.calls and not a.observations


def test_injected_completed_history_is_rejected_before_a_dispatch(tmp_path):
    tool,a,current=run(tmp_path);through(current,'baseline');state=json.loads(current.path.read_text())
    state['completed']['final']=state['completed']['preflight'];current.path.write_text(json.dumps(state))
    with pytest.raises(ValueError):tool.ProviderRun(current.path,a,code_sha256='a'*64)


def test_pending_or_failed_result_cannot_complete_a_stage(tmp_path):
    _,a,current=run(tmp_path);current.execute('preflight');a.pending='baseline'
    current.execute('baseline',apply=True)
    a.seen={'passed':False,'evidence':{'fake_ready':True}}
    report=current.execute('baseline',observe=True)
    assert report['skipped_required']==len(STAGES)-1 and current.state['active']['stage']=='baseline'


class Kube:
    def __init__(self):
        self.object=None;self.calls=[];self.lost=None;self.accept=True

    def run(self,*args,source=None):
        self.calls.append((args,source))
        if args[0]=='get':return json.dumps(self.object) if self.object else ''
        if args[0]=='create':
            if self.accept:
                self.object=json.loads(source);self.object['metadata']['uid']='12345678-1234-1234-1234-123456789abc'
            if self.lost=='create':raise OSError('lost-create-ack')
            return json.dumps(self.object)
        assert args[:2]==('delete','--raw') and '?' not in args[2]
        options=json.loads(source)
        assert options==dict(apiVersion='v1',kind='DeleteOptions',preconditions={'uid':self.object['metadata']['uid']})
        if self.accept:self.object=None
        if self.lost=='delete':raise OSError('lost-delete-ack')
        return json.dumps(dict(kind='Status',status='Success'))


def policy(tmp_path):
    from tests.test_replica_provider_faults import value
    tool=module();k=Kube();tmp_path.chmod(0o700);guard=[]
    actor=tool.PolicyActions(k,value(),tmp_path,'f'*32,lambda:guard.append('checked'))
    return actor,k,guard


def test_policy_create_and_uid_delete_are_each_submitted_once(tmp_path):
    actor,k,guard=policy(tmp_path)
    created=actor.create('qdrant');assert created['passed'] and created['evidence']['policy_uid']==k.object['metadata']['uid']
    assert actor.create('qdrant')==created
    assert actor.heal('qdrant')['passed'] and actor.heal('qdrant')['passed']
    assert len([a for a,_ in k.calls if a[0]=='create'])==len([a for a,_ in k.calls if a[0]=='delete'])==1
    assert len(guard)>=4


@pytest.mark.parametrize('accepted',[False,True])
def test_lost_create_ack_adopts_only_actual_exact_object_without_second_create(tmp_path,accepted):
    actor,k,_=policy(tmp_path);k.lost='create';k.accept=accepted
    with pytest.raises(OSError):actor.create('model')
    result=actor.create('model')
    assert (result is not None)==accepted
    assert len([a for a,_ in k.calls if a[0]=='create'])==1


@pytest.mark.parametrize('fault',['uid','spec','owner'])
def test_changed_policy_cannot_be_deleted(tmp_path,fault):
    actor,k,_=policy(tmp_path);actor.create('qdrant')
    if fault=='uid':k.object['metadata']['uid']='22345678-1234-1234-1234-123456789abc'
    elif fault=='spec':k.object['spec']['egressDeny']=[]
    else:k.object['metadata']['ownerReferences']=[{'uid':'foreign-owner'}]
    with pytest.raises(ValueError):actor.heal('qdrant')
    assert not [a for a,_ in k.calls if a[0]=='delete']


def test_preexisting_object_is_not_adopted_as_a_new_fault(tmp_path):
    from tests.test_replica_provider_faults import value
    from test_scripts.replica_provider_faults import fault_policy
    actor,k,_=policy(tmp_path);k.object=fault_policy(value(),'model','f'*32)
    k.object['metadata']['uid']='12345678-1234-1234-1234-123456789abc'
    with pytest.raises(ValueError):actor.create('model')
    assert not [a for a,_ in k.calls if a[0]=='create']


@pytest.mark.parametrize('accepted',[False,True])
def test_lost_delete_ack_observes_absence_without_resending_delete(tmp_path,accepted):
    actor,k,_=policy(tmp_path);actor.create('model');k.lost='delete';k.accept=accepted
    with pytest.raises(OSError):actor.heal('model')
    assert (actor.heal('model') is not None)==accepted
    assert len([a for a,_ in k.calls if a[0]=='delete'])==1


def test_live_scope_guard_runs_before_every_mutation_and_observation(tmp_path):
    actor,k,_=policy(tmp_path);actor.create('model');before=len(k.calls)
    def refuse():raise ValueError('foreign-namespace-or-pod')
    actor.guard=refuse
    for method in [actor.create,actor.observe_create,actor.heal,actor.observe_heal]:
        with pytest.raises(ValueError):method('model')
    assert len(k.calls)==before


def test_disappearance_before_a_heal_intent_cannot_forge_owned_cleanup(tmp_path):
    actor,k,_=policy(tmp_path);actor.create('qdrant');k.object=None
    with pytest.raises(ValueError):actor.heal('qdrant')
    assert not [a for a,_ in k.calls if a[0]=='delete']


def test_changing_protected_io_implementation_invalidates_the_recipe_digest(tmp_path):
    tool=module();tool.ROOT=tmp_path
    names=['scripts/kubernetes/replica_provider_outage.py','scripts/kubernetes/replica_runtime_run.py',
        'backend/test_scripts/replica_provider_faults.py','backend/test_scripts/replica_frontend_http.py',
        'scripts/kubernetes/replica_provider_native.py',
        'backend/app/deployment/replica_backup.py','backend/app/services/diagnostics/paths.py']
    for name in names:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original protected implementation')
    before=tool.code_digest()
    for name in names[-3:]:
        path=tmp_path/name;path.write_text('changed alias or write-marker behavior')
        assert tool.code_digest()!=before
        path.write_text('original protected implementation')


@pytest.mark.parametrize('name',['scripts/kubernetes/replica_fault_authority.py',
    'scripts/kubernetes/replica_image_upgrade.py','backend/test_scripts/replica_runtime_acceptance.py'])
def test_changed_native_authority_or_transport_cannot_resume_old_intents(tmp_path,name):
    tool=module();tool.ROOT=tmp_path
    names=['scripts/kubernetes/replica_provider_outage.py','scripts/kubernetes/replica_runtime_run.py',
        'backend/test_scripts/replica_provider_faults.py','backend/test_scripts/replica_frontend_http.py',
        'scripts/kubernetes/replica_provider_native.py','backend/app/deployment/replica_backup.py',
        'backend/app/services/diagnostics/paths.py',name]
    for item in names:
        path=tmp_path/item;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('original')
    before=tool.code_digest();(tmp_path/name).write_text('different authority behavior')
    assert tool.code_digest()!=before
