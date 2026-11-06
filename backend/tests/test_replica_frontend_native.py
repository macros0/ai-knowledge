"""Native coordinator refuses scope drift before HTTP, model or Kubernetes action."""
import copy
import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from tests.test_replica_frontend_acceptance import inputs, inventory, uid


def module():
    path = Path(__file__).resolve().parents[1] / 'test_scripts/replica_frontend_native.py'
    assert path.is_file(), 'Native frontend coordinator is missing'
    return importlib.import_module('test_scripts.replica_frontend_native')


def value():
    return {**inputs(), 'profile':'basic', 'functional_recipe': 'owned-simulation-local-v1',
            'expected_model_image_id': 'sha256:' + '4' * 64}


def objects():
    result = inventory()
    app = next(o for o in result if o['kind'] == 'Deployment' and o['metadata']['name'] == 'application')
    model = copy.deepcopy(app)
    model['metadata'].update(name='model-stub', uid=uid('model-stub'))
    model['spec']['selector'] = {'matchLabels': {'okf-role': 'model-stub'}}
    model['spec']['template']['metadata']['labels']['okf-role'] = 'model-stub'
    model['spec']['template']['spec']['containers'] = [{'name': 'model-stub', 'image': 'stub'}]
    model['status'].update(readyReplicas=1, availableReplicas=1, updatedReplicas=1)
    result.append(model)
    result.append(dict(kind='ReplicaSet', metadata=dict(name='model-rs', uid=uid('model-rs'),
        ownerReferences=[dict(kind='Deployment', uid=uid('model-stub'))])))
    pod = copy.deepcopy(next(o for o in result if o['kind'] == 'Pod' and o['metadata']['name'] == 'application-0'))
    pod['metadata'].update(name='model-0', uid=uid('model-0'))
    pod['metadata']['labels']['okf-role'] = 'model-stub'
    pod['metadata']['ownerReferences'] = [dict(kind='ReplicaSet', uid=uid('model-rs'))]
    pod['spec']['containers'] = [{'name': 'model-stub', 'image': 'stub'}]
    pod['status']['containerStatuses'][0].update(name='model-stub', imageID=value()['expected_model_image_id'])
    result.append(pod)
    for pod in [o for o in result if o['kind'] == 'Pod']: pod['spec']['nodeName'] = 'owned-node'
    result += [dict(kind='ConfigMap', metadata=dict(name='runtime', uid=uid('runtime')), data={'AUTH_PROVIDER': 'simulation'}),
               dict(kind='Secret', metadata=dict(name='test-runtime', uid=uid('runtime-secret')), data={'APP_SECRET_KEY': 'PRIVATE_CANARY'}),
               *[dict(kind='PersistentVolumeClaim', metadata=dict(name=name, uid=uid(name)),
                      spec={'volumeName': 'pv-' + name}, status={'phase': 'Bound'}) for name in ['data', 'diagnostics']]]
    for role,port in [('postgres',5432),('qdrant',6333)]:
        provider=copy.deepcopy(pod);provider['metadata'].update(name=role+'-0',uid=uid(role+'-0'))
        provider['metadata']['labels']={'okf-provider':role}
        provider['metadata']['ownerReferences']=[dict(kind='StatefulSet',uid=uid(role+'-sts'))]
        provider['spec']['containers']=[dict(name=role,image=role)]
        result.append(provider)
        result.append(dict(kind='StatefulSet',metadata=dict(name=role,uid=uid(role+'-sts')),spec={'replicas':1}))
        result.append(dict(kind='Service',metadata=dict(name=role,uid=uid(role+'-svc')),
                           spec={'type':'ClusterIP','selector':{'okf-provider':role},'ports':[{'port':port,'targetPort':port}]}))
    return result


class Kube:
    def __init__(self):
        self.objects = objects()
        self.calls = []
        self.node_uid = uid('node')
        self.pv_uid_prefix = 'pv-original-'
        self.proof = {'fresh': True, 'model': {'started': 0, 'finished': 0, 'active': 0}}

    def run(self, *args, source=None):
        self.calls.append(args)
        if args[:2] == ('get', 'namespace'): return json.dumps(self.objects[0])
        if args[:2] == ('get', 'node'): return json.dumps({'metadata': {'uid': self.node_uid}})
        if args[:2] == ('get', 'pv'):
            return json.dumps({'metadata': {'uid': uid(self.pv_uid_prefix + args[2])},
                               'spec': {'claimRef': {'uid': uid(args[2][3:]), 'namespace': inputs()['namespace']}}})
        if args[0] == 'get': return json.dumps({'items': self.objects[1:]})
        raise AssertionError('No mutation permitted in admission')

    def python(self, pod, source):
        self.calls.append(('native-read-only', pod))
        return copy.deepcopy(self.proof)


