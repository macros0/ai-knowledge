"""Source guards and explicit S4 fence for the owned basic rpl04 backup recipe.

Callers retain an offline operator and snapshot connection and fetch Kubernetes,
nodes and CRI afresh on every guard call. No saved digest alone proves a stop.
The explicit CLI can stop the owned synthetic source and export its providers;
it never releases the source fence or claims a successful restoration.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import export_postgres, validate_config
from test_scripts.replica_provider_stop import (
    observation_digest,
    stopped_cri_facts,
    stopped_facts,
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def controller_snapshot(items, value):
    controllers, pods = [], []
    for obj in items:
        kind, meta = obj.get('kind'), obj.get('metadata', {})
        if kind in {'Deployment', 'StatefulSet'}:
            generation = meta.get('generation')
            if not meta.get('uid') or type(generation) is not int or generation < 1:
                raise ValueError('Explicit controller UID and generation required')
            controllers.append({'kind': kind, 'name': meta['name'], 'uid': meta['uid'],
                'generation': generation, 'spec_sha256': _digest(obj['spec']),
                'labels_sha256': _digest(meta.get('labels', {}))})
        elif kind == 'Pod' and meta.get('labels', {}).get('app.kubernetes.io/instance') == value['storage_release']:
            if not meta.get('uid'):
                raise ValueError('Explicit storage Pod UID required')
            pods.append({'name': meta['name'], 'uid': meta['uid'], 'spec_sha256': _digest(obj['spec']),
                'containers': [{key: c.get(key) for key in ('name', 'imageID', 'containerID')}
                    for c in obj.get('status', {}).get('containerStatuses', [])]})
    if len(controllers) != 7 or len(pods) != 3:
        raise ValueError('Exact four application and three storage controllers/Pods required')
    return _digest({'controllers': sorted(controllers, key=lambda o: (o['kind'], o['name'])),
        'storage_pods': sorted(pods, key=lambda o: o['name'])})


def predecessor_stop_observation(items, nodes, containers, value, binding):
    if any(obj.get('kind') in {'HorizontalPodAutoscaler', 'VerticalPodAutoscaler'} for obj in items):
        raise ValueError('Autoscaled predecessor sources are not supported')
    snapshot = controller_snapshot(items, value)
    if binding.get('controller_snapshot_sha256') != snapshot:
        raise ValueError('Stopped controller generation/spec or storage binding changed')
    facts = stopped_facts(items, nodes, value, binding)
    cri = stopped_cri_facts(containers, value, binding)
    return _digest({'controllers': snapshot, 'physical': observation_digest(facts, cri)})


def validate_predecessor_snapshot(snapshot, config, fingerprint, *, operation_id=None):
    validate_config(config)
    if (operation_id is not None and (not re.fullmatch('[0-9a-f]{32}', operation_id) or operation_id == '0' * 32)
            or not re.fullmatch('[0-9a-f]{64}', fingerprint)
            or snapshot['database'] != config['database']
            or snapshot['schema_revisions'] != ['rpl04_runtime_identity']
            or snapshot['maintenance_table'] is not False or snapshot['work_maintenance_epoch'] is not False
            or len(snapshot['identities']) != 1 or len(snapshot['controls']) != 1
            or any(type(snapshot[key]) is not int or snapshot[key] != 0 for key in (
                'active_work', 'active_chats', 'owned_model_slots', 'deleting_artifacts'))):
        raise ValueError('Exact quiescent predecessor schema and database required')
    identity, control = snapshot['identities'][0], snapshot['controls'][0]
    descriptor = control.get('active_descriptor')
    expected = {'backend': 's3', 'store_id': config['store_id'], 'bucket': config['bucket'],
        'prefix': config['prefix'],
        'endpoint': f"https://{config['storage_release']}-artifacts.{config['namespace']}.svc:9000"}
    if (identity.get('id') != 1 or identity.get('installation_id') != config['installation_id']
            or identity.get('fingerprint') != fingerprint or identity.get('schema_revision') != 'rpl04_runtime_identity'
            or not re.fullmatch('sha256:[0-9a-f]{64}', identity.get('storage_receipt_digest', ''))
            or control.get('id') != 1 or control.get('installation_id') != config['installation_id']
            or control.get('active_store_id') != config['store_id'] or control.get('maintenance_token') != operation_id
            or control.get('migration_id') is not None or control.get('readonly_roots') != []
            or not isinstance(descriptor, dict) or any(descriptor.get(key) != item for key, item in expected.items())):
        raise ValueError('Predecessor identity or storage scope differs')
    return dict(identity), dict(control)


def predecessor_sql_source(connection, config, fingerprint, *, operation_id=None):
    from sqlalchemy import inspect, text
    if connection.dialect.name != 'postgresql':
        raise ValueError('Predecessor source requires an isolated PostgreSQL connection')
    inspector = inspect(connection)
    snapshot = {'database': connection.scalar(text('SELECT current_database()')),
        'schema_revisions': connection.execute(text('SELECT version_num FROM alembic_version')).scalars().all(),
        'maintenance_table': inspector.has_table('replica_maintenance_state'),
        'work_maintenance_epoch': any(c['name'] == 'maintenance_epoch' for c in inspector.get_columns('work_items')),
        'identities': [dict(row) for row in connection.execute(text('SELECT * FROM replica_runtime_identity')).mappings()],
        'controls': [dict(row) for row in connection.execute(text('SELECT * FROM artifact_storage_control')).mappings()]}
    predicates = {
        'active_work': "work_items WHERE status='running' OR owner_id IS NOT NULL OR lease_until IS NOT NULL",
        'active_chats': "chat_runs WHERE status='running'",
        'owned_model_slots': "replica_capacity_slots WHERE resource='llm' AND (owner_id IS NOT NULL OR release_required)",
        'deleting_artifacts': "artifact_objects WHERE state='deleting'",
    }
    snapshot.update({key: connection.scalar(text('SELECT count(*) FROM ' + statement))
        for key, statement in predicates.items()})
    return validate_predecessor_snapshot(snapshot, config, fingerprint, operation_id=operation_id)


def hold_predecessor_source(session, config, fingerprint, *, operation_id):
    """Commit an existing S4 token before drain/export; never manufacture epoch1."""
    from app.db.models import ArtifactStorageControl
    from app.deployment.runtime_upgrade import _recorded
    from app.services.work.maintenance_control import _require_operator
    op = _require_operator(session, operation_id)
    if operation_id == '0' * 32:
        raise ValueError('Reserved predecessor operation id')
    if op.settings is None or op.settings.deployment_namespace != config['namespace']:
        raise ValueError('Owned predecessor audit settings required')
    control = session.get(ArtifactStorageControl, 1, with_for_update=True)
    if control is None or control.maintenance_token not in {None, operation_id}:
        raise ValueError('Another predecessor storage operation is active')
    predecessor_sql_source(session.connection(), config, fingerprint,
        operation_id=operation_id if control.maintenance_token else None)
    recorded = _recorded(session, operation_id, 0, action='replica_maintenance_enter', target_type='predecessor_artifact_fence')
    if control.maintenance_token == operation_id:
        if not recorded:
            raise ValueError('Predecessor fence audit is missing')
        return
    if recorded:
        raise ValueError('A completed predecessor operation cannot be reused')
    control.maintenance_token = operation_id
    from app.services import audit
    from app.services.audit_event import AuditContext
    audit.record_in_session(session, action_type='replica_maintenance_enter', user_id='system', username='system',
        target_type='predecessor_artifact_fence', target_id=operation_id, new_value={'epoch': 0},
        event_context=AuditContext(actor_type='system'), settings=op.settings)
    session.flush()


def predecessor_archive_guard(plan, *, physical_observation, sql_guard, snapshot_guard):
    """Re-observe every authority on each archive publication boundary."""
    from app.deployment.replica_backup import PreMaintenanceSourceState
    if not isinstance(plan.source, PreMaintenanceSourceState) or plan.schema_revision != 'rpl04_runtime_identity':
        raise ValueError('Explicit predecessor archive required')
    source = plan.source

    def guard():
        sql_guard()  # Retained operator, exact current S4 token and old identity.
        snapshot_guard()  # The original exported snapshot connection, not a replacement.
        if physical_observation() != source.stop_proof.process_observation_sha256:
            raise ValueError('Predecessor physical stop changed during export')
        return source

    return guard


def retained_snapshot_guard(connection):
    """A reused connection/PID cannot stand in for the original transaction."""
    from sqlalchemy import text
    if connection.closed or connection.invalidated or not connection.in_transaction():
        raise ValueError('Original predecessor snapshot transaction is missing')
    transaction = connection.get_transaction()
    driver = connection.connection.driver_connection
    backend_pid = connection.scalar(text('SELECT pg_backend_pid()'))

    def guard():
        if (connection.closed or connection.invalidated or connection.get_transaction() is not transaction
                or not transaction.is_active or connection.connection.driver_connection is not driver
                or connection.scalar(text('SELECT pg_backend_pid()')) != backend_pid):
            raise ValueError('Original predecessor snapshot transaction was lost')

    return guard


def export_predecessor_archive(plan, archive, key, *, source_guard, destination_guard,
        postgres_export, object_open, finish_clock):
    """Copy actual providers into a create-only encrypted independent archive.

    This explicit predecessor path never changes the schema, releases the S4
    fence or starts a workload. Provider errors retain an incomplete archive.
    """
    from app.deployment.replica_backup import (
        BackupWriter,
        PreMaintenanceSourceState,
        verify_backup,
    )
    if not isinstance(plan.source, PreMaintenanceSourceState) or plan.schema_revision != 'rpl04_runtime_identity':
        raise ValueError('Explicit predecessor archive required')
    with BackupWriter(archive, plan, key, source_guard=source_guard, destination_guard=destination_guard) as writer:
        postgres = next(e for e in plan.inventory if e.component == 'postgres')
        postgres_export(writer, postgres)
        for entry in plan.inventory:
            if entry.component != 'postgres':
                with object_open(entry) as stream:
                    writer.add(entry.id, stream)
        writer.finish(now=finish_clock())
    return verify_backup(archive, key)


def fetch_predecessor_observations(lab):
    """One fresh node-side collection; a timeout is never a successful stop."""
    from replica_worker_loss import remote
    lab.assert_namespace()
    code = '''import json,subprocess,sys
ns,uid=sys.argv[1:]
k=['kubectl','--context','kind-okf-policy','-n',ns]
def get(*args):return json.loads(subprocess.check_output(k+list(args),timeout=15))
namespace=get('get','namespace',ns,'-o','json')
assert namespace['metadata']['uid']==uid and namespace['metadata']['labels']['okf-purpose']=='replica-test'
resources=subprocess.check_output(['kubectl','--context','kind-okf-policy','api-resources','--namespaced=true','--verbs=list','-o','name'],timeout=15).decode().splitlines()
selected='deploy,sts,rs,ds,rc,pods,jobs,cronjobs,services,endpointslices,hpa'
if any(r.startswith('verticalpodautoscalers.') for r in resources):selected+=',verticalpodautoscalers'
items=get('get',selected,'-o','json')['items']+[namespace]
nodes=get('get','nodes','-o','json')['items']
node=json.loads(subprocess.check_output(['docker','inspect','okf-policy-control-plane'],timeout=10))[0]
assert node['Config']['Labels']['io.x-k8s.kind.cluster']=='okf-policy' and node['State']['Running']
containers=json.loads(subprocess.check_output(['docker','exec','okf-policy-control-plane','crictl','ps','-a','-o','json'],timeout=10))['containers']
print(json.dumps({'items':items,'nodes':nodes,'containers':containers}))
'''
    result = remote(['python3', '-c', code, lab.config['namespace'], lab.namespace_uid])
    return result['items'], result['nodes'], result['containers']


def run_predecessor_backup(config, *, namespace_uid, fingerprint, evidence_file, archive,
        key_file, pg_dump, report, operation_journal):
    """Create one real rpl04 backup, leaving its audited source stopped and held."""
    import os
    import shutil
    import subprocess
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace
    from uuid import UUID, uuid4

    from app.deployment.replica_backup import BackupPlan, canonical
    from app.services.work.maintenance_control import (
        REQUIRED_WRITERS,
        MaintenanceOperator,
    )
    from replica_backup import (
        ROOT,
        Lab,
        ResponseStream,
        connections,
        destination_binding,
        postgres_dump_command,
        runtime_config_name,
    )
    from replica_runtime_run import write_json
    from sqlalchemy import text
    from test_scripts.replica_runtime_acceptance import Kubernetes, role_pods
    from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent

    validate_config(config)  # Before provider access, disk mutation or fencing.
    UUID(namespace_uid)
    if not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise ValueError('Explicit old runtime fingerprint required')
    archive, report, operation_journal = Path(archive), Path(report), Path(operation_journal)
    private = Path(config['ca_file']).resolve(strict=True).parent
    if (operation_journal.resolve().parent != private or operation_journal.exists()
            or operation_journal.resolve().is_relative_to(archive.resolve())
            or report.resolve().parent != ROOT / 'docs/superpowers/reports' or report.exists()):
        raise ValueError('New protected operation journal and public report required')
    destination = destination_binding(evidence_file, archive)
    key = Path(key_file).read_bytes()
    if len(key) != 32 or Path(key_file).resolve().is_relative_to(archive.resolve()):
        raise ValueError('Separate protected backup key required')
    if archive.exists() or shutil.disk_usage(archive.parent).free < 1024**3:
        raise ValueError('New independent archive destination with capacity required')
    version = subprocess.run([str(pg_dump), '--version'], capture_output=True, timeout=10, check=True).stdout
    if b'(PostgreSQL) 17.' not in version:
        raise ValueError('Selected PostgreSQL17 exporter required')
    lab = Lab(config, namespace_uid)
    value = {**config, 'namespace_uid': namespace_uid,
        'frontend_url': f"http://127.0.0.1:{config['ports']['frontend']}", 'simulation_username': 'replica.admin'}
    kube = Kubernetes(value, ssh_host='ubuntu@10.10.1.52', known_hosts=ROOT / 'tests/tmp/replica-lab-known_hosts')
    probe = FaultProbe(kube, value)
    require_quiescent(probe)
    if probe.identity != {'fingerprint': fingerprint, 'schema_revision': 'rpl04_runtime_identity'}:
        raise ValueError('Actual source is not the expected historical runtime')
    items, nodes, _ = fetch_predecessor_observations(lab)
    deployments = {o['metadata']['name']: o for o in items if o['kind'] == 'Deployment'}
    if (set(deployments) != {'frontend', 'api', 'worker', 'model-stub'}
            or any(o['spec']['replicas'] != (1 if r == 'model-stub' else 2) for r, o in deployments.items())
            or any(o['kind'] in {'HorizontalPodAutoscaler', 'VerticalPodAutoscaler', 'DaemonSet', 'ReplicationController'}
                or o['kind'] == 'CronJob' and o['spec'].get('suspend') is not True
                or o['kind'] == 'Job' and not o.get('status', {}).get('succeeded') for o in items)):
        raise ValueError('Exact non-autoscaled source topology required')
    runtime_name = runtime_config_name(deployments)
    runtime = json.loads(lab.kubectl('get', 'configmap', runtime_name, '-o', 'json'))
    if runtime.get('immutable') is not True:
        raise ValueError('Immutable selected source settings required')
    actual = kube.python(probe.api_pods()[0]['metadata']['name'], '''import json
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
s=get_settings();identity=enforce_runtime_identity(s)
print(json.dumps({'fingerprint':identity.fingerprint,'audit_db_enabled':s.audit_db_enabled,
 'audit_stdout_mode':s.audit_stdout_mode,'namespace':s.deployment_namespace}))
''')
    if (actual.get('fingerprint') != fingerprint or actual.get('namespace') != config['namespace']
            or type(actual.get('audit_db_enabled')) is not bool or not actual['audit_db_enabled']
            or actual.get('audit_stdout_mode') != 'disabled'):
        raise ValueError('Selected predecessor audit/runtime scope differs')
    audit_settings = SimpleNamespace(okf_deployment_schema='replicas-v1', app_role='api',
        deployment_namespace=config['namespace'], audit_db_enabled=True, audit_stdout_mode='disabled')
    writers = [p for r in ('api', 'worker', 'frontend') for p in role_pods(items, r)]
    models = role_pods(items, 'model-stub')
    if len(writers) != 6 or len(models) != 1 or {p['spec']['nodeName'] for p in writers + models} != {'okf-policy-control-plane'}:
        raise ValueError('Exact owned single-node process bindings required')
    service = next(o for o in items if o['kind'] == 'Service' and o['metadata']['name'] == 'model-stub')
    node_uids = {n['metadata']['name']: n['metadata']['uid'] for n in nodes}
    binding = {'service_uid': service['metadata']['uid'], 'service_selector': service['spec']['selector'],
        'nodes': {'okf-policy-control-plane': node_uids['okf-policy-control-plane']},
        'deployments': {r: o['metadata']['uid'] for r, o in deployments.items()},
        'writer_pod_uids': [p['metadata']['uid'] for p in writers], 'provider_pod_uid': models[0]['metadata']['uid']}
    images = {}
    for role in ('api', 'worker', 'frontend'):
        selected = {p['status']['containerStatuses'][0]['imageID'][-71:] for p in role_pods(items, role)}
        if len(selected) != 1:
            raise ValueError('Ambiguous source image digest')
        images[role] = selected.pop()
    engine, s3, q = connections(config)
    operation = uuid4().hex
    try:
        with engine.connect() as connection:
            predecessor_sql_source(connection, config, fingerprint)
            started = connection.scalar(text('SELECT clock_timestamp()'))
        journal = {'version': 1, 'phase': 'predecessor-hold-intent', 'operation_id': operation,
            'namespace': config['namespace'], 'namespace_uid': namespace_uid, 'installation_id': config['installation_id'],
            'fingerprint': fingerprint, 'original_deployments': deployments, 'binding': binding,
            'images': images, 'started_at': started.isoformat(), 'active_role': None,
            'source_epoch': 0, 'production_schedule': 'disabled', 'restore_verified': False}
        # Exclusive initial publication prevents two invocations sharing a journal.
        from app.deployment.replica_backup import _sync_directory
        with operation_journal.open('x', encoding='utf-8') as output:
            json.dump(journal, output); output.flush(); os.fsync(output.fileno())
        _sync_directory(operation_journal.parent)
        with MaintenanceOperator(engine, operation_id=operation, owner_id='owned-predecessor-backup', settings=audit_settings) as op:
            with op.transaction() as session:
                hold_predecessor_source(session, config, fingerprint, operation_id=operation)
            journal['phase'] = 'predecessor-held'
            write_json(operation_journal, journal)
            print(json.dumps({'phase': journal['phase'], 'operation_id': operation, 'source_epoch': 0}), flush=True)
            for role in ('frontend', 'api', 'worker', 'model-stub'):
                current = json.loads(lab.kubectl('get', 'deployment', role, '-o', 'json'))
                if current['metadata']['uid'] != deployments[role]['metadata']['uid'] or current['spec'] != deployments[role]['spec']:
                    raise ValueError('Source controller changed before normal stop')
                journal.update(phase='predecessor-stopping-writers', active_role=role)
                write_json(operation_journal, journal)
                lab.scale(role, 1 if role == 'model-stub' else 2, 0)
                journal['active_role'] = None
                write_json(operation_journal, journal)
            deadline = time.monotonic() + 180
            while True:
                stopped, stopped_nodes, containers = fetch_predecessor_observations(lab)
                candidate = {**binding, 'controller_snapshot_sha256': controller_snapshot(stopped, value)}
                try:
                    observation = predecessor_stop_observation(stopped, stopped_nodes, containers, value, candidate)
                    break
                except ValueError:
                    if time.monotonic() >= deadline:
                        raise ValueError('Predecessor physical stop unverified; S4 fence remains held') from None
                    time.sleep(2)
            # No template other than replica counts may change during normal stop.
            for obj in stopped:
                if obj['kind'] == 'Deployment':
                    original = deployments[obj['metadata']['name']]
                    expected = {**original['spec'], 'replicas': 0}
                    if obj['metadata']['uid'] != original['metadata']['uid'] or obj['spec'] != expected:
                        raise ValueError('Stopped predecessor template changed')
            binding = candidate
            journal.update(phase='predecessor-writers-stopped', binding=binding, physical_stop_sha256=observation)
            write_json(operation_journal, journal)
            with engine.connect().execution_options(isolation_level='REPEATABLE READ') as frozen:
                frozen.execute(text('SET TRANSACTION READ ONLY'))
                observed = frozen.scalar(text('SELECT clock_timestamp()'))
                snapshot = frozen.scalar(text('SELECT pg_export_snapshot()'))
                backend_pid = frozen.scalar(text('SELECT pg_backend_pid()'))
                snapshot_guard = retained_snapshot_guard(frozen)
                consistent = frozen.scalar(text('SELECT clock_timestamp()'))
                identity, _ = predecessor_sql_source(frozen, config, fingerprint, operation_id=operation)
                artifacts = frozen.execute(text("SELECT * FROM artifact_objects WHERE state='ready' ORDER BY object_id LIMIT 100001")).mappings().all()
                if len(artifacts) > 99998 or any(a['store_id'] != config['store_id'] or a['key'] != 'objects/' + a['object_id'] for a in artifacts):
                    raise ValueError('Predecessor artifact inventory exceeds or differs from selected scope')
                counts = {key: frozen.scalar(text('SELECT count(*) FROM ' + table)) for key, table in {
                    'documents': 'documents', 'concepts': 'okf_concepts', 'chunks': 'document_chunks',
                    'work_items': 'work_items', 'audit_events': 'audit_log'}.items()}
                counts['blobs'] = len(artifacts)
                response = q.post('/collections/' + config['collection'] + '/snapshots?wait=true')
                response.raise_for_status()
                qsnap = response.json()['result']
                if not re.fullmatch('[A-Za-z0-9_.-]+', qsnap['name']):
                    raise ValueError('Unsafe Qdrant snapshot identity')
                source_scope = {'installation_id': config['installation_id'], 'namespace': config['namespace'],
                    'operation_id': operation, 'namespace_uid': namespace_uid, 'schema_revision': identity['schema_revision'],
                    'runtime_fingerprint_sha256': _digest(fingerprint)}
                plan = BackupPlan.model_validate({'format': 3, 'backup_id': operation, 'schema_revision': identity['schema_revision'],
                    'started_at': started, 'source': {'installation_id': config['installation_id'], 'namespace': config['namespace'],
                        'operation_id': operation, 'epoch': 0, 'consistent_at': consistent,
                        'failure_domain': json.loads(Path(evidence_file).read_bytes())['source_failure_domain'],
                        'unknown_writers': 0, 'provider_requests': 0, 'writers': {r: 'stopped' for r in REQUIRED_WRITERS},
                        'snapshot_id': snapshot, 'fencing_kind': 'owned-kind-normal-stop-rpl04-v1',
                        'stop_proof': {'recipe': 'owned-kind-normal-stop-rpl04-v1', 'namespace_uid': namespace_uid,
                            'controller_snapshot_sha256': binding['controller_snapshot_sha256'],
                            'process_observation_sha256': observation, 'runtime_fingerprint_sha256': _digest(fingerprint),
                            'artifact_fence_operation_id': operation, 'source_scope_sha256': hashlib.sha256(canonical(source_scope)).hexdigest(),
                            'observed_at': observed, 'unknown_writers': 0, 'unreachable_nodes': 0, 'provider_requests': 0}},
                    'destination': destination, 'images': images, 'counts': counts,
                    'storage': [{'component': r, 'provider': 'managed', 'recipe': 'selected-lab-basic-v1',
                        'scope_sha256': _digest([config['namespace'], config['storage_release'], config.get(r, config['bucket'])])}
                        for r in ('postgres', 'qdrant', 'artifacts')], 'model_vector_sha256': _digest(fingerprint),
                    'component_receipts': {'postgres': _digest([snapshot, consistent]), 'qdrant': _digest(qsnap),
                        'artifacts': _digest([dict(a) for a in artifacts])}, 'key_reference': 'local-protected:training-backup-key',
                    'inventory': [{'id': uuid4().hex, 'component': 'postgres'}, {'id': uuid4().hex, 'component': 'qdrant',
                        'expected_size': qsnap.get('size'), 'expected_sha256': qsnap.get('checksum')}]
                        + [{'id': a['object_id'], 'component': 'artifacts', 'expected_size': a['size'], 'expected_sha256': a['sha256']} for a in artifacts]})

                def sql_guard():
                    with op.transaction() as session:
                        predecessor_sql_source(session.connection(), config, fingerprint, operation_id=operation)
                        from app.deployment.runtime_upgrade import _recorded
                        if not _recorded(session, operation, 0, action='replica_maintenance_enter', target_type='predecessor_artifact_fence'):
                            raise ValueError('Owned predecessor fence audit disappeared')

                def physical():
                    return predecessor_stop_observation(*fetch_predecessor_observations(lab), value, binding)

                guard = predecessor_archive_guard(plan, physical_observation=physical, sql_guard=sql_guard, snapshot_guard=snapshot_guard)
                artifact_rows = {a['object_id']: a for a in artifacts}

                @contextmanager
                def object_open(entry):
                    if entry.component == 'qdrant':
                        with q.stream('GET', '/collections/' + config['collection'] + '/snapshots/' + qsnap['name']) as response:
                            response.raise_for_status()
                            yield ResponseStream(response)
                    else:
                        artifact = artifact_rows[entry.id]
                        body = s3.get_object(Bucket=config['bucket'], Key=config['prefix'] + artifact['key'])['Body']
                        try:
                            yield body
                        finally:
                            body.close()

                environment = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
                journal.update(phase='predecessor-exporting', plan=plan.model_dump(mode='json'), snapshot_backend_pid=backend_pid)
                write_json(operation_journal, journal)
                cold = export_predecessor_archive(plan, archive, key, source_guard=guard,
                    destination_guard=lambda: destination_binding(evidence_file, archive),
                    postgres_export=lambda writer, entry: export_postgres(writer, entry.id, postgres_dump_command(config, snapshot),
                        environment, private / (operation + '-predecessor-pg-dump.log')),
                    object_open=object_open, finish_clock=lambda: frozen.scalar(text('SELECT clock_timestamp()')))
                guard()
            journal.update(phase='predecessor-archive-verified-source-held', counts=counts,
                encrypted_objects=len(cold.objects), archive=str(archive.absolute()))
            write_json(operation_journal, journal)
        public = {'status': 'independent-predecessor-backup-verified-source-held', 'format': 3, 'backup_id': operation,
            'namespace': config['namespace'], 'namespace_uid': namespace_uid, 'schema_revision': identity['schema_revision'],
            'source_epoch': 0, 'source_fence': 'owned S4 token plus fresh normal-stop/node/CRI observations',
            'runtime_configmap': runtime_name, 'runtime_fingerprint': fingerprint, 'images': images,
            'consistent_at': consistent.isoformat(), 'counts': counts, 'encrypted_objects': len(cold.objects),
            'plaintext_bytes': sum(o.size for o in cold.objects.values()), 'archive': str(archive.absolute()),
            'application_replicas': 0, 'source_storage_replicas': 3, 'source_fence_retained': True,
            'production_schedule': 'disabled', 'restore_verified': False, 'schema_update_verified': False}
        with report.open('x', encoding='utf-8') as output:
            json.dump(public, output, indent=2); output.write('\n')
        print(json.dumps(public), flush=True)
        return cold
    finally:
        q.close(); s3.close(); engine.dispose()


def main(argv=None):
    import argparse
    import traceback
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('config', 'evidence-file', 'archive', 'key-file', 'pg-dump', 'report', 'operation-journal'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--namespace-uid', required=True)
    parser.add_argument('--fingerprint', required=True)
    args = parser.parse_args(argv)
    try:
        from replica_runtime_run import read_json
        config = read_json(args.config, limit=16384)
        run_predecessor_backup(config, namespace_uid=args.namespace_uid, fingerprint=args.fingerprint,
            evidence_file=args.evidence_file, archive=args.archive, key_file=args.key_file, pg_dump=args.pg_dump,
            report=args.report, operation_journal=args.operation_journal)
        return 0
    except Exception as exc:  # noqa: BLE001 -- provider exceptions can contain credentials; emit fixed metadata only.
        with (args.config.parent / 'predecessor-backup-error.log').open('w', encoding='utf-8') as output:
            traceback.print_exc(file=output)
        print(json.dumps({'predecessor_backup_refused': type(exc).__name__, 'source_release_attempted': False,
            'frames': [{'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(exc.__traceback__)
                if Path(f.filename).resolve() == Path(__file__).resolve()]}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
