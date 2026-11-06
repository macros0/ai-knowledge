"""Admission for an owned ambiguous model call; no slot release authority here.

Historical receipt validation never executes the parent with an old code digest.
Physical recovery must independently re-observe scope, processes and exact token.
"""
from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'backend'), str(Path(__file__).resolve().parent)]

from replica_provider_gate import aware
from replica_provider_outage import PolicyActions, ProviderRun
from replica_runtime_run import OwnedAdapter, digest, read_json, recipe_digest, write_json
from replica_worker_loss import StopObservation, WorkerLoss
from test_scripts.replica_provider_faults import (
    RECIPE, TRANSIENT, assert_network, hexadecimal, publication, require, uid, work_row, works_by_id,
)
from test_scripts.replica_runtime_acceptance import role_pods


def admit_quarantine(parent, proof, *, parent_sha256, expected):
    """Check an immutable parent history and a separately collected live proof."""
    require(isinstance(parent, dict) and isinstance(proof, dict) and isinstance(expected, dict))
    require(hexadecimal(parent.get('code_sha256'), 64) and hexadecimal(parent_sha256, 64))
    history = ProviderRun.__new__(ProviderRun)
    history.state, history.code = copy.deepcopy(parent), parent['code_sha256']
    try:
        history.validate()  # Structural verification only, no adapter or execute.
    except Exception:
        raise ValueError('Historical provider receipts are invalid') from None
    require(len(parent['completed']) == 10 and isinstance(parent.get('active'), dict)
        and parent['active'].get('stage') == 'model.observe')
    require(proof.get('phase') == 'quarantine-proven' and proof.get('core_sha256') == parent_sha256
        and proof.get('execution_id') == parent['execution_id']
        and proof.get('native_code_sha256') == parent['code_sha256']
        and proof.get('binding_sha256') == parent['binding_sha256'])
    require(uid(expected.get('namespace_uid')) and hexadecimal(expected.get('runtime_fingerprint'), 64)
        and hexadecimal(expected.get('doc_id'), 16)
        and proof.get('namespace_uid') == expected['namespace_uid']
        and proof.get('fingerprint') == expected['runtime_fingerprint'])
    require(proof.get('publication_preserved') is True and proof.get('slot_expired_but_retained') is True
        and proof.get('no_new_provider_requests') is True
        and proof.get('provider_reconciliation_performed') is False and proof.get('qualification_complete') is False)
    stages = parent['completed']
    base = stages['qdrant.verify']['outcome']['database']
    current = proof.get('database', {})
    publication(base['publication']);publication(current.get('publication'))
    require(current['publication'] == base['publication'] and base['publication']['doc_id'] == expected['doc_id']
        and current.get('maintenance') == base['maintenance']
        and base['maintenance'].get('enabled') is False
        and type(base['maintenance'].get('epoch')) is int and base['maintenance']['epoch'] >= 0)
    previous = {w['id']: w for w in base['works']}
    require(len(previous) == len(base['works']))
    works = works_by_id(current.get('works'), expected['doc_id'], set(previous))
    added = set(works) - set(previous)
    require(len(added) == 1 and all(works[k] == v for k, v in previous.items()))
    work = proof.get('work')
    work_row(work, expected['doc_id'])
    require(work == works[next(iter(added))] and work['id'] == stages['model.submit']['outcome']['work_id']
        and work['status'] in {'running', 'queued'} and work['attempts'] >= 1 and work['fence'] >= 1
        and work['cancel_requested'] is False)
    now = aware(proof.get('db_now'))
    if work['status'] == 'running':
        require(isinstance(work['owner_id'], str) and aware(work['lease_until']) > now)
    else:
        require(work['owner_id'] is None and work['lease_until'] is None and work['error_code'] in TRANSIENT)
    require(type(current.get('model_owners')) is int and current['model_owners'] == 1
        and type(current.get('running')) is int and current['running'] == int(work['status'] == 'running')
        and type(current.get('chats')) is int and current['chats'] == 0)
    slot = proof.get('slot', {})
    require(hexadecimal(slot.get('id'), 32) and hexadecimal(slot.get('owner_id'), 32)
        and slot.get('resource') == 'llm' and type(slot.get('slot')) is int and slot['slot'] == 0
        and type(slot.get('fence')) is int and 0 < slot['fence'] < 2**63 and slot.get('release_required') is True
        and aware(slot.get('expires_at')) < now and aware(slot.get('protected_until')) <= now)
    injection = stages['model.inject']['outcome']
    provider = proof.get('provider', {})
    require(provider == injection['provider'] and provider.get('test_only') is True
        and all(type(provider.get(k)) is int for k in ('active', 'started', 'finished', 'peak_active'))
        and provider['active'] == 0 and provider['started'] == provider['finished']
        and provider['started'] >= 0 and 0 <= provider['peak_active'] <= 1)
    policy = proof.get('policy', {})
    require(policy == injection['policy'] and policy.get('provider') == 'model'
        and uid(policy.get('policy_uid')) and hexadecimal(policy.get('policy_sha256'), 64))
    require(proof.get('network') == injection['network'] == dict(provider='model', blocked_pods=4, positive_controls=12))
    apis, rows = expected.get('api_uids'), proof.get('api_admissions')
    require(isinstance(apis, set) and len(apis) == 2 and all(uid(v) for v in apis)
        and isinstance(rows, list) and len(rows) == 2)
    seen = set()
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'pod_uid', 'busy', 'provider_invoked'}
            and row['pod_uid'] in apis and row['pod_uid'] not in seen
            and row['busy'] is True and row['provider_invoked'] is False)
        seen.add(row['pod_uid'])
    require(seen == apis)
    return copy.deepcopy(dict(parent_execution_id=parent['execution_id'], source_epoch=base['maintenance']['epoch'],
        work=work, slot=slot, publication=current['publication'], provider=provider))


