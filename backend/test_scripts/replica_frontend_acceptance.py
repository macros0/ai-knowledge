"""Bounded live admission for two frontend Pods and one combined backend.

Local simulation observations do not qualify customer Gateway/IdP or turn
missing upload, stream and loss observations into a successful frontend suite.
"""
from __future__ import annotations

import json
import re

from test_scripts.replica_runtime_acceptance import Kubernetes, role_pods, validate_live_inputs


def _front(pod, value):
    if (pod.get('automountServiceAccountToken') is not False
            or any(pod.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC'))
            or len(pod.get('containers', [])) != 1):
        raise ValueError('Frontend isolation differs')
    for volume in pod.get('volumes', []):
        if not set(volume) <= {'name', 'emptyDir', 'configMap'} or len(set(volume) - {'name'}) != 1:
            raise ValueError('Frontend shared or credential volume refused')
    c = pod['containers'][0]
    if (c['name'] != 'frontend' or c.get('envFrom')
            or c.get('securityContext', {}).get('privileged') is True):
        raise ValueError('Frontend runtime secret projection refused')
    env = {e['name']: e for e in c.get('env', [])}
    if len(env) != len(c.get('env', [])):
        raise ValueError('Duplicate frontend environment')
    if any(env.get(k, {}).get('value') != 'http://backend:8000'
           for k in ('BACKEND_URL', 'DIAGNOSTICS_INGEST_URL')):
        raise ValueError('Frontend backend binding differs')
    expected = {'name': value['runtime_secret'], 'key': 'DIAGNOSTICS_INGEST_TOKEN'}
    if env.get('DIAGNOSTICS_INGEST_TOKEN', {}).get('valueFrom') != {'secretKeyRef': expected}:
        raise ValueError('Diagnostic credential binding differs')
    if any(e.get('valueFrom', {}).get('secretKeyRef') and name != 'DIAGNOSTICS_INGEST_TOKEN'
           for name, e in env.items()):
        raise ValueError('Extra frontend credential refused')
    if any(re.search(r'PASSWORD|SECRET|TOKEN|API_KEY|DATABASE_URL', name) and name != 'DIAGNOSTICS_INGEST_TOKEN'
           for name in env):
        raise ValueError('Frontend credential environment refused')


def validate_frontend_inventory(objects, value):
    """Validate observed ownership, templates, native builds and Service targets."""
    try:
        validate_live_inputs(value)
        if value.get('deployment_mode') != 'frontend-split':
            raise ValueError('Explicit frontend-split mode required')
        if not re.fullmatch(r'[0-9a-f-]{36}', value['namespace_uid']):
            raise ValueError('Namespace UID required')
        images = value['expected_image_ids']
        if set(images) != {'backend', 'frontend'} or any(not re.fullmatch(r'sha256:[0-9a-f]{64}', s) for s in images.values()):
            raise ValueError('Pinned native image IDs required')
        spaces = [o for o in objects if o['kind']=='Namespace']
        if len(spaces) != 1:
            raise ValueError('One namespace observation required')
        meta = spaces[0]['metadata']
        labels = meta.get('labels', {})
        if (meta['uid'] != value['namespace_uid'] or meta['name'] != value['namespace'] or meta.get('deletionTimestamp')
                or labels.get('okf-run-id') != value['run_id'] or labels.get('okf-purpose') != 'replica-test'
                or labels.get('app.kubernetes.io/managed-by') != 'okf-replica-acceptance'
                or labels.get('okf-installation-id') != value['installation_id']):
            raise ValueError('Namespace identity differs')
        deployments = {o['metadata']['name']: o for o in objects if o['kind']=='Deployment'}
        if not {'application', 'frontend'} <= set(deployments) or set(deployments) - {'application', 'frontend', 'model-stub'}:
            raise ValueError('Single combined topology required')
        expected = {}
        for name, role, count in [('application', 'application', 1), ('frontend', 'frontend', 2)]:
            deployment = deployments[name]
            meta, spec, status = deployment['metadata'], deployment['spec'], deployment.get('status', {})
            if (meta.get('labels', {}).get('app.kubernetes.io/instance') != value['application_release']
                    or meta['labels'].get('okf-run-id') != value['run_id'] or spec['replicas'] != count
                    or status.get('observedGeneration') != meta['generation']
                    or any(status.get(k) != count for k in ('readyReplicas', 'updatedReplicas', 'availableReplicas'))):
                raise ValueError('Split deployment is not ready')
            template = spec['template']['spec']
            if name == 'frontend':
                _front(template, value)
            elif [c['name'] for c in template['containers']] != ['backend']:
                raise ValueError('Combined backend must have one container')
            pods = role_pods(objects, role)
            if len(pods) != count:
                raise ValueError('Unexpected role Pod count')
            expected[role] = set()
            for pod in pods:
                meta, ps = pod['metadata'], pod.get('status', {})
                cs = ps.get('containerStatuses', [])
                image = images['frontend' if role=='frontend' else 'backend']
                if (meta.get('labels', {}).get('app.kubernetes.io/instance') != value['application_release']
                        or meta['labels'].get('okf-run-id') != value['run_id'] or ps.get('phase') != 'Running'
                        or len(cs) != 1 or cs[0].get('ready') is not True or cs[0].get('restartCount') != 0
                        or not cs[0].get('imageID', '').endswith(image)):
                    raise ValueError('Role Pod readiness/build differs')
                owners = meta.get('ownerReferences', [])
                rs = [o for o in objects if o['kind']=='ReplicaSet' and len(owners)==1
                      and owners[0]['kind']=='ReplicaSet' and o['metadata']['uid']==owners[0]['uid']]
                if (len(rs) != 1 or len(rs[0]['metadata'].get('ownerReferences', [])) != 1
                        or rs[0]['metadata']['ownerReferences'][0].get('kind') != 'Deployment'
                        or rs[0]['metadata']['ownerReferences'][0].get('uid') != deployment['metadata']['uid']):
                    raise ValueError('Pod controller ownership differs')
                if role == 'frontend':
                    _front(pod['spec'], value)
                expected[role].add(meta['uid'])
        for name, role, port in [('frontend', 'frontend', 3000), ('backend', 'application', 8000)]:
            services = [o for o in objects if o['kind']=='Service' and o['metadata']['name']==name]
            if (len(services) != 1 or services[0]['spec']['selector'] != {
                    'okf-role': role, 'app.kubernetes.io/instance': value['application_release']}
                    or [(p['port'], p['targetPort']) for p in services[0]['spec']['ports']] != [(port, port)]):
                raise ValueError('Service selects the wrong role')
            endpoints = set()
            for obj in objects:
                if obj['kind'] != 'EndpointSlice' or obj['metadata'].get('labels', {}).get('kubernetes.io/service-name') != name:
                    continue
                owners = obj['metadata'].get('ownerReferences', [])
                if len(owners) != 1 or owners[0].get('kind') != 'Service' or owners[0].get('uid') != services[0]['metadata']['uid']:
                    raise ValueError('EndpointSlice ownership differs')
                for endpoint in obj.get('endpoints', []):
                    if endpoint.get('conditions', {}).get('ready') is not True:
                        continue
                    ref = endpoint.get('targetRef', {})
                    if ref.get('kind') != 'Pod' or ref.get('uid') not in expected[role] or endpoint['conditions'].get('terminating'):
                        raise ValueError('Foreign or terminating Service endpoint')
                    endpoints.add(ref['uid'])
            if endpoints != expected[role]:
                raise ValueError('Service membership incomplete')
        return dict(frontend_replicas=2, combined_replicas=1, shared_frontend_pvcs=0,
                    frontend_pod_uids=sorted(expected['frontend']), backend_pod_uid=next(iter(expected['application'])))
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ValueError('Malformed frontend-split observation') from None


def probe_frontend(value, *, ssh_host=None, known_hosts=None):
    """Read-only topology gate; every unobserved functional scenario stays pending."""
    checks = []
    try:
        validate_live_inputs(value)
        if value.get('deployment_mode') != 'frontend-split':
            raise ValueError('Explicit frontend-split mode required')
        kube = Kubernetes(value, ssh_host=ssh_host, known_hosts=known_hosts)
        kube.scope()
        namespace = json.loads(kube.run('get', 'namespace', value['namespace'], '-o', 'json'))
        objects = json.loads(kube.run('get', 'deployments,replicasets,pods,services,endpointslices', '-o', 'json'))['items']
        validate_frontend_inventory([namespace, *objects], value)
        checks.append(dict(name='frontend.split_inventory', status='passed'))
    except Exception:  # Scope/provider details never enter the public report.
        checks.append(dict(name='frontend.split_inventory', status='failed', reason='frontend_probe_refused'))
    checks.extend(dict(name='frontend.'+name, status='pending', reason='native_scenario_not_observed') for name in (
        'session_csrf_upload_logout', 'streaming', 'frontend_loss', 'gateway_sso'))
    return checks