def envelope(stage='preflight'):
    return dict(execution_id='a' * 32, code_sha256='b' * 64, binding_sha256='c' * 64,
                stage=stage, intent_id=uuid4().hex)


def coordinator(tmp_path):
    kube = Kube()
    return module().Coordinator(tmp_path, value(), kube=kube), kube


def test_preflight_captures_scope_and_native_freshness_without_forward_or_http(tmp_path):
    current, kube = coordinator(tmp_path)
    request = envelope()
    result = current.dispatch(request)
    assert result['status'] == 'passed' and result['intent_id'] == request['intent_id']
    assert len(result['evidence']['scope_sha256']) == 64
    assert 'PRIVATE_CANARY' not in json.dumps(result) + (tmp_path / 'scope.json').read_text()
    assert all(call[0] in {'get', 'native-read-only'} for call in kube.calls)


@pytest.mark.parametrize('fault', ['recipe', 'namespace_uid', 'model_image', 'model_owner', 'foreign_job', 'freshness'])
def test_invalid_fixture_is_refused_before_native_mutation(fault, tmp_path):
    kube, selected = Kube(), value()
    if fault == 'recipe': selected['functional_recipe'] = 'customer'
    elif fault == 'namespace_uid': kube.objects[0]['metadata']['uid'] = uid('foreign')
    elif fault == 'model_image': next(o for o in kube.objects if o['kind'] == 'Pod' and o['metadata']['name'] == 'model-0')['status']['containerStatuses'][0]['imageID'] = 'sha256:' + '5' * 64
    elif fault == 'model_owner': next(o for o in kube.objects if o['kind'] == 'Pod' and o['metadata']['name'] == 'model-0')['metadata']['ownerReferences'][0]['uid'] = uid('foreign')
    elif fault == 'foreign_job': kube.objects.append(dict(kind='Job', metadata=dict(name='foreign')))
    elif fault == 'freshness': kube.proof['fresh'] = False
    with pytest.raises(ValueError):
        module().Coordinator(tmp_path, selected, kube=kube).dispatch(envelope())
    assert all(call[0] in {'get', 'native-read-only'} for call in kube.calls)


@pytest.mark.parametrize('kind', ['ConfigMap', 'Secret', 'PersistentVolumeClaim', 'Deployment'])
def test_configuration_claim_or_controller_drift_blocks_even_receipt_adoption(kind, tmp_path):
    current, kube = coordinator(tmp_path)
    request = envelope()
    current.dispatch(request)
    item = next(o for o in kube.objects if o['kind'] == kind)
    item['metadata']['uid'] = uid('foreign')
    with pytest.raises(ValueError): current.dispatch(request, observe=True)
    assert not any(call[0] == 'delete' for call in kube.calls)


def test_lost_ack_adopts_same_terminal_receipt_without_rerunning_native_probe(tmp_path):
    current, kube = coordinator(tmp_path)
    request = envelope(); first = current.dispatch(request)
    count = sum(call[0] == 'native-read-only' for call in kube.calls)
    second = current.dispatch(request, observe=True)
    assert first == second
    assert sum(call[0] == 'native-read-only' for call in kube.calls) == count


def test_duplicate_stage_cannot_select_another_intent_or_execution(tmp_path):
    current, _ = coordinator(tmp_path)
    request = envelope(); current.dispatch(request)
    for field in ['intent_id', 'execution_id', 'code_sha256', 'binding_sha256']:
        forged = {**request, field: 'f' * (32 if field in {'intent_id', 'execution_id'} else 64)}
        with pytest.raises(ValueError): current.dispatch(forged, observe=True)


def test_observation_never_creates_a_missing_native_stage(tmp_path):
    current, kube = coordinator(tmp_path)
    with pytest.raises(ValueError): current.dispatch(envelope(), observe=True)
    assert not (tmp_path / 'native.preflight.intent.json').exists()
    assert not any(call[0] == 'native-read-only' for call in kube.calls)


