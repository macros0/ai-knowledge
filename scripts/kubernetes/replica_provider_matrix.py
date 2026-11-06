"""Read-only assembly of actual scoped PG/Qdrant/model recovery branches."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import ast
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'backend'), str(Path(__file__).resolve().parent)]

from app.services.work.coordination import CapacityLease
from app.services.work.provider_reconciliation import ProviderStopProof, leases_digest
from replica_model_recovery import OPERATION, admit_quarantine
from replica_provider_gate import admit_proof, assert_released_claim
from replica_provider_native import pg_proof
from test_scripts.replica_provider_faults import assert_outage, assert_recovered, hexadecimal, publication, require, works_by_id


def _recipe_files(manifest):
    require(isinstance(manifest, dict) and manifest)
    result = {}
    for name, checksum in manifest.items():
        require(isinstance(name, str) and not any(p in {'..', '.'} for p in name.split('/'))
            and not name.startswith('/') and '\\' not in name and hexadecimal(checksum,64))
        if name.endswith('.py') and name.count('/') == 2 and name.startswith(
            ('scripts/kubernetes/replica_', 'backend/test_scripts/replica_')):
            result[name] = checksum
    require(result)
    return result


def historical_worker(root, expected):
    """Read a bounded local Git history; never fetch or execute old source."""
    name = 'scripts/kubernetes/replica_worker_loss.py'
    revisions = subprocess.check_output(['git','rev-list','--max-count=32','HEAD','--',name],cwd=root,timeout=10).decode().splitlines()
    for revision in revisions:
        require(hexadecimal(revision,40))
        body = subprocess.check_output(['git','cat-file','blob',revision+':'+name],cwd=root,timeout=10)
        if sha256(body).hexdigest() == expected:
            return body
    raise ValueError('Historical worker source not found locally')


def _physical_ast(body):
    tree = ast.parse(body)
    workers = [n for n in tree.body if isinstance(n,ast.ClassDef) and n.name == 'WorkerLoss']
    require(len(workers) == 1)
    # These methods only render/authenticate the functional verification path.
    # Qdrant execution never invokes them. Every other AST node must be equal.
    workers[0].body = [n for n in workers[0].body if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
        or n.name not in {'verification_client','authenticate_verification_client','fault_summary','verify'}]
    return ast.dump(tree, include_attributes=False)


def audit_sources(root, q_ci, model_ci, parent, child, *, old_worker=None):
    from replica_runtime_run import digest
    root = Path(root).resolve()
    for ci in (q_ci,model_ci):
        terminal = ci.get('terminal',{})
        require(ci.get('phase') == 'terminal' and terminal.get('verified') is True
            and terminal.get('source_unchanged') is True and terminal.get('exit_code') == 0 and terminal.get('oom') is False)
    q = _recipe_files(q_ci['expected_manifest'])
    for name in ('backend/app/deployment/replica_backup.py','backend/app/services/diagnostics/paths.py'):
        q[name] = q_ci['expected_manifest'][name]
    model = _recipe_files(model_ci['expected_manifest'])
    name = 'backend/test_scripts/run_replica_acceptance.py'
    model[name] = model_ci['expected_manifest'][name]
    eol = []
    for name, checksum in list(q.items()):
        path = root/name
        require(path.resolve().is_relative_to(root) and path.is_file() and not path.is_symlink())
        body = path.read_bytes()
        if name == 'backend/app/services/diagnostics/paths.py' and sha256(body).hexdigest() != checksum:
            require(sha256(body.replace(b'\r\n',b'\n')).hexdigest() == checksum)
            q[name] = sha256(body).hexdigest(); eol.append(name)
    require(digest(q) == parent.get('code_sha256') and digest(model) == child.get('recipe_sha256'))
    for name, checksum in model.items():
        path = root/name
        require(path.resolve().is_relative_to(root) and path.is_file() and not path.is_symlink()
            and sha256(path.read_bytes()).hexdigest() == checksum)
    worker = 'scripts/kubernetes/replica_worker_loss.py'
    differences = sorted(n for n,h in q.items() if sha256((root/n).read_bytes()).hexdigest() != h)
    require(differences in ([],[worker]))
    if differences:
        old_worker = historical_worker(root,q[worker]) if old_worker is None else old_worker
        require(sha256(old_worker).hexdigest() == q[worker]
            and _physical_ast(old_worker) == _physical_ast((root/worker).read_bytes()))
    return dict(q_parent_code_reconstructed=True,model_recipe_reconstructed=True,
        q_code_files=len(q),model_code_files=len(model),worker_physical_methods_unchanged=True,
        verification_only_worker_difference=bool(differences),verified_pc_eol_differences=eol,historical_cases_reexecuted=False)


def check_current(value, child, observed):
    from replica_runtime_run import digest
    require(observed.get('database') == child.get('functional_snapshot')
        and observed.get('before') == observed.get('after') and bool(observed.get('before'))
        and observed.get('runtime_fingerprint') == value['runtime_fingerprint']
        and bool(observed.get('expected_images')) and observed.get('images') == observed['expected_images']
        and observed.get('replicas') == dict(frontend=2,api=2,worker=2)
        and observed.get('policies') == [] and observed.get('model',{}).get('active') == 0
        and observed['model'].get('test_only') is True)
    return dict(verified=True,actual_database_matches_verified_child=True,
        inventory_and_platform_unchanged=True,replicas=observed['replicas'],images=observed['images'],
        current_observation_sha256=digest(observed))


def corpus_by_id(rows):
    require(isinstance(rows, list) and rows)
    result = {}
    for row in rows:
        require(isinstance(row, dict) and set(row) == {'id','status','problem','deleted_at','sha256','active_generation_id','concept_sha256'}
            and hexadecimal(row['id'],16) and row['id'] not in result and hexadecimal(row['sha256'],64)
            and hexadecimal(row['active_generation_id'],32) and hexadecimal(row['concept_sha256'],64))
        result[row['id']] = row
    return result


def assemble_matrix(value, authority, pg, pg_report, parent, child, model_report, *, parent_sha256):
    """Independent checks over native records; no mutation or stage execution."""
    require(isinstance(value, dict) and isinstance(authority, dict))
    require(child.get('phase') == 'verified' and child.get('operation_kind') == OPERATION
        and child.get('parent_execution_id') == parent.get('execution_id')
        and child.get('input_sha256',{}).get('parent') == parent_sha256)
    apis = set(child.get('original_api_uids', []))
    baseline = parent['completed']['qdrant.verify']['outcome']['database']
    doc_id = baseline['publication']['doc_id']
    admitted = admit_quarantine(parent, child['quarantine_proof'], parent_sha256=parent_sha256,
        expected=dict(namespace_uid=value['namespace_uid'], runtime_fingerprint=value['runtime_fingerprint'], doc_id=doc_id, api_uids=apis))
    pg_record = pg_proof(value, authority, pg, pg_report)
    require(parent['completed']['preflight']['outcome'].get('postgres') == pg_record)
    for record in (parent.get('admission',{}), child.get('fault_authority',{})):
        require(all(record.get(k) == authority.get(k) and authority.get(k) is not None for k in
            ('source_journal_sha256','backup_id','backup_authentication','backup_archive')))
    stale = pg_report.get('stale_return',{})
    require(stale.get('same_container_no_restart') is True)
    probe = stale.get('captured_old_owner_fence_publication_probe')
    if isinstance(probe, dict):
        require(probe == dict(old_token_rejected=True, probe_transaction_rolled_back=True))
    else:
        require(stale.get('captured_old_owner_fence_publication_probe_rejected') is True
            and stale.get('probe_transaction_rolled_back') is True)
    require(type(pg_report.get('maintenance_epoch')) is int and pg_report['maintenance_epoch'] == pg.get('epoch', pg.get('source_epoch'))
        and child.get('source_epoch') == admitted['source_epoch'] == pg_report['maintenance_epoch'])
    stages = parent['completed']
    before = stages['baseline']['outcome']['database']
    injection, observation = stages['qdrant.inject']['outcome'], stages['qdrant.observe']['outcome']
    gate = admit_proof(stages['qdrant.gate']['outcome']['gate'])
    require(injection['gate']['proof'] == gate)
    assert_released_claim(injection['admitted_work'], injection['gate'])
    require(injection['admitted_work']['work_id'] == stages['qdrant.submit']['outcome']['work_id']
        and injection['network'] == dict(provider='qdrant', blocked_pods=4, positive_controls=12))
    previous = [w['id'] for w in before['works']]
    queued = assert_outage(before['publication'], observation['database']['publication'], previous, observation['database']['works'], 'qdrant')
    require(queued == observation['queued'] and queued['id'] == injection['admitted_work']['work_id']
        and queued['fence'] == injection['admitted_work']['fence'])
    recovered = assert_recovered(before['publication'], baseline['publication'], previous, baseline['works'], queued,
        before['publication']['original_sha256'])
    require(stages['qdrant.verify']['outcome']['functional'].get('passed') is True)
    require(model_report.get('status') == 'owned-model-quarantine-recovery-passed'
        and all(model_report.get(k) == value[k] for k in ('namespace','namespace_uid','installation_id','runtime_fingerprint'))
        and model_report.get('operation_id') == child.get('operation_id')
        and model_report.get('parent_execution_id') == parent['execution_id']
        and model_report.get('parent_core_sha256') == parent_sha256 and model_report.get('parent_history_unchanged') is True
        and model_report.get('source_epoch') == child['source_epoch']
        and type(child.get('epoch')) is int and child['epoch'] > child['source_epoch']
        and model_report.get('recovery_epoch') == child['epoch'])
    require(child['original_work'] == admitted['work'] and child['original_lease'] == admitted['slot'])
    proof = ProviderStopProof.model_validate(child.get('proof'))
    lease = CapacityLease(**{k:child['original_lease'][k] for k in ('id','resource','owner_id','fence')},
        expires_at=datetime.fromisoformat(child['original_lease']['expires_at']))
    require(proof.digest() == child.get('proof_digest') == model_report.get('reconciliation',{}).get('proof_sha256')
        and proof.operation_id == child['operation_id'] and proof.installation_id == value['installation_id']
        and proof.namespace == value['namespace'] and str(proof.namespace_uid) == value['namespace_uid']
        and proof.epoch == child['epoch'] and proof.leases_sha256 == leases_digest([lease])
        and model_report['reconciliation'].get('audit_confirmed') is True)
    final = child.get('functional_snapshot',{})
    publication(final.get('publication'))
    require(not any(final.get(k) for k in ('model_owners','running','chats'))
        and all(type(final.get(k)) is int for k in ('model_owners','running','chats'))
        and final.get('maintenance') == dict(epoch=child['epoch'], enabled=False)
        and final['maintenance']['enabled'] is False)
    # The captured model work may still be running inside admission waits.
    # Require its real ownership transition, without manufacturing a queued row.
    old_work = {w['id']:w for w in baseline['works']}
    rows = works_by_id(final['works'], doc_id, set(old_work))
    require(set(rows) == set(old_work)|{admitted['work']['id']} and all(rows[k] == w for k,w in old_work.items()))
    completed = rows[admitted['work']['id']]
    require(completed['status'] == 'succeeded' and completed['attempts'] > admitted['work']['attempts']
        and completed['fence'] > admitted['work']['fence'] and completed['owner_id'] is None
        and completed['lease_until'] is None and completed['error_code'] is None and completed['cancel_requested'] is False
        and final['publication']['doc_id'] == doc_id
        and final['publication']['original_sha256'] == baseline['publication']['original_sha256']
        and final['publication']['active_generation_id'] != baseline['publication']['active_generation_id'])
    final_work = dict(work_id=completed['id'], attempts=completed['attempts'], fence=completed['fence'],
        active_generation_id=final['publication']['active_generation_id'], original_sha256=final['publication']['original_sha256'])
    doc = model_report.get('recovered_document',{})
    require(doc.get('id') == doc_id and doc.get('sha256') == final['publication']['original_sha256']
        and doc.get('status') == 'done' and doc.get('problem') is None and doc.get('active_publications') == 1
        and doc.get('original_fence') == admitted['work']['fence'] and doc.get('recovered_fence') == final_work['fence'])
    old_docs, new_docs = corpus_by_id(baseline['corpus']), corpus_by_id(final['corpus'])
    require(set(old_docs) == set(new_docs) and doc_id in old_docs
        and all(new_docs[k] == v for k,v in old_docs.items() if k != doc_id)
        and new_docs[doc_id]['status'] == 'done' and new_docs[doc_id]['problem'] is None
        and new_docs[doc_id]['active_generation_id'] == final['publication']['active_generation_id']
        and new_docs[doc_id]['sha256'] == baseline['publication']['original_sha256'])
    session = model_report.get('session',{})
    require(model_report.get('policy_removed') is True and model_report.get('old_export_verified') is True
        and all(session.get(k) is True for k in ('session_revoked','old_cookie_checked','parent_session_preserved_across_restart')))
    return dict(schema_version=1, scope='owned-basic-managed-s3-current-provider-branches',
        **{k:value[k] for k in ('namespace','namespace_uid','installation_id','runtime_fingerprint')},
        selected_provider_matrix_passed=True, source_parent_completed_stages=10, source_parent_total_stages=14,
        source_parent_all_stages_passed=False, postgres_operation=pg['operation_id'], qdrant_execution=parent['execution_id'],
        qdrant_recovered_work=recovered, model_recovery_operation=child['operation_id'],
        model_recovered_work=final_work, model_recovered_fence=final_work['fence'], documents_preserved=len(new_docs),
        initial_epoch=child['source_epoch'], final_epoch=child['epoch'], required_skips=0, failed=0,
        qualification_complete=False, certification=None, production_schedule='disabled', full_backend_ci=False)


def observe_owned(manifest, child, output):
    """Only fresh get/exec read probes; no policy, session or job actions."""
    from replica_provider_native import KINDS, SQL_SOURCE, inventory_binding, platform_binding
    from replica_runtime_run import OwnedAdapter
    from test_scripts.replica_runtime_acceptance import role_pods, validate_inventory, verify_runtime
    owned = OwnedAdapter(manifest, output)
    owned.bind(dict(admission=child['fault_authority']))
    kube, value = owned.kube, owned.value
    kube.scope()
    namespace = json.loads(kube.run('get','namespace',value['namespace'],'-o','json'))
    require(namespace['metadata']['uid'] == manifest['namespace_uid'])
    owned.authority.check(inject=True)
    def snapshot():
        objects = json.loads(kube.run('get',KINDS,'-o','json'))['items']
        return dict(inventory=inventory_binding(objects),platform=platform_binding(kube,objects))
    before = snapshot()
    objects = kube.inventory()
    ready = validate_inventory(objects,value)
    runtime = verify_runtime(kube,objects,value)
    models = role_pods(objects,'model-stub'); require(len(models) == 1)
    model = kube.python(models[0]['metadata']['name'],'''import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/test/status',timeout=3) as r: result=json.load(r)
print(json.dumps(result))''')
    expected = {k:manifest[k] for k in ('installation_id','runtime_fingerprint')}
    expected['doc_id'] = manifest['provider_fault']['doc_id']
    api = sorted(role_pods(objects,'api'),key=lambda p:p['metadata']['name'])[0]['metadata']['name']
    database = kube.python(api,'EXPECTED='+repr(expected)+'\n'+SQL_SOURCE)
    policies = json.loads(kube.run('get','cnp','-o','json'))['items']
    owned.authority.check(inject=True)
    after = snapshot()
    return check_current(manifest,child,dict(database=database,before=before,after=after,
        runtime_fingerprint=runtime['fingerprint'],images=ready['images'],expected_images=dict(owned.backup.plan.images),
        replicas=ready['replicas'],policies=policies,model=model))


def collect_matrix(manifest_path, parent_path, child_path, q_ci_path, model_ci_path, output, *, observer=observe_owned):
    from replica_runtime_run import now, read_json, write_json
    paths = {k:Path(v).absolute() for k,v in dict(manifest=manifest_path,parent=parent_path,model=child_path,
        q_ci=q_ci_path,model_ci=model_ci_path).items()}
    manifest,parent,child,q_ci,model_ci = (read_json(paths[k]) for k in ('manifest','parent','model','q_ci','model_ci'))
    output = Path(output).absolute(); private = paths['manifest'].parent
    require(hexadecimal(parent.get('execution_id'),32) and paths['parent'].parent == paths['model'].parent == private
        and output == private/('provider-matrix-'+parent['execution_id']+'-report.json') and not output.exists())
    report = private/('provider-model-recovery-'+parent['execution_id']+'-report.json')
    require(child.get('report') == str(report))
    paths.update(pg=Path(manifest['provider_fault']['pg_journal']).absolute(),
        pg_report=Path(manifest['provider_fault']['pg_report']).absolute(),model_report=report,
        config=Path(manifest['owned_runtime']['config']).absolute())
    require(all(paths[k].parent == private for k in ('pg','pg_report','model_report','config'))
        and len(set(paths.values())|{output}) == len(paths)+1)
    initial = {k:sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}
    require(all(child.get('input_sha256',{}).get(k) == initial[k] for k in ('manifest','parent','config')))
    sources = audit_sources(ROOT,q_ci,model_ci,parent,child)
    pg,pg_report,model_report = (read_json(paths[k]) for k in ('pg','pg_report','model_report'))
    result = assemble_matrix(manifest,child['fault_authority'],pg,pg_report,parent,child,model_report,parent_sha256=initial['parent'])
    current = observer(manifest,child,output)
    require(current.get('verified') is True and initial == {k:sha256(p.read_bytes()).hexdigest() for k,p in paths.items()})
    result.update(source_audit=sources,fresh_current=current,input_sha256=initial,verified_at=now(),
        collector_sha256=sha256(Path(__file__).read_bytes()).hexdigest())
    write_json(output,result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','parent','model','q-ci','model-ci','output'):
        parser.add_argument('--'+name,required=True)
    args = parser.parse_args()
    try:
        result = collect_matrix(args.manifest,args.parent,args.model,args.q_ci,args.model_ci,args.output)
        print(json.dumps({k:result[k] for k in ('selected_provider_matrix_passed','source_parent_completed_stages',
            'model_recovered_fence','documents_preserved','full_backend_ci','qualification_complete')}))
    except Exception as exc:  # noqa: BLE001 -- confidential provider facts never enter stdout.
        import traceback
        frames = traceback.extract_tb(exc.__traceback__)[-3:]
        print(json.dumps(dict(status='refused',error_type=type(exc).__name__,
            locations=[Path(f.filename).name+':'+str(f.lineno) for f in frames])))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