SLOT_SOURCE = '''import json
from sqlalchemy import text
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.db.session import get_engine
p=EXPECTED;s=get_settings();identity=enforce_runtime_identity(s)
assert s.deployment_namespace==p['namespace'] and identity.fingerprint==p['fingerprint']
with get_engine().connect() as c:
 c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
 rows=[dict(r) for r in c.execute(text("SELECT id,resource,slot,owner_id,fence,expires_at,protected_until,release_required FROM replica_capacity_slots WHERE resource='llm' ORDER BY slot")).mappings()]
 print(json.dumps(dict(slots=rows,now=c.scalar(text('SELECT clock_timestamp()'))),default=lambda v:v.isoformat()))
'''

BUSY_SOURCE = '''import json
from app.config import get_settings
from app.deployment.runtime_identity import enforce_runtime_identity
from app.services.work.model_budget import acquire_model_budget
from app.services.llm_client import LLMBusyError
p=EXPECTED;s=get_settings();identity=enforce_runtime_identity(s)
assert s.app_role=='api' and s.deployment_namespace==p['namespace'] and identity.fingerprint==p['fingerprint']
try:b=acquire_model_budget(s,max_wait=0)
except LLMBusyError:print(json.dumps({'busy':True,'provider_invoked':False}))
else:
 b.complete(confirmed=True)
 print(json.dumps({'busy':False,'provider_invoked':False}))
'''

OPERATION = 'owned-model-quarantine-recovery-v1'


