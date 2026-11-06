"""Offline qualification mode cannot invoke live probes or accept self-trust."""
import json

import pytest

from test_scripts import run_replica_acceptance as runner
from tests.replica_evidence_fixture import canonical, fixture, seal_bundle
from tests.test_replica_acceptance_safety import manifest


def inputs(tmp_path):
    data=fixture(tmp_path)
    selected=manifest()
    selected.update(data['scope'],runtime_fingerprint=data['binding']['runtime']['identity_fingerprint'])
    manifest_path=tmp_path/'manifest.json';manifest_path.write_bytes(canonical(selected))
    binding_path=tmp_path/'binding.json';binding_path.write_bytes(canonical(data['binding']))
    return data,selected,manifest_path,binding_path


def argv(data,manifest_path,binding_path):
    return ['--suite','all','--manifest',str(manifest_path),
        '--qualification-bundle',str(data['bundle_path']),'--qualification-binding',str(binding_path),
        '--qualification-trust',str(data['trust_path']),'--qualification-root',str(data['evidence_root'])]


def invoke(args):
    try:
        return runner.main(args)
    except SystemExit as exc:
        assert exc.code != 2, 'Common runner lacks explicit offline qualification arguments'
        return exc.code


def test_common_runner_verifies_all_trusted_files_without_probes(tmp_path,monkeypatch,capsys):
    data,_,path,binding_path=inputs(tmp_path)
    def forbidden(*args,**kwargs): raise AssertionError('Offline mode called a live probe')
    monkeypatch.setattr(runner,'probe_runtime',forbidden)
    monkeypatch.setattr(runner,'execute_owned_runtime',forbidden)
    assert invoke(argv(data,path,binding_path))==0
    report=json.loads(capsys.readouterr().out)
    assert report['passed'] and report['qualification']['qualification_complete']
    assert report['mode']=='offline-trusted-harness-verification'
    assert 'test-secret' not in json.dumps(report) and 'database_url' not in json.dumps(report)


@pytest.mark.parametrize('flag',['--live','--idle-faults','--active-faults','--apply-owned-runtime','--resume-owned-stage'])
def test_offline_mode_refuses_any_live_or_mutating_switch(tmp_path,flag,capsys):
    data,_,path,binding_path=inputs(tmp_path)
    assert invoke(argv(data,path,binding_path)+[flag])==1
    assert json.loads(capsys.readouterr().out)['passed'] is False


@pytest.mark.parametrize('field',['namespace_uid','installation_id','acceptance_id','runtime_fingerprint','profile','namespace'])
def test_binding_and_manifest_scope_must_match(tmp_path,field,capsys):
    data,value,path,binding_path=inputs(tmp_path)
    replacement={'namespace_uid':'22222222-2222-4333-8444-555555555555','installation_id':'e'*32,
        'acceptance_id':'e'*32,'runtime_fingerprint':'e'*64,'profile':'resilient','namespace':'default'}
    value[field]=replacement[field];path.write_bytes(canonical(value))
    assert invoke(argv(data,path,binding_path))==1
    report=json.loads(capsys.readouterr().out)
    assert not report['passed'] and 'test-secret' not in json.dumps(report)


@pytest.mark.parametrize('option',['--qualification-bundle','--qualification-binding','--qualification-trust','--qualification-root'])
def test_all_explicit_qualification_inputs_required(tmp_path,option,capsys):
    data,_,path,binding_path=inputs(tmp_path)
    args=argv(data,path,binding_path);index=args.index(option);del args[index:index+2]
    assert invoke(args)==1
    assert not json.loads(capsys.readouterr().out)['passed']


def test_partial_suite_cannot_certify_all_profile_checks(tmp_path,capsys):
    data,_,path,binding_path=inputs(tmp_path)
    args=argv(data,path,binding_path);args[1]='runtime'
    assert invoke(args)==1
    assert not json.loads(capsys.readouterr().out)['passed']


def test_binding_must_be_selected_outside_evidence_root(tmp_path,capsys):
    data,_,path,binding_path=inputs(tmp_path)
    embedded=data['evidence_root']/'binding.json';embedded.write_bytes(binding_path.read_bytes())
    assert invoke(argv(data,path,embedded))==1
    assert not json.loads(capsys.readouterr().out)['passed']


def test_missing_evidence_is_pending_and_preserves_null_certificate(tmp_path,capsys):
    data,_,path,binding_path=inputs(tmp_path)
    bundle=json.loads(data['bundle_path'].read_bytes());bundle['receipts']=[]
    seal_bundle(data,bundle)
    assert invoke(argv(data,path,binding_path))==1
    report=json.loads(capsys.readouterr().out)
    assert report['skipped_required']==22 and report['qualification']['certification'] is None


def test_corrupt_private_input_error_is_redacted(tmp_path,capsys):
    data,_,path,binding_path=inputs(tmp_path)
    data['bundle_path'].write_bytes(b'{"secret":"private-canary"}')
    assert invoke(argv(data,path,binding_path))==1
    output=capsys.readouterr().out
    assert 'private-canary' not in output and 'test-secret' not in output
