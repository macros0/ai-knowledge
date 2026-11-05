"""Pure checks for admitted owned dependency observations; no fault authority.

The native caller must independently bind namespace/installation/Pod/image and
provider identities. A valid policy or supplied observation never certifies a
live operation, submits a request, or authorizes provider reconciliation.
"""
from copy import deepcopy
import re
from uuid import UUID


RECIPE = 'owned-managed-s3-dependencies-v1'
PROVIDERS = {'qdrant': ('6333','6334'), 'model': ('8000',)}
SERVICES = {'postgres','qdrant','model','artifacts'}
TRANSIENT = {'storage_unavailable','model_unavailable','transport_unavailable'}
PUBLICATION_FIELDS = {'doc_id','active_generation_id','active_count','concept_sha256','original_sha256'}
WORK_FIELDS = {'id','kind','doc_id','status','attempts','fence','error_code','cancel_requested','owner_id','lease_until'}


def require(condition):
    if not condition:
        raise ValueError('Invalid owned dependency fault evidence')


def hexadecimal(value, length):
    return isinstance(value,str) and re.fullmatch('[0-9a-f]{'+str(length)+'}',value) is not None


def uid(value):
    if not isinstance(value,str):
        return False
    try:
        return str(UUID(value))==value
    except ValueError:
        return False


def provider_name(provider):
    require(isinstance(provider,str) and provider in PROVIDERS)


def release(value):
    return isinstance(value,str) and re.fullmatch('[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?',value) is not None


def fault_policy(value, provider, operation):
    """Construct only a namespaced deny rule; actual admission is separate."""
    provider_name(provider)
    require(isinstance(value,dict) and hexadecimal(operation,32))
    require(value.get('recipe')==RECIPE and value.get('profile')=='basic'
        and value.get('deployment_mode')=='distributed' and value.get('artifact_backend')=='s3')
    run=value.get('run_id')
    require(isinstance(run,str) and re.fullmatch('[a-z0-9]{8,32}',run) is not None)
    require(value.get('namespace')=='replica-test-'+run and uid(value.get('namespace_uid')))
    app,storage=value.get('application_release'),value.get('storage_release')
    require(release(app) and release(storage) and app!=storage)
    target={'k8s:io.kubernetes.pod.namespace':value['namespace'],
        'app.kubernetes.io/instance':storage if provider=='qdrant' else app}
    target['okf-storage-component' if provider=='qdrant' else 'okf-role']='qdrant' if provider=='qdrant' else 'model-stub'
    return dict(apiVersion='cilium.io/v2',kind='CiliumNetworkPolicy',metadata=dict(
        name='replica-dependency-'+provider+'-'+operation[:12],namespace=value['namespace'],
        labels={'okf-run-id':run,'okf-provider-fault-operation':operation,'okf-provider-fault':provider}),
        spec=dict(endpointSelector=dict(matchLabels={'app.kubernetes.io/instance':app},
            matchExpressions=[dict(key='okf-role',operator='In',values=['api','worker'])]),
            egressDeny=[dict(toEndpoints=[dict(matchLabels=target)],
                toPorts=[dict(ports=[dict(port=port,protocol='TCP') for port in PROVIDERS[provider]])])]))


def assert_network(rows, expected_pods, provider):
    """Every exact API/worker must prove selective denial and live controls."""
    provider_name(provider)
    require(isinstance(expected_pods,dict) and len(expected_pods)==4 and all(uid(k) for k in expected_pods))
    roles=list(expected_pods.values())
    require(roles.count('api')==roles.count('worker')==2)
    require(isinstance(rows,list) and len(rows)==4)
    seen=set()
    for row in rows:
        require(isinstance(row,dict) and set(row)=={'pod_uid','role','live','restart_count','connectivity'})
        identity=row['pod_uid']
        require(uid(identity) and identity in expected_pods and identity not in seen and row['role']==expected_pods[identity])
        seen.add(identity)
        require(type(row['live']) is int and row['live']==200 and type(row['restart_count']) is int and row['restart_count']==0)
        states=row['connectivity']
        require(isinstance(states,dict) and set(states)==SERVICES and all(type(s) is bool for s in states.values()))
        require(states[provider] is False and all(s is True for name,s in states.items() if name!=provider))
    require(seen==set(expected_pods))
    return dict(provider=provider,blocked_pods=4,positive_controls=12)


