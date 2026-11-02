"""Untrusted bundles cannot manufacture trusted current-profile evidence."""
from hashlib import sha256
import importlib
import json
import os

import pytest

from tests.replica_evidence_fixture import arguments, canonical, change_receipt, fixture, seal_bundle


def verify(data):
    name = 'test_scripts.replica_evidence'
    assert importlib.util.find_spec(name) is not None, 'Trusted file-backed evidence loader is missing'
    return importlib.import_module(name).verify_evidence(**arguments(data))


def test_exact_trusted_complete_bundle_can_qualify(tmp_path):
    data = fixture(tmp_path)
    report = verify(data)
    assert report['qualification_complete'] and report['status']=='basic-certified'
    assert report['provenance']['receipt_count'] == 22
    assert 'public_key' not in json.dumps(report) and 'artifact-0.json' not in json.dumps(report)


def test_missing_required_receipt_stays_incomplete(tmp_path):
    data = fixture(tmp_path)
    bundle = json.loads(data['bundle_path'].read_bytes())
    bundle['receipts'].pop()
    seal_bundle(data,bundle)
    assert verify(data)['certification'] is None


def test_untrusted_receipt_set_modification_requires_terminal_bundle_signature(tmp_path):
    data=fixture(tmp_path)
    assert verify(data)['qualification_complete']
    bundle=json.loads(data['bundle_path'].read_bytes())
    bundle['receipts'].reverse()
    data['bundle_path'].write_bytes(canonical(bundle))
    with pytest.raises(ValueError): verify(data)


@pytest.mark.parametrize('fault',['wrong-key','disabled','producer','check','closure-authority'])
def test_explicit_trust_authority_cannot_be_bypassed(tmp_path,fault):
    data = fixture(tmp_path)
    trust=json.loads(data['trust_path'].read_bytes())
    item=trust['issuers'][0]
    if fault=='wrong-key': item['public_key']='e'*64
    elif fault=='disabled': item['enabled']=False
    elif fault=='producer': item['producer_sha256']='e'*64
    elif fault=='check': item['checks']=item['checks'][1:]
    else: item['checks'].remove('qualification.bundle')
    data['trust_path'].write_bytes(canonical(trust))
    with pytest.raises(ValueError): verify(data)


@pytest.mark.parametrize('fault',['receipt','artifact','signature','unknown-issuer','kind'])
def test_actual_file_hash_and_signature_are_required(tmp_path,fault):
    data=fixture(tmp_path)
    if fault=='receipt': (data['evidence_root']/'receipt-0.json').write_bytes(b'{}')
    elif fault=='artifact': (data['evidence_root']/'artifact-0.json').write_bytes(b'{}')
    elif fault=='signature': change_receipt(data,receipt_change=lambda r:r.update(signature='f'*128))
    elif fault=='unknown-issuer': change_receipt(data,receipt_change=lambda r:r.update(issuer='unknown'))
    else: change_receipt(data,receipt_change=lambda r:r.update(kind='unit'))
    # The helper re-signs; corrupt the final signature after the declared SHA is updated.
    if fault=='signature':
        bundle=json.loads(data['bundle_path'].read_bytes());ref=bundle['receipts'][0]
        path=data['evidence_root']/ref['path'];body=json.loads(path.read_bytes());body['signature']='f'*128
        path.write_bytes(canonical(body));ref['sha256']=sha256(path.read_bytes()).hexdigest()
        seal_bundle(data,bundle)
    with pytest.raises(ValueError): verify(data)


@pytest.mark.parametrize('field',['run_id','namespace_uid','installation_id','acceptance_id'])
def test_receipt_cannot_cross_acceptance_or_target_scope(tmp_path,field):
    data=fixture(tmp_path)
    replacement={'run_id':'ijklmnop','namespace_uid':'22222222-2222-4333-8444-555555555555',
        'installation_id':'e'*32,'acceptance_id':'e'*32}[field]
    change_receipt(data,receipt_change=lambda r:r['scope'].update({field:replacement}))
    with pytest.raises(ValueError): verify(data)


