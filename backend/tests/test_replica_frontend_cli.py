"""Selected native stages never close production or offline-issuer gates."""
import json

import pytest

from test_scripts import run_replica_acceptance as runner
from test_scripts.replica_frontend_run import STAGES
from tests.test_replica_frontend_native import value


def manifest():
    return {**value(), 'schema_version':1, 'profile':'basic',
            'database_url':'postgresql://test:synthetic@db/replica_test_abcdefgh',
            'qdrant_collection':'replica_test_abcdefgh', 'bucket_prefix':'replica_test/abcdefgh/'}


def completed():
    return dict(selected_case_passed=True,execution_id='a'*32,runner_sha256='b'*64,binding_sha256='c'*64,
                checks=[{'name':'frontend.'+stage,'status':'passed','evidence_sha256':'d'*64} for stage in STAGES])


def test_selected_native_case_remains_separate_from_real_csrf_gateway_qualification(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(runner,'execute_frontend',lambda *a,**k:(calls.append(k) or completed()),raising=False)
    monkeypatch.setattr(runner,'probe_frontend',lambda *a,**k:pytest.fail('No duplicate live inventory probe'))
    report=runner.build_report(manifest(),'frontend',live=True,frontend_stage='final',frontend_journal=tmp_path/'run.json',apply_frontend=True)
    assert report['selected_frontend_case_passed'] is True and report['passed'] is False
    assert report['skipped_required']==2
    assert {'frontend.production_csrf','frontend.gateway_sso'} <= {c['name'] for c in report['checks'] if c['status']=='pending'}
    assert calls[0]['apply'] is True


@pytest.mark.parametrize('fault',['missing','duplicate','false_completion'])
def test_partial_or_inconsistent_stage_report_is_refused(monkeypatch,tmp_path,fault):
    native=completed()
    if fault=='missing':native['checks'].pop()
    elif fault=='duplicate':native['checks'][-1]=native['checks'][0]
    else:native['checks'][0]['status']='pending'
    monkeypatch.setattr(runner,'execute_frontend',lambda *a,**k:native,raising=False)
    report=runner.build_report(manifest(),'frontend',live=True,frontend_stage='preflight',frontend_journal=tmp_path/'run.json')
    assert report['selected_frontend_case_passed'] is False and report['failed']>=1


def test_transport_error_is_publicly_allowlisted(monkeypatch,tmp_path):
    def refused(*args,**kwargs):raise ValueError('PRIVATE_PROVIDER_CANARY')
    monkeypatch.setattr(runner,'execute_frontend',refused,raising=False)
    report=runner.build_report(manifest(),'frontend',live=True,frontend_stage='preflight',frontend_journal=tmp_path/'run.json')
    assert report['failed']>=1 and 'PRIVATE_PROVIDER_CANARY' not in json.dumps(report)


@pytest.mark.parametrize('extra',[
    ['--apply-frontend'], ['--frontend-journal','unused.json'], ['--observe-frontend'],
    ['--frontend-stage','preflight'],
    ['--frontend-stage','preflight','--frontend-journal','unused.json','--owned-runtime-stage','preflight'],
])
def test_invalid_frontend_flag_combinations_never_dispatch(extra,tmp_path,monkeypatch,capsys):
    path=tmp_path/'manifest.json';path.write_text(json.dumps(manifest()))
    monkeypatch.setattr(runner,'execute_frontend',lambda *a,**k:pytest.fail('Invalid flags dispatched'),raising=False)
    assert runner.main(['--suite','frontend','--manifest',str(path),*extra])==1
    assert json.loads(capsys.readouterr().out)['passed'] is False


def test_offline_qualification_cannot_mix_frontend_actions(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(runner,'execute_frontend',lambda *a,**k:pytest.fail('Offline mode dispatched'),raising=False)
    args=['--suite','all','--manifest',str(tmp_path/'absent.json'),'--frontend-stage','preflight',
          '--qualification-bundle','bundle','--qualification-binding','binding','--qualification-trust','trust','--qualification-root','root']
    assert runner.main(args)==1
    assert json.loads(capsys.readouterr().out)['passed'] is False


@pytest.mark.parametrize('field',['owned_journal','apply_owned','resume_owned'])
def test_frontend_api_rejects_every_runtime_control_even_without_a_runtime_stage(field,tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'execute_frontend',lambda *a,**k:pytest.fail('Mixed API controls dispatched'))
    extra={field:tmp_path/'runtime.json' if field=='owned_journal' else True}
    with pytest.raises(ValueError):runner.build_report(manifest(),'frontend',live=True,
        frontend_stage='preflight',frontend_journal=tmp_path/'frontend.json',**extra)


def oidc_case():
    from hashlib import sha256
    from pathlib import Path
    from test_scripts.replica_frontend_adapter import OIDC_MODULES
    from test_scripts.replica_frontend_native import digest
    from tests.test_replica_frontend_oidc_native import value as oidc_value
    selected={**manifest(),**oidc_value()};native=completed()
    root=Path(__file__).resolve().parents[1]/'test_scripts'
    native['provider_evidence']=dict(recipe='owned-keycloak-local-v1',execution_id=native['execution_id'],
        code_sha256=native['runner_sha256'],binding_sha256=native['binding_sha256'],
        capsule_sha256=digest({n:sha256((root/(n+'.py')).read_text().encode()).hexdigest() for n in OIDC_MODULES}),
        receipts_sha256={s:'d'*64 for s in STAGES})
    return selected,native


def test_verified_full_oidc_case_clears_only_selected_real_provider_checks(monkeypatch,tmp_path):
    selected,native=oidc_case()
    monkeypatch.setattr(runner,'execute_frontend',lambda *a,**k:native)
    report=runner.build_report(selected,'frontend',live=True,frontend_stage='final',frontend_journal=tmp_path/'run.json')
    rows={r['name']:r for r in report['checks']}
    assert rows['frontend.production_csrf']['status']==rows['frontend.gateway_sso']['status']=='passed'
    assert all(len(rows[n]['evidence_sha256'])==64 for n in ['frontend.production_csrf','frontend.gateway_sso'])
    assert report['selected_frontend_case_passed'] is True and report['passed'] is False
    assert report['skipped_required']==0


@pytest.mark.parametrize('fault',['flag_only','capsule','receipt','identity','recipe','partial'])
def test_oidc_flag_or_mismatched_native_evidence_never_clears_real_checks(fault,monkeypatch,tmp_path):
    selected,native=oidc_case();proof=native['provider_evidence']
    if fault=='flag_only':native.pop('provider_evidence')
    elif fault=='capsule':proof['capsule_sha256']='f'*64
    elif fault=='receipt':proof['receipts_sha256']['session']='f'*64
    elif fault=='identity':proof['execution_id']='f'*32
    elif fault=='recipe':proof['recipe']='owned-simulation-local-v1'
    else:native['checks'][0]['status']='pending';native['selected_case_passed']=False
    monkeypatch.setattr(runner,'execute_frontend',lambda *a,**k:native)
    report=runner.build_report(selected,'frontend',live=True,frontend_stage='final',frontend_journal=tmp_path/'run.json')
    assert all(r['status']!='passed' for r in report['checks'] if r['name'] in ['frontend.production_csrf','frontend.gateway_sso'])


def test_common_runner_fingerprint_covers_all_oidc_source_modules():
    from hashlib import sha256
    from pathlib import Path
    names=('run_replica_acceptance.py','replica_runtime_acceptance.py','replica_runtime_faults.py',
        'replica_frontend_acceptance.py','replica_frontend_adapter.py','replica_frontend_native.py',
        'replica_frontend_process.py','replica_frontend_http.py','replica_frontend_run.py',
        'replica_frontend_oidc_http.py','replica_frontend_oidc_business.py','replica_frontend_oidc_native.py')
    root=Path(__file__).resolve().parents[1]/'test_scripts'
    assert runner.RUNNER_SHA256==sha256(b''.join((root/n).read_bytes() for n in names)).hexdigest()
