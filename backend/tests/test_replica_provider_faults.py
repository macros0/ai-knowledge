"""Evidence cannot turn a foreign fault, lost publication or new job into recovery."""
import copy
import importlib
import json
from uuid import UUID

import pytest


def tool():
    return importlib.import_module('test_scripts.replica_provider_faults')


def value():
    return dict(recipe='owned-managed-s3-dependencies-v1',profile='basic',deployment_mode='distributed',
        artifact_backend='s3',run_id='faults01',namespace='replica-test-faults01',namespace_uid=str(UUID(int=100)),
        application_release='app',storage_release='storage')


def publication():
    return dict(doc_id='1'*16,active_generation_id='2'*32,active_count=1,
        concept_sha256='3'*64,original_sha256='4'*64)


def work(identity='b'*32,**changes):
    return dict(id=identity,kind='document',doc_id='1'*16,status='queued',attempts=1,fence=1,
        error_code='storage_unavailable',cancel_requested=False,owner_id=None,lease_until=None,**changes)


def old_work():
    row=work('a'*32);row.update(status='succeeded',error_code=None);return row


def network(provider='qdrant'):
    expected={str(UUID(int=i)):role for i,role in enumerate(['api','api','worker','worker'],1)}
    rows=[dict(pod_uid=uid,role=role,live=200,restart_count=0,
        connectivity={name:name!=provider for name in ['postgres','qdrant','model','artifacts']}) for uid,role in expected.items()]
    return rows,expected


@pytest.mark.parametrize('provider,ports',[('qdrant',['6333','6334']),('model',['8000'])])
def test_policy_denies_only_owned_api_workers_to_one_same_namespace_component(provider,ports):
    v=value();v['extra_secret']='DO_NOT_PUBLISH_THIS_SECRET';before=copy.deepcopy(v)
    p=tool().fault_policy(v,provider,'f'*32)
    assert v==before and p['apiVersion']=='cilium.io/v2' and p['kind']=='CiliumNetworkPolicy'
    assert p['metadata']['namespace']==v['namespace'] and p['metadata']['labels']['okf-provider-fault-operation']=='f'*32
    assert set(p['spec'])=={'endpointSelector','egressDeny'}
    selector=p['spec']['endpointSelector']
    assert selector==dict(matchLabels={'app.kubernetes.io/instance':'app'},matchExpressions=[dict(key='okf-role',operator='In',values=['api','worker'])])
    rule=p['spec']['egressDeny'];assert len(rule)==1
    target=rule[0]['toEndpoints'];assert len(target)==1
    labels=target[0]['matchLabels'];assert labels['k8s:io.kubernetes.pod.namespace']==v['namespace']
    assert labels['app.kubernetes.io/instance']==('storage' if provider=='qdrant' else 'app')
    assert labels['okf-storage-component' if provider=='qdrant' else 'okf-role']==('qdrant' if provider=='qdrant' else 'model-stub')
    assert rule[0]['toPorts']==[dict(ports=[dict(port=port,protocol='TCP') for port in ports])]
    assert v['extra_secret'] not in json.dumps(p)


@pytest.mark.parametrize('field,bad',[
    ('recipe','passed'),('profile','resilient'),('deployment_mode','compact'),('artifact_backend','rwx'),
    ('namespace','production'),('run_id','faults/01'),('namespace_uid','unknown'),
    ('application_release','app/foreign'),('storage_release','storage.other'),('storage_release','app'),
])
def test_policy_requires_the_explicit_owned_basic_managed_s3_scope(field,bad):
    v=value();v[field]=bad
    with pytest.raises(ValueError):tool().fault_policy(v,'qdrant','f'*32)


@pytest.mark.parametrize('provider,operation',[('postgres','f'*32),('real-model','f'*32),('model','not-an-operation'),('model',True)])
def test_unknown_provider_or_operation_cannot_select_a_fault(provider,operation):
    with pytest.raises(ValueError):tool().fault_policy(value(),provider,operation)


@pytest.mark.parametrize('provider',['qdrant','model'])
def test_all_four_bound_processes_prove_denial_with_independent_positive_controls(provider):
    rows,expected=network(provider)
    assert tool().assert_network(rows,expected,provider)==dict(provider=provider,blocked_pods=4,positive_controls=12)