def publication(value):
    require(isinstance(value,dict) and set(value)==PUBLICATION_FIELDS)
    require(hexadecimal(value['doc_id'],16) and hexadecimal(value['active_generation_id'],32)
        and type(value['active_count']) is int and value['active_count']==1
        and hexadecimal(value['concept_sha256'],64) and hexadecimal(value['original_sha256'],64))


def previous_ids(values):
    require(isinstance(values,list) and 1<=len(values)<=16 and all(hexadecimal(v,32) for v in values))
    require(len(values)==len(set(values)))
    return set(values)


def work_row(row, doc_id):
    require(isinstance(row,dict) and set(row)==WORK_FIELDS)
    require(hexadecimal(row['id'],32) and row['kind']=='document' and row['doc_id']==doc_id)
    require(isinstance(row['status'],str) and row['status'] in {'queued','running','succeeded','failed','cancelled'})
    require(type(row['attempts']) is int and 0<=row['attempts']<=10
        and type(row['fence']) is int and 0<=row['fence']<=2**63-1 and type(row['cancel_requested']) is bool)
    require(row['error_code'] is None or isinstance(row['error_code'],str)
        and re.fullmatch('[a-z][a-z0-9_]{0,63}',row['error_code']) is not None)
    require(row['owner_id'] is None or isinstance(row['owner_id'],str) and 1<=len(row['owner_id'])<=128)
    require(row['lease_until'] is None or isinstance(row['lease_until'],str) and 1<=len(row['lease_until'])<=64)


def works_by_id(rows, doc_id, previous):
    require(isinstance(rows,list) and 1<=len(rows)<=17)
    result={}
    for row in rows:
        work_row(row,doc_id)
        require(row['id'] not in result)
        result[row['id']]=row
    require(previous<=set(result))
    for identity in previous:
        old=result[identity]
        require(old['status'] in {'succeeded','failed','cancelled'} and old['owner_id'] is None and old['lease_until'] is None)
    return result


def queued_retry(row):
    require(row['status']=='queued' and row['attempts']>=1 and row['fence']>=1
        and row['error_code'] in TRANSIENT and row['cancel_requested'] is False
        and row['owner_id'] is None and row['lease_until'] is None)


def assert_outage(before, current, previous_work_ids, works, provider):
    """Return the one observed retry, never manufacture work or change a base."""
    provider_name(provider)
    publication(before);publication(current)
    require(before==current)
    previous=previous_ids(previous_work_ids)
    rows=works_by_id(works,before['doc_id'],previous)
    added=set(rows)-previous
    require(len(added)==1)
    row=rows[next(iter(added))]
    queued_retry(row)
    return deepcopy(row)


def assert_recovered(before, current, previous_work_ids, works, queued, original_sha256):
    """Require a retry of the same work and a single new active publication."""
    publication(before);publication(current)
    require(before['doc_id']==current['doc_id'] and hexadecimal(original_sha256,64)
        and before['original_sha256']==current['original_sha256']==original_sha256
        and current['active_generation_id']!=before['active_generation_id'])
    previous=previous_ids(previous_work_ids)
    work_row(queued,before['doc_id']);queued_retry(queued)
    require(queued['id'] not in previous)
    rows=works_by_id(works,before['doc_id'],previous)
    require(set(rows)==previous|{queued['id']})
    row=rows[queued['id']]
    require(row['status']=='succeeded' and row['attempts']>queued['attempts'] and row['fence']>queued['fence']
        and row['error_code'] is None and row['owner_id'] is None and row['lease_until'] is None and row['cancel_requested'] is False)
    return dict(work_id=row['id'],attempts=row['attempts'],fence=row['fence'],
        active_generation_id=current['active_generation_id'],original_sha256=original_sha256)
