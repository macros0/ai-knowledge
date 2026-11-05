"""A partial automatic branch requires the exact verified physical child."""
import copy
from hashlib import sha256
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
from uuid import UUID

import pytest

from tests.test_replica_provider_outage import run, through
from tests.test_replica_provider_native import pg_inputs


def tool():
    path=Path(__file__).resolve().parents[2]/'scripts/kubernetes/replica_provider_matrix.py'
    assert path.is_file(),'Chained provider matrix producer missing'
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location('provider_matrix_test',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def case(tmp_path):
    from app.services.work.coordination import CapacityLease
    from app.services.work.maintenance_control import REQUIRED_WRITERS
    from app.services.work.provider_reconciliation import ProviderStopProof, leases_digest
    kernel,a,current=run(tmp_path)
    value,authority,pg,pg_report=pg_inputs()
    value.update(namespace='replica-test-matrix20261105',runtime_fingerprint='b'*64)
    pg.update(fingerprint='b'*64,epoch=5)
    pg_report.update(namespace=value['namespace'],runtime_fingerprint='b'*64,maintenance_epoch=5)
    pg_report['stale_return']=dict(same_container_no_restart=True,
        captured_old_owner_fence_publication_probe=dict(old_token_rejected=True,probe_transaction_rolled_back=True))
    a.bind=lambda state:{'scope':'scope-a','admission':authority}
    current=kernel.ProviderRun(current.path,a,code_sha256='a'*64)
    from replica_provider_native import pg_proof
    def doc(identity,gen='2'*32,concept='4'*64):
        return dict(id=identity,status='done',problem=None,deleted_at=None,sha256='3'*64,active_generation_id=gen,concept_sha256=concept)
    docid='1'*16
    pub=dict(doc_id=docid,active_generation_id='2'*32,concept_sha256='4'*64,original_sha256='3'*64,active_count=1)
    old=dict(id='5'*32,kind='document',doc_id=docid,status='succeeded',attempts=1,fence=1,error_code=None,cancel_requested=False,owner_id=None,lease_until=None)
    queued=dict(old,id='6'*32,status='queued',error_code='storage_unavailable')
    qdone=dict(queued,status='succeeded',attempts=2,fence=2,error_code=None)
    mpending=dict(old,id='9'*32,status='running',owner_id='worker/0',lease_until='2026-11-05T14:02:00+00:00')
    mdone=dict(mpending,status='succeeded',attempts=2,fence=2,owner_id=None,lease_until=None)
    base=dict(publication=pub,works=[old],corpus=[doc(docid)]+[doc(c*16) for c in 'abc'],maintenance={'epoch':5,'enabled':False},model_owners=0,running=0,chats=0)
    outage=copy.deepcopy(base);outage['works'].append(queued)
    after=copy.deepcopy(base);after.update(publication={**pub,'active_generation_id':'7'*32,'concept_sha256':'8'*64},works=[old,qdone])
    after['corpus'][0]=doc(docid,'7'*32,'8'*64)
    gate=dict(native_process=dict(pid=12,start_ticks='300',uid=1000,argv_sha256='f'*64),pg_pid=123,pg_started_at='2026-11-05T13:58:00+00:00',maximum_seconds=180)
    claim=dict(work_id=queued['id'],owner_id='worker/0',fence=1,gate_pg_pid=123,lease_until='2026-11-05T14:02:00+00:00')
    closed=dict(proof=gate,closure=dict(closed_at='2026-11-05T14:00:20+00:00',rolled_back=True,reason='explicit-release',sql_backend_gone=True,native_process_gone=True))
    provider=dict(test_only=True,active=0,started=5,finished=5,peak_active=1)
    policy=dict(provider='model',policy_uid=str(UUID(int=40)),policy_sha256='d'*64)
    network=dict(provider='model',blocked_pods=4,positive_controls=12)
    outcomes={'preflight':dict(passed=True,postgres=pg_proof(value,authority,pg,pg_report)),
        'baseline':dict(passed=True,database=base),'qdrant.gate':dict(passed=True,gate=gate),
        'qdrant.submit':dict(passed=True,work_id=queued['id']),
        'qdrant.inject':dict(passed=True,admitted_work=claim,gate=closed,network=dict(provider='qdrant',blocked_pods=4,positive_controls=12)),
        'qdrant.observe':dict(passed=True,queued=queued,database=outage),
        'qdrant.verify':dict(passed=True,database=after,functional={'passed':True}),
        'model.inject':dict(passed=True,provider=provider,policy=policy,network=network),'model.submit':dict(passed=True,work_id=mpending['id'])}
    a.execute=lambda stage,state:copy.deepcopy(outcomes.get(stage,dict(passed=True)))
    through(current,'model.submit');a.execute=lambda *args:None;current.execute('model.observe',apply=True)
    slot=dict(id='e'*32,resource='llm',slot=0,owner_id='f'*32,fence=11,expires_at='2026-11-05T13:59:00+00:00',protected_until='2026-11-05T13:59:00+00:00',release_required=True)
    apis={str(UUID(int=2)),str(UUID(int=3))}
    during=copy.deepcopy(after);during.update(works=[old,qdone,mpending],model_owners=1,running=1)
    quarantine=dict(phase='quarantine-proven',execution_id=current.state['execution_id'],native_code_sha256=current.state['code_sha256'],core_sha256='c'*64,
        binding_sha256=current.state['binding_sha256'],namespace_uid=value['namespace_uid'],fingerprint=value['runtime_fingerprint'],
        slot=slot,db_now='2026-11-05T14:00:00+00:00',work=mpending,database=during,provider=provider,policy=policy,network=network,
        api_admissions=[dict(pod_uid=v,busy=True,provider_invoked=False) for v in sorted(apis)],publication_preserved=True,slot_expired_but_retained=True,
        no_new_provider_requests=True,provider_reconciliation_performed=False,qualification_complete=False)
    lease=CapacityLease(**{k:slot[k] for k in ['id','resource','owner_id','fence']},expires_at=datetime.fromisoformat(slot['expires_at']))
    stop=ProviderStopProof(operation_id='a'*32,installation_id=value['installation_id'],namespace=value['namespace'],namespace_uid=value['namespace_uid'],epoch=6,
        provider_pod_uid=UUID(int=50),provider_image_digest='sha256:'+'d'*64,writer_pod_uids=[UUID(int=i) for i in range(2,8)],
        writers={k:'stopped' for k in REQUIRED_WRITERS},unknown_writers=0,unreachable_nodes=0,provider_pods=0,provider_endpoints=0,
        observation_sha256='e'*64,leases_sha256=leases_digest([lease]))
    final=copy.deepcopy(after);final.update(publication={**pub,'active_generation_id':'d'*32,'concept_sha256':'e'*64},works=[old,qdone,mdone],maintenance={'epoch':6,'enabled':False})
    final['corpus'][0]=doc(docid,'d'*32,'e'*64)
    child=dict(phase='verified',operation_kind='owned-model-quarantine-recovery-v1',parent_execution_id=current.state['execution_id'],operation_id='a'*32,
        input_sha256={'parent':'c'*64},source_epoch=5,epoch=6,original_api_uids=sorted(apis),original_work=mpending,original_lease=slot,
        quarantine_proof=quarantine,proof=stop.model_dump(mode='json'),proof_digest=stop.digest(),functional_snapshot=final,fault_authority=authority)
    report=dict(status='owned-model-quarantine-recovery-passed',**{k:value[k] for k in ['namespace','namespace_uid','installation_id','runtime_fingerprint']},
        operation_id='a'*32,parent_execution_id=current.state['execution_id'],parent_core_sha256='c'*64,source_epoch=5,recovery_epoch=6,
        policy_removed=True,parent_history_unchanged=True,old_export_verified=True,
        session=dict(session_revoked=True,old_cookie_checked=True,parent_session_preserved_across_restart=True),
        reconciliation=dict(proof_sha256=stop.digest(),audit_confirmed=True),
        recovered_document=dict(id=docid,sha256='3'*64,status='done',problem=None,active_publications=1,original_fence=1,recovered_fence=2),
        qualification_complete=False,certification=None)
    return value,authority,pg,pg_report,current.state,child,report


def test_matrix_joins_exact_actual_branches_without_forging_parent_completion(tmp_path):
    args=case(tmp_path);module=tool();before=copy.deepcopy(args)
    result=module.assemble_matrix(*args,parent_sha256='c'*64)
    assert result['selected_provider_matrix_passed'] is True and not result['source_parent_all_stages_passed']
    assert result['source_parent_completed_stages']==10 and result['model_recovered_fence']==2
    assert result['certification'] is None and not result['qualification_complete']
    assert args==before


@pytest.mark.parametrize('fault',['pg-scope','pg-policy','pg-stale','epoch','parent-hash','q-gate','q-generation','q-work',
    'model-parent','model-token','model-proof','model-audit','model-session','model-export','model-work','corpus','snapshot-busy'])
def test_missing_or_cross_run_evidence_never_passes_the_matrix(tmp_path,fault):
    args=list(case(tmp_path));module=tool();value,authority,pg,pgr,parent,child,report=args
    if fault=='pg-scope':pgr['namespace_uid']=str(UUID(int=99))
    elif fault=='pg-policy':pgr['policy_removed']=False
    elif fault=='pg-stale':pgr['stale_return']['captured_old_owner_fence_publication_probe']['old_token_rejected']=False
    elif fault=='epoch':child['source_epoch']=4
    elif fault=='parent-hash':child['input_sha256']['parent']='d'*64
    elif fault=='q-gate':parent['completed']['qdrant.inject']['outcome']['gate']['closure']['reason']='expired'
    elif fault=='q-generation':parent['completed']['qdrant.verify']['outcome']['database']['publication']['active_count']=2
    elif fault=='q-work':parent['completed']['qdrant.verify']['outcome']['database']['works'][-1]['id']='a'*32
    elif fault=='model-parent':report['parent_execution_id']='f'*32
    elif fault=='model-token':child['original_lease']['owner_id']='d'*32
    elif fault=='model-proof':child['proof']['provider_pods']=1
    elif fault=='model-audit':report['reconciliation']['audit_confirmed']=False
    elif fault=='model-session':report['session']['session_revoked']=False
    elif fault=='model-export':report['old_export_verified']=False
    elif fault=='model-work':child['functional_snapshot']['works'][-1]['id']='a'*32
    elif fault=='corpus':child['functional_snapshot']['corpus'][1]['sha256']='d'*64
    else:child['functional_snapshot']['model_owners']=1
    if fault in {'q-gate','q-generation','q-work'}:
        from replica_runtime_run import digest
        previous=None
        for row in parent['completed'].values():
            row['previous_sha256']=previous
            row['receipt_sha256']=digest({k:v for k,v in row.items() if k!='receipt_sha256'})
            previous=row['receipt_sha256']
    with pytest.raises(ValueError):module.assemble_matrix(*args,parent_sha256='c'*64)


def source_case(tmp_path):
    from replica_runtime_run import digest
    names=['scripts/kubernetes/replica_worker_loss.py','scripts/kubernetes/replica_provider_native.py',
        'backend/test_scripts/replica_one.py','backend/app/deployment/replica_backup.py',
        'backend/app/services/diagnostics/paths.py','backend/test_scripts/run_replica_acceptance.py']
    bodies={n:b'VALUE = 1\n' for n in names}
    bodies[names[0]]=b'class WorkerLoss:\n def recover(self): return 1\n def verify(self, report): return 1\n'
    q={n:sha256(bodies[n]).hexdigest() for n in names[:-1]}
    for name,body in bodies.items():
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
    bodies[names[0]]=b'class WorkerLoss:\n def recover(self): return 1\n def verify(self, report): return 2\n def verification_client(self): return None\n'
    (tmp_path/names[0]).write_bytes(bodies[names[0]])
    model={n:sha256(body).hexdigest() for n,body in bodies.items() if n not in names[3:5]}
    # The original PC code digest includes CRLF; isolated CI contains LF.
    path=tmp_path/names[4];path.write_bytes(path.read_bytes().replace(b'\n',b'\r\n'))
    qraw={**q,names[4]:sha256(path.read_bytes()).hexdigest()}
    terminal=dict(verified=True,source_unchanged=True,exit_code=0,oom=False)
    qc=dict(phase='terminal',terminal=terminal.copy(),expected_manifest=q)
    mc=dict(phase='terminal',terminal=terminal.copy(),expected_manifest=model)
    return qc,mc,dict(code_sha256=digest(qraw)),dict(recipe_sha256=digest(model)),q


def test_source_audit_reconstructs_historical_hashes_and_checks_unchanged_fault_code(tmp_path):
    module=tool();qc,mc,parent,child,q=source_case(tmp_path)
    old=b'class WorkerLoss:\n def recover(self): return 1\n def verify(self, report): return 1\n'
    result=module.audit_sources(tmp_path,qc,mc,parent,child,old_worker=old)
    assert result['q_parent_code_reconstructed'] and result['model_recipe_reconstructed']
    assert result['worker_physical_methods_unchanged']
    assert result['verified_pc_eol_differences']==['backend/app/services/diagnostics/paths.py']
    assert result['historical_cases_reexecuted'] is False


@pytest.mark.parametrize('fault',['unfinished-ci','bad-ci','q-hash','model-hash','physical-change','old-blob','current-drift','unsafe-path'])
def test_source_audit_refuses_unproven_source_or_physical_changes(tmp_path,fault):
    module=tool();qc,mc,parent,child,q=source_case(tmp_path)
    old=b'class WorkerLoss:\n def recover(self): return 1\n def verify(self, report): return 1\n'
    if fault=='unfinished-ci':qc['phase']='running'
    elif fault=='bad-ci':mc['terminal']['source_unchanged']=False
    elif fault=='q-hash':parent['code_sha256']='0'*64
    elif fault=='model-hash':child['recipe_sha256']='0'*64
    elif fault=='physical-change':
        path=tmp_path/'scripts/kubernetes/replica_worker_loss.py';path.write_bytes(path.read_bytes().replace(b'recover(self): return 1',b'recover(self): return 2'))
        mc['expected_manifest'][path.relative_to(tmp_path).as_posix()]=sha256(path.read_bytes()).hexdigest()
        from replica_runtime_run import digest
        child['recipe_sha256']=digest(mc['expected_manifest'])
    elif fault=='old-blob':old+=b'OTHER = 2\n'
    elif fault=='unsafe-path':qc['expected_manifest']['scripts/kubernetes/../outside.py']='f'*64
    else:(tmp_path/'scripts/kubernetes/replica_provider_native.py').write_bytes(b'VALUE = 99\n')
    with pytest.raises(ValueError):module.audit_sources(tmp_path,qc,mc,parent,child,old_worker=old)


def test_fresh_observation_requires_exact_verified_data_and_unchanged_resources(tmp_path):
    module=tool();args=case(tmp_path);child=args[5]
    before=dict(inventory={'secret':{'uid':'a'}},platform={'nodes':{'one':'a'}})
    current=dict(database=copy.deepcopy(child['functional_snapshot']),before=before,after=copy.deepcopy(before),
        runtime_fingerprint=args[0]['runtime_fingerprint'],images={'backend':'same'},expected_images={'backend':'same'},
        replicas={'frontend':2,'api':2,'worker':2},policies=[],model={'active':0,'test_only':True})
    assert module.check_current(args[0],child,current)['verified'] is True
    for field in ['database','after','runtime_fingerprint','images','replicas','policies','model']:
        bad=copy.deepcopy(current)
        if field=='database':bad[field]['running']=1
        elif field=='after':bad[field]['inventory']['secret']['uid']='changed'
        elif field=='runtime_fingerprint':bad[field]='0'*64
        elif field=='images':bad[field]['backend']='changed'
        elif field=='replicas':bad[field]['worker']=1
        elif field=='policies':bad[field]=[{'uid':'unknown'}]
        else:bad[field]['active']=1
        with pytest.raises(ValueError):module.check_current(args[0],child,bad)


def test_collector_joins_native_records_with_fresh_observation_and_retains_input_hashes(tmp_path,monkeypatch):
    import json
    from replica_runtime_run import digest
    module=tool();args=list(case(tmp_path));value,authority,pg,pgr,parent,child,report=args
    source=tmp_path/'source';qc,mc,qp,cp,q=source_case(source)
    parent['code_sha256']=qp['code_sha256']
    previous=None
    for row in parent['completed'].values():
        row['code_sha256']=parent['code_sha256'];row['previous_sha256']=previous
        row['receipt_sha256']=digest({k:v for k,v in row.items() if k!='receipt_sha256'});previous=row['receipt_sha256']
    child['recipe_sha256']=cp['recipe_sha256'];child['quarantine_proof']['native_code_sha256']=parent['code_sha256']
    private=tmp_path/'private';private.mkdir()
    def save(name,body):
        path=private/name;path.write_text(json.dumps(body,sort_keys=True));return path
    pgpath=save('pg.json',pg);pgrpath=save('pgr.json',pgr);config=save('config.json',{'only':'test'})
    value['provider_fault']={'pg_journal':str(pgpath),'pg_report':str(pgrpath)};value['owned_runtime']={'config':str(config)}
    mp=save('manifest.json',value);pp=save('parent.json',parent);checksum=sha256(pp.read_bytes()).hexdigest()
    child['input_sha256'].update(manifest=sha256(mp.read_bytes()).hexdigest(),parent=checksum,config=sha256(config.read_bytes()).hexdigest())
    child['quarantine_proof']['core_sha256']=checksum;report['parent_core_sha256']=checksum
    rp=save('provider-model-recovery-'+parent['execution_id']+'-report.json',report);child['report']=str(rp)
    cp=save('child.json',child);qcpath=save('q-ci.json',qc);mcpath=save('model-ci.json',mc)
    output=private/('provider-matrix-'+parent['execution_id']+'-report.json')
    old=b'class WorkerLoss:\n def recover(self): return 1\n def verify(self, report): return 1\n'
    monkeypatch.setattr(module,'ROOT',source);monkeypatch.setattr(module,'historical_worker',lambda *args:old)
    calls=[]
    def observer(manifest,selected,path):
        calls.append(path)
        return dict(verified=True,current_observation_sha256='a'*64)
    result=module.collect_matrix(mp,pp,cp,qcpath,mcpath,output,observer=observer)
    assert result['selected_provider_matrix_passed'] and result['fresh_current']['verified']
    assert result['input_sha256']['parent']==checksum and calls==[output]
    assert json.loads(output.read_bytes())==result
    output.unlink()
    def drift(*args):
        pp.write_bytes(pp.read_bytes()+b' ');return dict(verified=True)
    with pytest.raises(ValueError):module.collect_matrix(mp,pp,cp,qcpath,mcpath,output,observer=drift)
    assert not output.exists()