class ModelRecovery(WorkerLoss):
    """Adopt the own model outage; reuse audited stop/reconcile/release methods."""
    def __init__(self, manifest, journal, parent, quarantine, parent_session):
        self.manifest_path, self.parent_path, self.quarantine_path, self.session_path = (
            Path(v).absolute() for v in (manifest, parent, quarantine, parent_session))
        self.manifest = read_json(self.manifest_path)
        owned = OwnedAdapter(self.manifest, self.parent_path)
        self.inputs = dict(manifest=self.manifest_path, parent=self.parent_path,
            quarantine=self.quarantine_path, session=self.session_path, config=owned.paths['config'])
        require(len(set(self.inputs.values()) | {Path(journal).absolute()}) == 6
            and all(v.parent == owned.private for v in self.inputs.values())
            and Path(journal).absolute().parent == owned.private)
        self.parent, self.quarantine, self.session = (read_json(v) for v in
            (self.parent_path, self.quarantine_path, self.session_path))
        require(hexadecimal(self.parent.get('execution_id'), 32)
            and Path(journal).name == 'provider-model-recovery-'+self.parent['execution_id']+'.json')
        cookie = self.session.get('cookie')
        require(isinstance(cookie, str) and 1 <= len(cookie) <= 16384 and '\r' not in cookie and '\n' not in cookie
            and self.session.get('cookie_sha256') == sha256(cookie.encode()).hexdigest()
            and self.session.get('parent_execution_id') == self.parent['execution_id']
            and self.session.get('namespace_uid') == self.manifest['namespace_uid']
            and self.session.get('fingerprint') == self.manifest['runtime_fingerprint'])
        super().__init__(owned.config, self.manifest['namespace_uid'], journal, self.manifest['runtime_fingerprint'],
            source_journal=owned.paths['source_journal'], archive=owned.paths['archive'], key_file=owned.paths['key_file'])
        self._guard_parent()

    def _guard_parent(self):
        if self.journal is not None:
            require(self.journal.get('operation_kind') == OPERATION
                and self.journal.get('parent_execution_id') == self.parent['execution_id']
                and self.journal.get('recipe_sha256') == recipe_digest()
                and self.journal.get('input_sha256') == {k:sha256(v.read_bytes()).hexdigest() for k,v in self.inputs.items()})

    def _require_phase(self, phases):
        require(isinstance(self.journal, dict) and self.journal.get('operation_kind') == OPERATION
            and self.journal.get('phase') in phases)

    def save(self, **updates):
        require(isinstance(self.journal, dict))
        if self.authority is not None:
            require(self.journal.get('fault_authority', self.authority.record) == self.authority.record)
            self.journal['fault_authority'] = self.authority.record
        self.journal.update(updates)
        write_json(self.path, self.journal)

    def preflight(self, phase):
        self._guard_parent()
        super().preflight(phase)
        self.authority.source.check_roles(staged=True, allow_running=True)

    def inject(self):
        raise ValueError('This recipe adopts the existing owned model outage; no new injection')

    def adopt(self):
        from replica_provider_native import NativeAdapter
        require(self.journal is None)
        self.preflight('adopt')
        a = NativeAdapter(self.manifest, self.parent_path)
        require(digest(a.bind(self.parent)) == self.parent['binding_sha256'])
        a.guard()
        network = assert_network(a.network(), a.expected_pods, 'model')
        policy = a.policy.observe_create('model')
        require(policy is not None and policy['passed'] is True)
        expected = dict(namespace=self.value['namespace'], fingerprint=self.fingerprint)
        sql = SLOT_SOURCE.replace('EXPECTED', repr(expected))
        first = self.kube.python(a.api(), sql)
        require(first['slots'] == [self.quarantine['slot']])
        admissions = []
        for pod in role_pods(self.kube.inventory(), 'api'):
            result = self.kube.python(pod['metadata']['name'], BUSY_SOURCE.replace('EXPECTED', repr(expected)))
            require(result == dict(busy=True, provider_invoked=False))
            admissions.append(dict(pod_uid=pod['metadata']['uid'], **result))
        slots, provider, database = self.kube.python(a.api(), sql), a.model(), a.database()
        require(slots['slots'] == first['slots'] and provider == self.quarantine['provider'])
        work = next(w for w in database['works'] if w['id'] == self.parent['completed']['model.submit']['outcome']['work_id'])
        proof = {**self.quarantine, 'slot':slots['slots'][0], 'db_now':slots['now'], 'work':work,
            'database':database, 'provider':provider, 'network':network, 'policy':policy['evidence'], 'api_admissions':admissions}
        apis = {k for k,v in a.expected_pods.items() if v == 'api'}
        admitted = admit_quarantine(self.parent, proof, parent_sha256=sha256(self.parent_path.read_bytes()).hexdigest(),
            expected=dict(namespace_uid=self.value['namespace_uid'], runtime_fingerprint=self.fingerprint,
                doc_id=self.manifest['provider_fault']['doc_id'], api_uids=apis))
        require(self.session['api_pod_uid'] in apis and self.session['parent_core_sha256'] == proof['core_sha256'])
        with self.verification_client() as client:
            self.authenticate_verification_client(client)
        objects = self.lab.inventory()
        deployments = {o['metadata']['name']:o for o in objects if o['kind'] == 'Deployment'}
        require(set(deployments) == {'frontend','api','worker','model-stub'})
        current = self.kube.inventory()
        models = role_pods(current, 'model-stub');require(len(models) == 1)
        writers = [p for role in ('frontend','api','worker') for p in role_pods(current, role)]
        require(len(writers) == 6 and {p['spec']['nodeName'] for p in writers+models} == {'okf-policy-control-plane'})
        service = json.loads(self.kube.run('get','service','model-stub','-o','json'))
        nodes = json.loads(self.kube.run('get','nodes','-o','json'))['items']
        require(len(nodes) == 1 and nodes[0]['metadata']['name'] == 'okf-policy-control-plane')
        checks = self.manifest['provider_fault']['checks']
        self.journal = dict(version=1, operation_kind=OPERATION, phase='quarantine-proven',
            operation_id=uuid4().hex, owner_id='owned-model-quarantine-recovery', parent_execution_id=self.parent['execution_id'],
            namespace_uid=self.value['namespace_uid'], fingerprint=self.fingerprint, recipe_sha256=recipe_digest(),
            input_sha256={k:sha256(v.read_bytes()).hexdigest() for k,v in self.inputs.items()},
            old_deployments=deployments, old_worker_pods=role_pods(current,'worker'),
            api_pod=next(p for p in role_pods(current,'api') if p['metadata']['uid']==self.session['api_pod_uid']),
            original_api_uids=sorted(apis), source_epoch=admitted['source_epoch'], original_work=admitted['work'],
            original_lease=admitted['slot'], provider_before=provider, quarantine_proof=proof,
            baseline_documents=[d for d in database['corpus'] if d['id'] != work['doc_id']],
            document=dict(id=work['doc_id'], sha256=checks['original_sha256'], search_query=checks['query']),
            binding=dict(service_uid=service['metadata']['uid'], service_selector=service['spec']['selector'],
                nodes={nodes[0]['metadata']['name']:nodes[0]['metadata']['uid']},
                deployments={k:v['metadata']['uid'] for k,v in deployments.items()},
                writer_pod_uids=sorted(p['metadata']['uid'] for p in writers),
                provider_pod_uid=models[0]['metadata']['uid'], provider_image_digest=models[0]['status']['containerStatuses'][0]['imageID'][-71:]))
        self.save()
        return dict(adopted=True, parent_execution_id=self.parent['execution_id'], physical_stop_performed=False)

    def recover(self):
        self._require_phase({'quarantine-proven','held','reconciled'})
        self._guard_parent()
        return super().recover()

    def _stopped_guard(self):
        self._guard_parent()
        observer = StopObservation(self.fetch_stop, self.value, self.journal['binding'])
        require(observer.digest() == self.journal['proof']['observation_sha256'])
        return True

    def _heal_model_policy(self):
        value = {**self.config, **self.manifest, 'recipe':RECIPE, 'profile':'basic',
            'deployment_mode':'distributed', 'artifact_backend':'s3'}
        directory = self.path.parent/('provider-'+self.parent['execution_id'])
        require(directory.is_dir())
        actions = PolicyActions(self.kube, value, directory, self.parent['execution_id'], self._stopped_guard)
        result = actions.heal('model')
        require(result is not None and result['passed'] is True)
        require(not json.loads(self.kube.run('get','cnp','-o','json'))['items'])
        self.save(policy_healed=result['evidence'])

    def release(self):
        self._require_phase({'reconciled'})
        self._guard_parent()
        observer = StopObservation(self.fetch_stop, self.value, self.journal['binding'])
        require(observer.digest() == self.journal['proof']['observation_sha256'])
        self._heal_model_policy()
        return super().release()

    def verification_client(self):
        client = super().verification_client()
        client.headers['Cookie'] = self.session['cookie']
        return client

    def authenticate_verification_client(self, client):
        response = client.get('/api/auth/me')
        require(response.status_code == 200 and response.json()['user']['username'] == 'replica.admin')

    def fault_summary(self):
        return dict(fault_kind='model-connectivity-loss', worker_crash_injected=False, provider_requests_started_during_fault=0)

    def _final_snapshot(self):
        from replica_provider_native import SQL_SOURCE
        expected = dict(installation_id=self.config['installation_id'], runtime_fingerprint=self.fingerprint,
            doc_id=self.journal['document']['id'])
        api = role_pods(self.kube.inventory(),'api')[0]['metadata']['name']
        return self.kube.python(api, 'EXPECTED='+repr(expected)+'\n'+SQL_SOURCE)

    def _finish_logout(self, public):
        if not self.journal.get('logout_intent'):
            with self.verification_client() as client:
                self.authenticate_verification_client(client)
                checks = self.manifest['provider_fault']['checks']
                response = client.get('/api/jobs/'+str(checks['export_job'])+'/export/'+str(checks['export_part']))
                require(response.status_code == 200 and sha256(response.content).hexdigest() == checks['export_sha256'])
                self.save(functional_report=public, functional_snapshot=self._final_snapshot(),
                    logout_intent=dict(cookie_sha256=self.session['cookie_sha256'], parent_execution_id=self.parent['execution_id']))
                response = client.post('/api/auth/logout', json={})
                require(response.status_code == 200)
        require(self._final_snapshot() == self.journal['functional_snapshot'])
        with self.verification_client() as client:
            response = client.get('/api/auth/me')
            if response.status_code == 200:
                body = response.json();user = body.get('user',{})
                require(body.get('mode') == 'simulation' and user.get('user_id') == user.get('username') == 'anonymous'
                    and user.get('roles') == [] and user.get('groups') == [])
            else:require(response.status_code == 401)
            require(client.get('/api/documents/'+self.journal['document']['id']+'/download').status_code == 401)
        self.save(logout_receipt=dict(session_revoked=True, old_cookie_checked=True, parent_session_preserved_across_restart=True))
        return {**public, 'session':self.journal['logout_receipt'], 'old_export_verified':True}

    def report_result(self, public):
        require(not json.loads(self.kube.run('get','cnp','-o','json'))['items'])
        public = {**public, 'status':'owned-model-quarantine-recovery-passed',
            'parent_execution_id':self.parent['execution_id'], 'parent_core_sha256':self.journal['input_sha256']['parent'],
            'parent_completed_native_stages':10, 'parent_history_unchanged':True,
            'policy_removed':True, 'qualification_complete':False, 'certification':None,
            'remaining_runtime_faults':['final-source-provider-matrix-and-full-CI']}
        return self._finish_logout(public)

    def verify(self, report):
        self._require_phase({'running','verified'})
        report = checked_report_path(self, report)
        if self.journal.get('logout_intent'):
            self.preflight('verify')
            public = self._finish_logout(self.journal['functional_report'])
            Path(report).write_text(json.dumps(public, indent=2)+'\n')
            self.save(phase='verified', report=str(Path(report).resolve()))
            return public
        return super().verify(report)


