"""Frontend live admission must reject ambiguous scope before any HTTP or mutation."""
import copy
from uuid import NAMESPACE_DNS, uuid5

import pytest

from tests.test_kubernetes_chart import render


def uid(name):
    return str(uuid5(NAMESPACE_DNS, name))


def inputs():
    return dict(run_id='abcdefgh', namespace='replica-test-abcdefgh', namespace_uid=uid('namespace'),
                installation_id='3' * 32, application_release='test', context='kind-owned',
                frontend_url='http://127.0.0.1:25439', simulation_username='replica.admin',
                runtime_secret='test-runtime',
                deployment_mode='frontend-split',
                expected_image_ids={'backend': 'sha256:' + '1' * 64, 'frontend': 'sha256:' + '2' * 64})


def inventory():
    docs = render('deployment.mode=frontend-split', 'frontend.replicas=2')
    ns = dict(kind='Namespace', metadata=dict(name=inputs()['namespace'], uid=uid('namespace'), labels={
        'okf-run-id': 'abcdefgh', 'okf-purpose': 'replica-test',
        'app.kubernetes.io/managed-by': 'okf-replica-acceptance', 'okf-installation-id': '3' * 32}))
    objects = [ns]
    for item in docs:
        if item['kind'] not in {'Deployment', 'Service'} or item['metadata']['name'] not in {'application', 'frontend', 'backend'}:
            continue
        item['metadata'].update(uid=uid(item['metadata']['name']), generation=1, namespace=inputs()['namespace'])
        item['metadata']['labels']['okf-run-id'] = 'abcdefgh'
        objects.append(item)
        if item['kind'] == 'Service':
            continue
        role = item['spec']['template']['metadata']['labels']['okf-role']
        replicas = item['spec']['replicas']
        item['status'] = dict(observedGeneration=1, readyReplicas=replicas, updatedReplicas=replicas, availableReplicas=replicas)
        rs = dict(kind='ReplicaSet', metadata=dict(name=role+'-rs', uid=uid(role+'-rs'),
            ownerReferences=[dict(kind='Deployment', name=item['metadata']['name'], uid=item['metadata']['uid'])]))
        objects.append(rs)
        for i in range(replicas):
            objects.append(dict(kind='Pod', metadata=dict(name=role+'-'+str(i), uid=uid(role+'-'+str(i)),
                labels={**item['spec']['template']['metadata']['labels'], 'okf-run-id': 'abcdefgh'},
                ownerReferences=[dict(kind='ReplicaSet', name=rs['metadata']['name'], uid=rs['metadata']['uid'])]),
                spec=copy.deepcopy(item['spec']['template']['spec']), status=dict(phase='Running', containerStatuses=[dict(
                    name='frontend' if role=='frontend' else 'backend', ready=True, restartCount=0,
                    imageID='containerd://'+inputs()['expected_image_ids']['frontend' if role=='frontend' else 'backend'])])))
    for service, role in [('frontend', 'frontend'), ('backend', 'application')]:
        objects.append(dict(kind='EndpointSlice', metadata=dict(name=service+'-slice', labels={
            'kubernetes.io/service-name': service}, ownerReferences=[dict(kind='Service', uid=uid(service))]),
            endpoints=[dict(conditions=dict(ready=True), targetRef=dict(kind='Pod', uid=p['metadata']['uid']))
                       for p in objects if p['kind']=='Pod' and p['metadata']['labels']['okf-role']==role]))
    return objects


def validate(objects=None, value=None):
    from test_scripts.replica_frontend_acceptance import validate_frontend_inventory
    return validate_frontend_inventory(objects if objects is not None else inventory(), value or inputs())


def test_actual_rendered_split_contract_is_admitted():
    receipt = validate()
    assert receipt['frontend_replicas'] == 2 and receipt['combined_replicas'] == 1
    assert receipt['backend_pod_uid'] == uid('application-0')
    assert len(receipt['frontend_pod_uids']) == 2
    assert receipt['shared_frontend_pvcs'] == 0


@pytest.mark.parametrize('change', ['namespace_uid', 'namespace_label', 'installation', 'namespace_missing',
                                  'frontend_build', 'backend_build', 'not_ready', 'restart', 'retiring',
                                  'owner_uid', 'foreign_release', 'extra_worker', 'backend_scale',
                                  'pvc', 'host_path', 'secret_mount', 'env_secret', 'env_literal', 'privileged', 'env_from', 'token_automount',
                                  'host_network', 'backend_url', 'diagnostic_token', 'service_selector',
                                  'endpoint_owner', 'foreign_endpoint', 'terminating_endpoint'])