@pytest.mark.parametrize('target', ['node', 'pv'])
def test_node_and_physical_volume_uid_change_refuses_adoption(target, tmp_path):
    current, kube = coordinator(tmp_path)
    request = envelope(); current.dispatch(request)
    if target == 'node': kube.node_uid = uid('other-node')
    else: kube.pv_uid_prefix = 'other-pv-'
    with pytest.raises(ValueError): current.dispatch(request, observe=True)


class Processes:
    def __init__(self, files, kube):
        self.files, self.kube, self.handles, self.calls = files, kube, {}, []

    def forward(self, slot, pod, port):
        assert slot not in self.handles
        self.handles[slot] = {'url': 'http://127.0.0.1:' + str(25400 + len(self.handles)), 'pod': pod, 'slot': slot}
        self.calls.append(('forward', slot))
        self.files.once(slot+'.intent.json', {'pod':pod,'port':port})
        self.files.once(slot+'.pid.json', {'pid':25400+len(self.handles)})
        return self.handles[slot]

    def route(self, slot): return self.handles[slot]
    def stop(self, slot):
        self.calls.append(('stop', slot))
        if not self.files.path(slot+'.closed.json').exists(): self.files.once(slot+'.closed.json', {'original_process_gone': True})
    def describe(self, pid): return None if self.kube.deleted else {'pid': pid}

    def start_client(self, program, configuration):
        self.calls.append(('client', 'once'))
        self.kube.metrics = {'started': 3, 'finished': 2, 'active': 1}
        self.files.once('client.pid.json', {'pid': 1234})
        self.files.once('loss.progress.json', {'terminal': False, 'sources_seen': True, 'result_seen': False})


class HTTP:
    def __init__(self, directory, routes, username, execution_id):
        from test_scripts.replica_frontend_http import PrivateFiles
        self.files = PrivateFiles(directory)

    def effect(self, name, value): self.files.once(name + '.result.json', value); return value
    def session(self): return self.effect('session', {'shared_routes': 3})
    def upload(self): return self.effect('upload', {'document_id': 'a' * 16, 'original_sha256': 'f' * 64})
    def publish(self): return {'status': 'passed', 'verified_routes': 3}
    def export(self): return self.effect('export', {'job_id': 7})
    def export_observe(self): return {'status': 'passed', 'export_sha256': 'e' * 64}
    def stream(self): return self.effect('stream', {'unbuffered_frontends': 2})
    def shared_session(self): pass
    def logout(self): return self.effect('logout', {'old_session_revoked_routes': 3})


class FunctionalKube(Kube):
    def __init__(self):
        super().__init__(); self.metrics = {'started': 2, 'finished': 2, 'active': 0}; self.deleted = False

    def python(self, pod, source):
        if 'FRONTEND_MODEL_METRICS' in source: return self.metrics.copy()
        if 'FRONTEND_FINAL_PROOF' in source: return {'quiescent': True, 'original_sha256': 'f'*64, 'model': self.metrics.copy()}
        return super().python(pod, source)

    def run(self, *args, source=None):
        if args[0] == 'delete':
            self.calls.append((*args, json.loads(source))); self.deleted = True
            return json.dumps({'kind': 'Status', 'status': 'Success'})
        if args[:2] == ('get', 'pod'):
            found = next((p for p in self.objects if p['kind']=='Pod' and p['metadata']['name']==args[2]), None)
            return json.dumps(found) if found else ''
        return super().run(*args, source=source)


def before_loss(tmp_path):
    from test_scripts.replica_frontend_http import PrivateFiles
    kube = FunctionalKube(); process = Processes(PrivateFiles(tmp_path), kube)
    current = module().Coordinator(tmp_path, value(), kube=kube, processes=process, http_factory=HTTP)
    current.sleep = lambda _: None
    for stage in module().STAGES[:7]: current.dispatch(envelope(stage))
    return current, kube, process


def test_functional_stages_own_only_three_forwards_and_adopt_without_http_replay(tmp_path):
    current, _, process = before_loss(tmp_path)
    assert process.calls == [('forward', 'pf-0'), ('forward', 'pf-1'), ('forward', 'pf-2')]
    intent = current.files.read('native.upload.intent.json')
    assert current.dispatch(intent, observe=True)['status'] == 'passed'
    assert current.files.read('upload.result.json')['document_id'] == 'a' * 16