@pytest.mark.parametrize('fault',['missing','duplicate','foreign','role','allowed','other_blocked','not_live','restart','truthy','extra'])
def test_network_failure_is_not_inferred_from_timeout_or_a_partial_pod_set(fault):
    rows,expected=network()
    if fault=='missing':rows.pop()
    elif fault=='duplicate':rows[-1]=copy.deepcopy(rows[0])
    elif fault=='foreign':rows[0]['pod_uid']=str(UUID(int=999))
    elif fault=='role':rows[0]['role']='frontend'
    elif fault=='allowed':rows[0]['connectivity']['qdrant']=True
    elif fault=='other_blocked':rows[0]['connectivity']['postgres']=False
    elif fault=='not_live':rows[0]['live']=503
    elif fault=='restart':rows[0]['restart_count']=1
    elif fault=='truthy':rows[0]['connectivity']['model']=1
    else:rows[0]['credentials']='must-not-be-an-evidence-field'
    with pytest.raises(ValueError):tool().assert_network(rows,expected,'qdrant')


@pytest.mark.parametrize('provider,code',[('qdrant','storage_unavailable'),('model','storage_unavailable'),('model','model_unavailable'),('qdrant','transport_unavailable')])
def test_outage_preserves_publication_and_freezes_one_actual_queued_retry(provider,code):
    row=work();row['error_code']=code;rows=[old_work(),row]
    result=tool().assert_outage(publication(),publication(),['a'*32],rows,provider)
    assert result==row and result is not row


@pytest.mark.parametrize('fault',['pointer','concepts','original','count','bool_count','failed','running','untyped',
    'no_attempt','no_fence','owner','lease','cancelled','duplicate','missing_old','second_new','foreign_doc','extra'])
def test_changed_publication_or_non_retry_work_cannot_pass_outage(fault):
    before=publication();current=copy.deepcopy(before);row=work();rows=[old_work(),row]
    if fault=='pointer':current['active_generation_id']='e'*32
    elif fault=='concepts':current['concept_sha256']='e'*64
    elif fault=='original':current['original_sha256']='e'*64
    elif fault=='count':current['active_count']=2
    elif fault=='bool_count':current['active_count']=True
    elif fault=='failed':row['status']='failed'
    elif fault=='running':row['status']='running'
    elif fault=='untyped':row['error_code']='handler_failed'
    elif fault=='no_attempt':row['attempts']=0
    elif fault=='no_fence':row['fence']=0
    elif fault=='owner':row['owner_id']='old-owner'
    elif fault=='lease':row['lease_until']='still-leased'
    elif fault=='cancelled':row['cancel_requested']=True
    elif fault=='duplicate':rows.append(copy.deepcopy(row))
    elif fault=='missing_old':rows.pop(0)
    elif fault=='second_new':rows.append(work('c'*32))
    elif fault=='foreign_doc':row['doc_id']='9'*16
    else:row['payload_json']={'arbitrary':'not part of proof'}
    with pytest.raises(ValueError):tool().assert_outage(before,current,['a'*32],rows,'qdrant')


def recovered():
    current=publication();current.update(active_generation_id='5'*32,concept_sha256='6'*64)
    row=work();row.update(status='succeeded',attempts=2,fence=2,error_code=None)
    return current,[old_work(),row]


def test_recovery_retries_same_work_with_new_fence_one_generation_and_same_original():
    current,rows=recovered()
    result=tool().assert_recovered(publication(),current,['a'*32],rows,work(),'4'*64)
    assert result==dict(work_id='b'*32,attempts=2,fence=2,active_generation_id='5'*32,original_sha256='4'*64)


@pytest.mark.parametrize('fault',['new_work','same_fence','same_attempt','not_done','same_generation','two_active','download','original','error','missing_old','extra_work'])
def test_recovery_cannot_hide_resubmission_stale_owner_or_wrong_download(fault):
    before=publication();current,rows=recovered();row=rows[1];download='4'*64
    if fault=='new_work':row['id']='c'*32
    elif fault=='same_fence':row['fence']=1
    elif fault=='same_attempt':row['attempts']=1
    elif fault=='not_done':row['status']='queued'
    elif fault=='same_generation':current['active_generation_id']=before['active_generation_id']
    elif fault=='two_active':current['active_count']=2
    elif fault=='download':download='7'*64
    elif fault=='original':current['original_sha256']='7'*64
    elif fault=='error':row['error_code']='handler_failed'
    elif fault=='missing_old':rows.pop(0)
    else:rows.append(work('c'*32))
    with pytest.raises(ValueError):tool().assert_recovered(before,current,['a'*32],rows,work(),download)