def test_ambiguous_or_unsafe_frontend_scope_refused(change):
    objects = inventory()
    front = next(p for p in objects if p['kind']=='Pod' and p['metadata']['name']=='frontend-0')
    backend = next(p for p in objects if p['kind']=='Deployment' and p['metadata']['name']=='application')
    container = front['spec']['containers'][0]
    service = next(p for p in objects if p['kind']=='Service' and p['metadata']['name']=='backend')
    endpoints = next(p for p in objects if p['kind']=='EndpointSlice' and p['metadata']['name']=='backend-slice')
    if change == 'namespace_uid': objects[0]['metadata']['uid'] = uid('foreign')
    elif change == 'namespace_label': objects[0]['metadata']['labels']['okf-purpose'] = 'production'
    elif change == 'installation': objects[0]['metadata']['labels']['okf-installation-id'] = '4' * 32
    elif change == 'namespace_missing': objects.pop(0)
    elif change == 'frontend_build': front['status']['containerStatuses'][0]['imageID'] = 'sha256:' + '9' * 64
    elif change == 'backend_build':
        next(p for p in objects if p['kind']=='Pod' and p['metadata']['name']=='application-0')['status']['containerStatuses'][0]['imageID']='sha256:'+'9'*64
    elif change == 'not_ready': front['status']['containerStatuses'][0]['ready'] = False
    elif change == 'restart': front['status']['containerStatuses'][0]['restartCount'] = 1
    elif change == 'retiring': front['metadata']['deletionTimestamp'] = 'now'
    elif change == 'owner_uid': front['metadata']['ownerReferences'][0]['uid'] = uid('foreign')
    elif change == 'foreign_release': front['metadata']['labels']['app.kubernetes.io/instance'] = 'foreign'
    elif change == 'extra_worker': objects.append(dict(kind='Deployment', metadata=dict(name='worker')))
    elif change == 'backend_scale': backend['spec']['replicas'] = 2
    elif change == 'pvc': front['spec']['volumes'].append(dict(name='foreign', persistentVolumeClaim=dict(claimName='data')))
    elif change == 'host_path': front['spec']['volumes'].append(dict(name='foreign', hostPath=dict(path='/data')))
    elif change == 'secret_mount': front['spec']['volumes'].append(dict(name='foreign', secret=dict(secretName='runtime')))
    elif change == 'env_secret': container['env'].append(dict(name='DATABASE_URL', valueFrom=dict(secretKeyRef=dict(name='runtime', key='DATABASE_URL'))))
    elif change == 'env_literal': container['env'].append(dict(name='DATABASE_URL', value='postgresql://app:PRIVATE_CANARY@db/example'))
    elif change == 'privileged': container['securityContext']['privileged'] = True
    elif change == 'env_from': container['envFrom'] = [dict(secretRef=dict(name='runtime'))]
    elif change == 'token_automount': front['spec']['automountServiceAccountToken'] = True
    elif change == 'host_network': front['spec']['hostNetwork'] = True
    elif change == 'backend_url': next(e for e in container['env'] if e['name']=='BACKEND_URL')['value']='http://foreign:8000'
    elif change == 'diagnostic_token': next(e for e in container['env'] if e['name']=='DIAGNOSTICS_INGEST_TOKEN')['valueFrom']['secretKeyRef']['key']='APP_SECRET_KEY'
    elif change == 'service_selector': service['spec']['selector']['okf-role']='worker'
    elif change == 'endpoint_owner': endpoints['metadata']['ownerReferences'][0]['uid']=uid('foreign')
    elif change == 'foreign_endpoint': endpoints['endpoints'][0]['targetRef']['uid']=uid('foreign')
    elif change == 'terminating_endpoint': endpoints['endpoints'][0]['conditions']['terminating']=True
    with pytest.raises(ValueError): validate(objects)


def test_live_frontend_dispatch_keeps_unobserved_scenarios_pending(monkeypatch):
    from test_scripts import run_replica_acceptance as runner
    seen = []
    def probe(value, **kwargs):
        seen.append((value['namespace_uid'], kwargs))
        return [{'name': 'frontend.split_inventory', 'status': 'passed'},
                {'name': 'frontend.gateway_sso', 'status': 'pending'}]
    monkeypatch.setattr(runner, 'probe_frontend', probe, raising=False)
    value = {**inputs(), 'schema_version': 1, 'profile': 'basic',
             'database_url': 'postgresql://app@db/replica_test_abcdefgh',
             'qdrant_collection': 'replica_test_abcdefgh', 'bucket_prefix': 'replica_test/abcdefgh/'}
    report = runner.build_report(value, 'frontend', live=True)
    assert len(seen) == 1
    assert report['passed'] is False and report['skipped_required'] == 1
    assert {c['name'] for c in report['checks']} == {'target_identity', 'frontend.split_inventory', 'frontend.gateway_sso'}


def test_foreign_frontend_observation_never_reaches_exec(monkeypatch):
    from test_scripts import replica_frontend_acceptance as live
    class Foreign:
        def __init__(self, *_a, **_k): pass
        def scope(self): raise ValueError('PRIVATE_CANARY')
        def run(self, *_a, **_k): pytest.fail('No read or mutation after ownership refusal')
    monkeypatch.setattr(live, 'Kubernetes', Foreign)
    checks = live.probe_frontend(inputs())
    assert checks[0]['status'] == 'failed' and all(c['status'] == 'pending' for c in checks[1:])
    assert 'PRIVATE_CANARY' not in str(checks)


def test_distributed_all_suite_does_not_run_combined_frontend_probe(monkeypatch):
    from test_scripts import run_replica_acceptance as runner
    monkeypatch.setattr(runner, 'probe_frontend', lambda *_a, **_k: pytest.fail('Wrong topology probe'))
    monkeypatch.setattr(runner, 'probe_runtime', lambda *_a, **_k: [])
    value = {**inputs(), 'deployment_mode': 'distributed', 'schema_version': 1, 'profile': 'basic',
             'database_url': 'postgresql://app@db/replica_test_abcdefgh',
             'qdrant_collection': 'replica_test_abcdefgh', 'bucket_prefix': 'replica_test/abcdefgh/'}
    report = runner.build_report(value, 'all', live=True)
    assert report['passed'] is False
    assert any(c['name']=='frontend' and c['status']=='pending' for c in report['checks'])