def test_active_loss_requires_both_gates_and_uses_body_uid_precondition_once(tmp_path):
    current, kube, process = before_loss(tmp_path)
    request = envelope('loss'); result = current.dispatch(request)
    deletes = [call for call in kube.calls if call[0] == 'delete']
    assert len(deletes) == 1 and result['status'] == 'passed'
    args, body = deletes[0][:-1], deletes[0][-1]
    assert args[1] == '--raw' and '?dryRun=' not in args[2]
    assert body['preconditions']['uid'] == current.files.read('scope.json')['frontends'][0]['uid']
    assert body['gracePeriodSeconds'] == 2
    assert current.dispatch(request, observe=True) == result
    assert len([call for call in kube.calls if call[0] == 'delete']) == 1
    assert process.calls.count(('client', 'once')) == 1


@pytest.mark.parametrize('fault', ['no_sources', 'model_not_active', 'model_extra_call'])
def test_missing_stream_or_model_gate_never_deletes_a_pod(fault, tmp_path):
    current, kube, process = before_loss(tmp_path)
    original = process.start_client
    def changed(*args):
        original(*args)
        if fault == 'no_sources':
            path = tmp_path / 'loss.progress.json'; data = json.loads(path.read_bytes()); data['sources_seen'] = False; path.write_text(json.dumps(data))
        elif fault == 'model_not_active': kube.metrics['active'] = 0
        else: kube.metrics['started'] = 4
    process.start_client = changed
    with pytest.raises(ValueError): current.dispatch(envelope('loss'))
    assert not [call for call in kube.calls if call[0] == 'delete']


def test_client_pid_substitution_is_not_an_active_stream_gate(tmp_path):
    current, kube, process = before_loss(tmp_path)
    process.describe = lambda pid: {'pid': pid, 'start_ticks': 'foreign'}
    with pytest.raises(ValueError): current.dispatch(envelope('loss'))
    assert not [call for call in kube.calls if call[0] == 'delete']


def test_model_completion_during_scope_recheck_prevents_deletion(tmp_path):
    current, kube, _ = before_loss(tmp_path)
    original = kube.run
    def complete(*args, **kwargs):
        value = original(*args, **kwargs)
        if args[:2] == ('get','pod'): kube.metrics.update(active=0, finished=3)
        return value
    kube.run = complete
    with pytest.raises(ValueError): current.dispatch(envelope('loss'))
    assert not [call for call in kube.calls if call[0] == 'delete']


def after_loss(tmp_path):
    current, kube, process = before_loss(tmp_path)
    current.dispatch(envelope('loss'))
    victim = current.files.read('scope.json')['frontends'][0]
    pod = next(o for o in kube.objects if o['kind']=='Pod' and o['metadata']['uid']==victim['uid'])
    pod['metadata'].update(name='frontend-new', uid=uid('frontend-new'))
    for item in kube.objects:
        if item['kind']=='EndpointSlice':
            for endpoint in item['endpoints']:
                if endpoint['targetRef']['uid']==victim['uid']: endpoint['targetRef']['uid']=uid('frontend-new')
    kube.metrics.update(active=0, finished=3)
    progress = tmp_path/'loss.progress.json'; v=json.loads(progress.read_bytes());v.update(terminal=True,result_seen=False);progress.write_text(json.dumps(v))
    return current,kube,process


def test_native_loss_observation_replacement_logout_and_closure_finish_without_more_model_calls(tmp_path):
    current,kube,process = after_loss(tmp_path)
    observed = current.dispatch(envelope('loss-observe'))
    assert observed['status']=='passed' and observed['evidence']['victim_gone']
    assert current.dispatch(envelope('replacement'))['status']=='passed'
    assert current.dispatch(envelope('logout'))['status']=='passed'
    assert current.dispatch(envelope('final'))['status']=='passed'
    assert kube.metrics=={'started':3,'finished':3,'active':0}
    assert sum(call[0]=='delete' for call in kube.calls)==1
    assert process.calls.count(('forward','pf-0r'))==1
    assert all(current.files.path(slot+'.closed.json').exists() for slot in ['pf-0','pf-0r','pf-1','pf-2','client'])


