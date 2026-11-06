"""A retained call may enter physical recovery only from an exact live proof."""
import copy
import importlib.util
from pathlib import Path
import sys
from uuid import UUID

import pytest

from tests.test_replica_provider_outage import run, through


def tool():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_model_recovery.py'
    assert path.is_file(),'Owned model quarantine recovery missing'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('model_recovery_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def case(tmp_path):
    _,adapter,current=run(tmp_path)
    doc='1'*16
    pub=dict(doc_id=doc,active_generation_id='2'*32,active_count=1,concept_sha256='3'*64,original_sha256='4'*64)
    old=dict(id='5'*32,kind='document',doc_id=doc,status='succeeded',attempts=2,fence=2,error_code=None,
        cancel_requested=False,owner_id=None,lease_until=None)
    work=dict(old,id='6'*32,status='running',attempts=1,fence=1,owner_id='worker/0',lease_until='2026-11-05T14:02:00+00:00')
    base=dict(publication=pub,works=[old],corpus=[{'id':doc}],maintenance=dict(enabled=False,epoch=5),model_owners=0,running=0,chats=0)
    provider=dict(test_only=True,active=0,started=5,finished=5,peak_active=1)
    policy=dict(policy_uid=str(UUID(int=20)),provider='model',policy_sha256='d'*64)
    network=dict(provider='model',blocked_pods=4,positive_controls=12)
    outcomes={'qdrant.verify':dict(passed=True,database=base),
        'model.inject':dict(passed=True,provider=provider,policy=policy,network=network),
        'model.submit':dict(passed=True,work_id=work['id'])}
    adapter.execute=lambda stage,state:copy.deepcopy(outcomes.get(stage,dict(passed=True)))
    through(current,'model.submit')
    adapter.execute=lambda *args:None
    current.execute('model.observe',apply=True)
    expected=dict(namespace_uid=str(UUID(int=1)),runtime_fingerprint='b'*64,doc_id=doc,
        api_uids={str(UUID(int=2)),str(UUID(int=3))})
    database=copy.deepcopy(base);database.update(works=[old,work],model_owners=1,running=1)
    proof=dict(phase='quarantine-proven',execution_id=current.state['execution_id'],native_code_sha256=current.state['code_sha256'],
        core_sha256='c'*64,binding_sha256=current.state['binding_sha256'],namespace_uid=expected['namespace_uid'],
        fingerprint=expected['runtime_fingerprint'],policy=policy,network=network,
        slot=dict(id='7'*32,resource='llm',slot=0,owner_id='8'*32,fence=11,
            expires_at='2026-11-05T13:59:00+00:00',protected_until='2026-11-05T13:59:00+00:00',release_required=True),
        db_now='2026-11-05T14:00:00+00:00',work=work,database=database,provider=provider,
        api_admissions=[dict(pod_uid=uid,busy=True,provider_invoked=False) for uid in sorted(expected['api_uids'])],
        publication_preserved=True,slot_expired_but_retained=True,no_new_provider_requests=True,
        provider_reconciliation_performed=False,qualification_complete=False)
    return current,proof,expected


def test_admission_preserves_history_and_exact_token_without_claiming_a_worker_crash(tmp_path):
    current,proof,expected=case(tmp_path);module=tool()
    before=copy.deepcopy((current.state,proof,expected))
    result=module.admit_quarantine(current.state,proof,parent_sha256='c'*64,expected=expected)
    assert result['work']==proof['work'] and result['slot']==proof['slot']
    assert result['source_epoch']==5 and result['parent_execution_id']==current.state['execution_id']
    assert (current.state,proof,expected)==before
    assert 'crash_receipt' not in result and 'reconciled' not in result


def test_queued_retry_with_the_same_uncertain_token_is_admitted(tmp_path):
    current,proof,expected=case(tmp_path);module=tool()
    proof['work'].update(status='queued',owner_id=None,lease_until=None,error_code='model_unavailable')
    proof['database']['running']=0
    assert module.admit_quarantine(current.state,proof,parent_sha256='c'*64,expected=expected)['work']['status']=='queued'


def test_absent_parent_model_observation_intent_is_rejected(tmp_path):
    current,proof,expected=case(tmp_path);module=tool()
    current.state['active']=None
    with pytest.raises(ValueError):module.admit_quarantine(current.state,proof,parent_sha256='c'*64,expected=expected)


@pytest.mark.parametrize('fault',['parent-hash','execution','code','binding','namespace','fingerprint','work',
    'publication','policy','network','slot-owner','release-required','unexpired','provider','duplicate-api',
    'missing-api','busy','provider-invoked','reconciled','history'])
def test_incomplete_or_foreign_quarantine_never_authorizes_recovery(tmp_path,fault):
    current,proof,expected=case(tmp_path);module=tool();parent=copy.deepcopy(current.state)
    if fault=='parent-hash':proof['core_sha256']='d'*64
    elif fault=='execution':proof['execution_id']='d'*32
    elif fault=='code':proof['native_code_sha256']='d'*64
    elif fault=='binding':proof['binding_sha256']='d'*64
    elif fault=='namespace':proof['namespace_uid']=str(UUID(int=99))
    elif fault=='fingerprint':proof['fingerprint']='d'*64
    elif fault=='work':proof['work']={**proof['work'],'id':'d'*32}
    elif fault=='publication':proof['database']['publication']['concept_sha256']='d'*64
    elif fault=='policy':proof['policy']={**proof['policy'],'policy_uid':str(UUID(int=99))}
    elif fault=='network':proof['network']={**proof['network'],'blocked_pods':3}
    elif fault=='slot-owner':proof['slot']['owner_id']=None
    elif fault=='release-required':proof['slot']['release_required']=False
    elif fault=='unexpired':proof['slot']['expires_at']='2026-11-05T14:01:00+00:00'
    elif fault=='provider':proof['provider']={**proof['provider'],'started':6}
    elif fault=='duplicate-api':proof['api_admissions'][1]=copy.deepcopy(proof['api_admissions'][0])
    elif fault=='missing-api':proof['api_admissions'].pop()
    elif fault=='busy':proof['api_admissions'][0]['busy']=False
    elif fault=='provider-invoked':proof['api_admissions'][0]['provider_invoked']=True
    elif fault=='reconciled':proof['provider_reconciliation_performed']=True
    else:parent['completed']['model.inject']['outcome']['policy']['policy_uid']=str(UUID(int=99))
    with pytest.raises(ValueError):module.admit_quarantine(parent,proof,parent_sha256='c'*64,expected=expected)


def coordinator(module, phase):
    assert hasattr(module,'ModelRecovery'),'Physical recovery coordinator missing'
    value=module.ModelRecovery.__new__(module.ModelRecovery)
    value.journal=dict(operation_kind='owned-model-quarantine-recovery-v1',phase=phase,
        proof={'observation_sha256':'f'*64},binding={},original_lease={'owner_id':'retained'})
    calls=[]
    value._guard_parent=lambda:calls.append('parent')
    value._heal_model_policy=lambda:calls.append('heal')
    value.fetch_stop=lambda:None
    return value,calls


def test_model_recovery_cannot_inject_a_worker_crash_or_another_request(monkeypatch):
    module=tool();value,_=coordinator(module,'quarantine-proven')
    monkeypatch.setattr(module.WorkerLoss,'inject',lambda self:pytest.fail('Crash injection called'))
    with pytest.raises(ValueError):value.inject()


@pytest.mark.parametrize('phase',['prepared','released','running','verified'])
def test_wrong_recovery_phase_never_calls_existing_mutating_recovery(monkeypatch,phase):
    module=tool();value,_=coordinator(module,phase)
    monkeypatch.setattr(module.WorkerLoss,'recover',lambda self:pytest.fail('Unauthorised recovery called'))
    with pytest.raises(ValueError):value.recover()


def test_exact_parent_is_checked_before_delegating_audited_recovery(monkeypatch):
    module=tool();value,calls=coordinator(module,'quarantine-proven')
    monkeypatch.setattr(module.WorkerLoss,'recover',lambda self:calls.append('audited-recovery'))
    value.recover()
    assert calls==['parent','audited-recovery'] and value.journal['original_lease']['owner_id']=='retained'


@pytest.mark.parametrize('same',[True,False])
def test_physical_proof_is_reobserved_before_policy_removal_and_release(monkeypatch,same):
    module=tool();value,calls=coordinator(module,'reconciled')
    class Observer:
        def __init__(self,*args):pass
        def digest(self):calls.append('physical-proof');return 'f'*64 if same else 'e'*64
    monkeypatch.setattr(module,'StopObservation',Observer)
    monkeypatch.setattr(module.WorkerLoss,'release',lambda self:calls.append('release'))
    value.value={}
    if same:
        value.release();assert calls==['parent','physical-proof','heal','release']
    else:
        with pytest.raises(ValueError):value.release()
        assert calls==['parent','physical-proof']


def test_existing_worker_loss_summary_keeps_its_actual_crash_evidence():
    module=tool();value=module.WorkerLoss.__new__(module.WorkerLoss)
    value.journal={'crash_receipt':{'stopped_containers':2}}
    assert value.fault_summary()=={'crashed_worker_containers':2,'provider_active_after_crash':1}


def test_model_summary_never_fabricates_worker_crash_evidence():
    module=tool();value,_=coordinator(module,'running')
    assert value.fault_summary()==dict(fault_kind='model-connectivity-loss',worker_crash_injected=False,
        provider_requests_started_during_fault=0)


def test_default_worker_verification_authentication_is_preserved():
    module=tool();value=module.WorkerLoss.__new__(module.WorkerLoss);calls=[]
    class Response:
        def raise_for_status(self):calls.append('status-checked')
    class Client:
        def post(self,*args,**kwargs):calls.append((args,kwargs));return Response()
    value.authenticate_verification_client(Client())
    assert calls==[(('/api/auth/simulate',),{'json':{'username':'replica.admin'}}),'status-checked']


def test_failed_default_authentication_still_refuses_verification():
    module=tool();value=module.WorkerLoss.__new__(module.WorkerLoss)
    class Response:
        def raise_for_status(self):raise RuntimeError('Authentication rejected')
    class Client:
        def post(self,*args,**kwargs):return Response()
    with pytest.raises(RuntimeError):value.authenticate_verification_client(Client())


def test_model_verification_uses_retained_parent_identity_without_another_login():
    module=tool();value,_=coordinator(module,'running');calls=[]
    class Response:
        status_code=200
        def json(self):return {'user':{'username':'replica.admin'}}
    class Client:
        def post(self,*args,**kwargs):pytest.fail('Must not create another session')
        def get(self,path):calls.append(path);return Response()
    value.authenticate_verification_client(Client())
    assert calls==['/api/auth/me']


def logout_case(module, *, lose_ack=False, denied=True, drift=False, valid_export=True):
    from hashlib import sha256
    value,_=coordinator(module,'running')
    value.parent={'execution_id':'a'*32}
    value.session={'cookie_sha256':'b'*64}
    value.journal['document']={'id':'c'*16}
    value.manifest={'provider_fault':{'checks':dict(export_job=2,export_part=1,export_sha256=sha256(b'frozen-zip').hexdigest())}}
    state={'revoked':False,'posts':0};calls=[]
    value.save=lambda **updates:value.journal.update(copy.deepcopy(updates))
    value._final_snapshot=lambda:{'publication':'different' if drift and state['revoked'] else 'same'}
    class Response:
        def __init__(self,status,body=None,content=b''):self.status_code,self.body,self.content=status,body,content
        def json(self):return self.body
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def get(self,path):
            calls.append(('GET',path))
            if path=='/api/auth/me':
                user=dict(user_id='anonymous',username='anonymous',roles=[],groups=[]) if state['revoked'] else {'username':'replica.admin'}
                return Response(200,dict(mode='simulation',user=user))
            if path.startswith('/api/jobs/'):
                return Response(200,content=b'frozen-zip' if valid_export else b'wrong-export')
            assert path=='/api/documents/'+'c'*16+'/download'
            return Response(401 if denied else 200)
        def post(self,path,**kwargs):
            assert path=='/api/auth/logout' and value.journal['logout_intent']
            calls.append(('POST',path));state['posts']+=1;state['revoked']=True
            if lose_ack:raise OSError('Lost logout acknowledgement')
            return Response(200)
    value.verification_client=lambda:Client()
    return value,state,calls


def test_recovery_logout_lost_ack_is_observed_without_another_post():
    module=tool();value,state,_=logout_case(module,lose_ack=True)
    with pytest.raises(OSError):value._finish_logout({'recovery':'verified'})
    result=value._finish_logout(value.journal['functional_report'])
    assert state['posts']==1 and result['session']['session_revoked'] is True
    assert value.journal['functional_snapshot']=={'publication':'same'}


@pytest.mark.parametrize('fault',['download','snapshot','export'])
def test_recovery_logout_refuses_granted_access_drift_or_wrong_frozen_export(fault):
    module=tool();value,state,_=logout_case(module,denied=fault!='download',drift=fault=='snapshot',valid_export=fault!='export')
    with pytest.raises(ValueError):value._finish_logout({'recovery':'verified'})
    assert not value.journal.get('logout_receipt')
    assert state['posts']==(0 if fault=='export' else 1)
    if fault!='export':
        with pytest.raises(ValueError):value._finish_logout(value.journal['functional_report'])
        assert state['posts']==1


@pytest.mark.parametrize('phase,apply,observe',[('recover',False,False),('adopt',False,True),('verify',False,True)])
def test_dispatch_cannot_start_mutation_or_logout_as_an_observer(tmp_path,phase,apply,observe):
    from types import SimpleNamespace
    module=tool();calls=[]
    recipe=SimpleNamespace(journal=None,path=tmp_path/'journal.json',inputs={},parent={'execution_id':'a'*32},
        recover=lambda:calls.append('recover'),adopt=lambda:calls.append('adopt'),verify=lambda path:calls.append('verify'))
    with pytest.raises(ValueError):module.execute_recovery(recipe,phase,apply=apply,observe=observe,report=tmp_path/('provider-model-recovery-'+'a'*32+'-report.json'))
    assert not calls


def test_dispatch_refuses_report_alias_to_another_operation(tmp_path):
    from types import SimpleNamespace
    module=tool();calls=[]
    recipe=SimpleNamespace(journal={'logout_intent':True},path=tmp_path/'journal.json',inputs={},parent={'execution_id':'a'*32},
        verify=lambda path:calls.append('verify'))
    with pytest.raises(ValueError):module.execute_recovery(recipe,'verify',apply=True,report=tmp_path/'other-operation-report.json')
    assert not calls