def checked_report_path(recipe, report):
    from app.services.diagnostics.paths import assert_no_links
    require(isinstance(report, (str, Path)))
    path = Path(report).absolute()
    require(path.parent == recipe.path.parent
        and path.name == 'provider-model-recovery-'+recipe.parent['execution_id']+'-report.json'
        and path != recipe.path and path not in recipe.inputs.values())
    assert_no_links(path, path.parent)
    return path


def execute_recovery(recipe, phase, *, apply=False, observe=False, report=None):
    require(type(apply) is bool and type(observe) is bool and not (apply and observe))
    require(phase in {'inspect','adopt','recover','release','start','verify'})
    if phase == 'inspect':
        recipe.preflight('inspect')
        return dict(phase=(recipe.journal or {}).get('phase','not-adopted'), qualification_complete=False)
    require(apply or observe)
    if observe:
        require(phase == 'verify' and isinstance(recipe.journal, dict) and bool(recipe.journal.get('logout_intent')))
    if phase == 'verify':
        report = checked_report_path(recipe, report)
        recipe.verify(report)
    else:
        getattr(recipe, phase)()
    return dict(phase=recipe.journal['phase'], operation_id=recipe.journal['operation_id'],
        parent_execution_id=recipe.parent['execution_id'], qualification_complete=False)