def test_foreign_terminal_receipt_is_refused_before_parent_adoption(tmp_path):
    current,_=coordinator(tmp_path);request=envelope();current.dispatch(request)
    path=tmp_path/'native.preflight.result.json';receipt=json.loads(path.read_bytes());receipt['intent_id']='f'*32;path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):current.dispatch(request,observe=True)


def test_corrupted_prior_stage_does_not_authorize_a_later_loss(tmp_path):
    current,kube,_=before_loss(tmp_path)
    path=tmp_path/'native.export.result.json';receipt=json.loads(path.read_bytes());receipt['stage']='foreign';path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):current.dispatch(envelope('loss'))
    assert not [call for call in kube.calls if call[0]=='delete']


@pytest.mark.parametrize('outside',['postgres','qdrant','tls_mode','existing_files'])
def test_fresh_native_proof_rejects_unsafe_provider_before_any_sql_connection(outside,tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app import config
    from app.db import session
    selected=value();kube=Kube();engine_calls=[]
    ca=tmp_path/'test-ca.crt';ca.write_text('synthetic root, no actual TLS connection in this refusal test')
    directories={}
    for name in ['uploads_dir','okf_dir','staging_dir','exports_dir']:
        directories[name]=tmp_path/name;directories[name].mkdir()
    settings=SimpleNamespace(environment='development',app_role='combined',deployment_mode='compact',okf_deployment_schema=None,resilience_class='basic',
        deployment_namespace=selected['namespace'],db_url='postgresql+psycopg://lab@postgres/replica_test_abcdefgh',
        qdrant_url='https://qdrant:6333',qdrant_collection='replica_test_abcdefgh',artifact_backend='local',
        artifact_uploads_enabled=False,auth_provider='simulation',embedding_provider='fake',model_mode='stub',llm_base_url='http://model-stub:8000/v1',**directories)
    if outside=='postgres':settings.db_url='postgresql+psycopg://lab@customer.example/replica_test_abcdefgh'
    elif outside=='qdrant':settings.qdrant_url='https://customer.example:6333'
    settings.db_url+='?sslmode='+('require' if outside=='tls_mode' else 'verify-full')+'&sslrootcert='+ca.as_posix()
    if outside=='existing_files':(settings.uploads_dir/'existing.docx').write_bytes(b'existing corpus must be refused')
    monkeypatch.setattr(config,'get_settings',lambda:settings)
    def forbidden():engine_calls.append(True);raise AssertionError('SQL must not be reached')
    monkeypatch.setattr(session,'get_engine',forbidden)
    def probe(pod,source):
        with pytest.raises(AssertionError):exec(compile(source,'native-backend-proof.py','exec'),{})
        return {'fresh':False}
    kube.python=probe
    with pytest.raises(ValueError):module().Coordinator(tmp_path,selected,kube=kube).dispatch(envelope())
    assert engine_calls==[]


@pytest.mark.parametrize('fault',['external_name','foreign_stateful_owner'])
def test_provider_service_must_select_owned_stateful_pod_before_any_sql_probe(fault,tmp_path):
    current,kube=coordinator(tmp_path)
    if fault=='external_name':
        service=next(o for o in kube.objects if o['kind']=='Service' and o['metadata']['name']=='postgres')
        service['spec']['type']='ExternalName';service['spec']['externalName']='customer.example'
    else:
        pod=next(o for o in kube.objects if o['kind']=='Pod' and o['metadata']['name']=='postgres-0')
        pod['metadata']['ownerReferences'][0]['uid']=uid('foreign-provider')
    with pytest.raises(ValueError):current.dispatch(envelope())
    assert not any(c[0]=='native-read-only' for c in kube.calls)


def test_helm_prefixed_provider_services_are_discovered_within_the_owned_namespace(tmp_path):
    current,kube=coordinator(tmp_path)
    for item in kube.objects:
        if item['kind']=='Service' and item['metadata']['name'] in {'postgres','qdrant'}:
            item['metadata']['name']='training-storage-'+item['metadata']['name']
    current.dispatch(envelope())
    assert current.files.read('scope.json')['provider_services']=={'postgres':'training-storage-postgres','qdrant':'training-storage-qdrant'}