def test_changed_expected_binding_refuses_old_receipts(tmp_path):
    data=fixture(tmp_path);data['binding']['runtime']['model_provider']='changed-model'
    with pytest.raises(ValueError): verify(data)


@pytest.mark.parametrize('status',['failed','skipped','pending'])
def test_signed_nonpassing_outcome_cannot_qualify(tmp_path,status):
    data=fixture(tmp_path)
    change_receipt(data,artifact_change=lambda a:a['outcomes'][0].update(status=status))
    report=verify(data)
    assert report['certification'] is None and report['failed_required']==['contracts.platform']


def test_restore_requires_explicit_verified_native_outcome(tmp_path):
    data=fixture(tmp_path)
    bundle=json.loads(data['bundle_path'].read_bytes())
    index=next(i for i,ref in enumerate(bundle['receipts']) if json.loads((data['evidence_root']/ref['path']).read_bytes())['name']=='operations.independent_restore')
    change_receipt(data,index=index,artifact_change=lambda a:a.update(restore_verified=False))
    assert verify(data)['certification'] is None


@pytest.mark.parametrize('fault',['empty-outcomes','duplicate-outcomes','missing-native','extra-field','duplicate-receipt'])
def test_strict_artifact_and_bundle_contracts(tmp_path,fault):
    data=fixture(tmp_path)
    if fault=='duplicate-receipt':
        body=json.loads(data['bundle_path'].read_bytes());body['receipts'].append(body['receipts'][0])
        seal_bundle(data,body)
    else:
        def change(a):
            if fault=='empty-outcomes': a['outcomes']=[]
            elif fault=='duplicate-outcomes': a['outcomes'].append(a['outcomes'][0])
            elif fault=='missing-native': a['native_reference_sha256']=[]
            else: a['secret']='private-canary'
        change_receipt(data,artifact_change=change)
    with pytest.raises(ValueError) as caught: verify(data)
    assert 'private-canary' not in str(caught.value)


@pytest.mark.parametrize('path',['../outside.json','/tmp/outside.json','https://example.test/a.json',r'C:\outside.json','sub/../receipt-0.json'])
def test_evidence_paths_cannot_escape_or_alias(tmp_path,path):
    data=fixture(tmp_path)
    body=json.loads(data['bundle_path'].read_bytes());body['receipts'][0]['path']=path
    data['bundle_path'].write_bytes(canonical(body))
    with pytest.raises(ValueError): verify(data)


def test_trust_policy_cannot_be_supplied_by_evidence_root(tmp_path):
    data=fixture(tmp_path)
    path=data['evidence_root']/'trust.json';path.write_bytes(data['trust_path'].read_bytes());data['trust_path']=path
    with pytest.raises(ValueError): verify(data)


def test_parent_alias_cannot_disguise_embedded_trust_policy(tmp_path):
    data=fixture(tmp_path)
    embedded=data['evidence_root']/'trust.json'
    embedded.write_bytes(data['trust_path'].read_bytes())
    outside=tmp_path/'outside';outside.mkdir()
    data['trust_path']=outside/'..'/'evidence'/'trust.json'
    with pytest.raises(ValueError): verify(data)


@pytest.mark.parametrize('fault',['duplicate-json','oversize','hardlink','directory'])
def test_unstable_or_unbounded_inputs_are_refused(tmp_path,fault):
    data=fixture(tmp_path)
    if fault=='duplicate-json': data['bundle_path'].write_bytes(b'{"secret":"private-canary","secret":"hidden"}')
    elif fault=='oversize': data['bundle_path'].write_bytes(b' '*65537)
    elif fault=='hardlink': os.link(data['evidence_root']/'receipt-0.json',tmp_path/'alias.json')
    else: data['bundle_path']=data['evidence_root']
    with pytest.raises(ValueError) as caught: verify(data)
    assert 'private-canary' not in str(caught.value)


@pytest.mark.skipif(os.name=='nt',reason='POSIX FIFO guard runs in Linux CI')
def test_fifo_refused_without_waiting_for_writer(tmp_path):
    data=fixture(tmp_path);data['bundle_path'].unlink();os.mkfifo(data['bundle_path'])
    with pytest.raises(ValueError): verify(data)