def main():
    import argparse
    from contextlib import redirect_stdout
    import traceback
    from filelock import FileLock
    from app.services.diagnostics.paths import assert_no_links
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['inspect','adopt','recover','release','start','verify'])
    for name in ['manifest','journal','parent','quarantine','parent-session']:
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--report')
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument('--apply', action='store_true');flags.add_argument('--observe', action='store_true')
    args = parser.parse_args()
    try:
        with redirect_stdout(sys.stderr):
            recipe = ModelRecovery(args.manifest, args.journal, args.parent, args.quarantine, args.parent_session)
            if args.phase == 'inspect':
                result = execute_recovery(recipe, args.phase)
            else:
                lock = recipe.path.with_name(recipe.path.name+'.lock')
                assert_no_links(lock, lock.parent)
                with FileLock(lock, timeout=5):
                    require(not recipe.path.exists() and recipe.journal is None
                        or recipe.path.exists() and read_json(recipe.path) == recipe.journal)
                    result = execute_recovery(recipe, args.phase, apply=args.apply, observe=args.observe, report=args.report)
        print(json.dumps(result));return 0
    except Exception as exc:
        print(json.dumps(dict(model_recovery_refused=type(exc).__name__, qualification_complete=False,
            locations=[Path(f.filename).name+':'+str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]])))
        return 1


if __name__ == '__main__':
    sys.exit(main())
