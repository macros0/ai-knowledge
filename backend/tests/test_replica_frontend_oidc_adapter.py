"""Exact OIDC capsules and native completion attestations, never a manifest flag."""
from hashlib import sha256
import importlib
import json
from types import SimpleNamespace

import pytest

from tests.test_replica_frontend_oidc_native import value
from tests.test_replica_frontend_native import envelope


def adapter(tmp_path):
    tool=importlib.import_module('test_scripts.replica_frontend_adapter')
    hosts=tmp_path/'known-hosts';hosts.write_text('owned-host ssh-ed25519 DUMMY\n')
    manifest={**value(),'password':'PRIVATE_PASSWORD_CANARY','database_url':'PRIVATE_DSN_CANARY'}
    return tool.NativeAdapter(manifest,tmp_path/'journal.json',ssh_host='ubuntu@10.10.1.52',known_hosts=hosts)


def test_oidc_adapter_selects_only_the_literal_declared_capsule_and_nonsecret_inputs(tmp_path,monkeypatch):
    current=adapter(tmp_path);tool=importlib.import_module('test_scripts.replica_frontend_adapter')
    assert tuple(current.sources)==(*tool.MODULES,'replica_frontend_oidc_http','replica_frontend_oidc_business','replica_frontend_oidc_native')
    request=envelope();state={k:request[k] for k in ['execution_id','code_sha256','binding_sha256']};state['active']=request
    calls=[]
    def run(argv,**kwargs):
        calls.append(json.loads(kwargs['input']));return SimpleNamespace(returncode=0,stdout='{"ok":true,"receipt":null}')
    monkeypatch.setattr(tool.subprocess,'run',run)
    current.execute('preflight',state)
    sent=calls[0]['value']
    assert sent['functional_recipe']=='owned-keycloak-local-v1' and sent['oidc_expected_images']==value()['oidc_expected_images']
    assert 'PRIVATE' not in json.dumps(sent)


@pytest.mark.parametrize('field',['oidc_access_namespace_uid','oidc_platform_namespace_uid','oidc_controller_uid','oidc_gateway_class_uid','oidc_expected_images'])
def test_missing_oidc_metadata_is_refused_before_transport(field,tmp_path):
    tool=importlib.import_module('test_scripts.replica_frontend_adapter');selected=value();selected.pop(field)
    hosts=tmp_path/'hosts';hosts.write_text('dummy')
    with pytest.raises(ValueError):tool.NativeAdapter(selected,tmp_path/'run.json',ssh_host='ubuntu@10.10.1.52',known_hosts=hosts)


def complete_state():
    from test_scripts.replica_frontend_native import STAGES
    return dict(execution_id='a'*32,code_sha256='b'*64,binding_sha256='c'*64,active=None,
        completed={s:dict(intent_id=sha256(s.encode()).hexdigest()[:32],receipt_sha256=sha256(('receipt-'+s).encode()).hexdigest()) for s in STAGES})


def test_oidc_completion_requires_native_receipt_hashes_and_current_capsule(tmp_path,monkeypatch):
    from test_scripts.replica_frontend_native import digest
    current=adapter(tmp_path);state=complete_state()
    assert hasattr(current,'verify_completed'),'Native completion attestation missing'
    proof=dict(recipe='owned-keycloak-local-v1',**{k:state[k] for k in ['execution_id','code_sha256','binding_sha256']},
        capsule_sha256=digest({n:sha256(s.encode()).hexdigest() for n,s in current.sources.items()}),
        receipts_sha256={s:r['receipt_sha256'] for s,r in state['completed'].items()})
    calls=[]
    def rpc(stage,sent,mode):calls.append((stage,sent,mode));return proof.copy()
    monkeypatch.setattr(current,'rpc',rpc)
    assert current.verify_completed(state)==proof and calls[0][0]=='final' and calls[0][2]=='verify'
    for key in ['recipe','execution_id','code_sha256','binding_sha256','capsule_sha256','receipts_sha256']:
        old=proof[key];proof[key]='foreign'
        with pytest.raises(ValueError):current.verify_completed(state)
        proof[key]=old


def test_partial_oidc_history_cannot_request_native_completion(tmp_path,monkeypatch):
    current=adapter(tmp_path);state=complete_state();state['completed'].pop('final')
    assert hasattr(current,'verify_completed'),'Native completion attestation missing'
    calls=[];monkeypatch.setattr(current,'rpc',lambda *a:calls.append(a))
    with pytest.raises(ValueError):current.verify_completed(state)
    assert not calls
