"""The fixed native transport sends code and owned identity, never runtime secrets."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_replica_frontend_native import value, envelope


def module():
    path=Path(__file__).resolve().parents[1]/'test_scripts/replica_frontend_adapter.py'
    assert path.is_file(), 'Fixed frontend transport is missing'
    return importlib.import_module('test_scripts.replica_frontend_adapter')


def adapter(tmp_path):
    hosts=tmp_path/'known-hosts';hosts.write_text('owned-host ssh-ed25519 DUMMY\n')
    manifest={**value(),'profile':'basic','database_url':'postgresql://app:PRIVATE_DSN_CANARY@db/replica_test_abcdefgh',
              'APP_SECRET_KEY':'PRIVATE_KEY_CANARY'}
    return module().NativeAdapter(manifest,tmp_path/'run.json',ssh_host='ubuntu@10.10.1.52',known_hosts=hosts)


def test_transport_is_fixed_pinned_ssh_and_removes_private_manifest_fields(tmp_path,monkeypatch):
    tool=module();current=adapter(tmp_path);request=envelope()
    state={k:request[k] for k in ['execution_id','code_sha256','binding_sha256']}
    state['active']={'intent_id':request['intent_id'],'stage':request['stage']}
    captured=[]
    def run(argv,**kwargs):
        captured.append((argv,kwargs));return SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'receipt':None}),stderr='')
    monkeypatch.setattr(tool.subprocess,'run',run)
    assert current.execute('preflight',state) is None
    argv,kwargs=captured[0];payload=json.loads(kwargs['input'])
    assert 'StrictHostKeyChecking=yes' in argv and argv[-2]=='ubuntu@10.10.1.52'
    assert payload['mode']=='execute' and payload['request']==request
    assert set(payload['sources'])==set(tool.MODULES)
    assert 'PRIVATE_DSN_CANARY' not in kwargs['input'] and 'PRIVATE_KEY_CANARY' not in kwargs['input']
    assert 'database_url' not in payload['value']


def test_observe_uses_same_intent_and_never_dispatches_execute(tmp_path,monkeypatch):
    tool=module();current=adapter(tmp_path);request=envelope('loss')
    state={k:request[k] for k in ['execution_id','code_sha256','binding_sha256']};state['active']=request
    modes=[]
    def run(argv,**kwargs):
        modes.append(json.loads(kwargs['input'])['mode']);return SimpleNamespace(returncode=0,stdout='{"ok":true,"receipt":null}',stderr='')
    monkeypatch.setattr(tool.subprocess,'run',run)
    current.observe('loss',state)
    assert modes==['observe']


def test_changed_host_pin_changes_binding_before_any_native_dispatch(tmp_path):
    current=adapter(tmp_path);first=current.bind()
    (tmp_path/'known-hosts').write_text('changed-host ssh-ed25519 DUMMY\n')
    assert current.bind()!=first


def test_native_refusal_and_provider_error_do_not_expose_raw_text(tmp_path,monkeypatch):
    tool=module();current=adapter(tmp_path);request=envelope()
    state={k:request[k] for k in ['execution_id','code_sha256','binding_sha256']};state['active']=request
    monkeypatch.setattr(tool.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr='PRIVATE_ERROR_CANARY'))
    with pytest.raises(ValueError) as failure:current.execute('preflight',state)
    assert 'PRIVATE_ERROR_CANARY' not in str(failure.value)


def test_native_manifest_recipe_required_before_transport(tmp_path):
    tool=module();hosts=tmp_path/'known-hosts';hosts.write_text('dummy')
    selected={**value(),'profile':'basic'};selected['functional_recipe']='customer'
    with pytest.raises(ValueError):tool.NativeAdapter(selected,tmp_path/'run.json',ssh_host='ubuntu@10.10.1.52',known_hosts=hosts)


def test_full_manifest_is_bound_without_sending_its_private_or_extra_fields(tmp_path):
    tool=module();hosts=tmp_path/'known-hosts';hosts.write_text('dummy')
    selected={**value(),'profile':'basic','operator_note':'first'}
    first=tool.NativeAdapter(selected,tmp_path/'run.json',ssh_host='ubuntu@10.10.1.52',known_hosts=hosts).bind()
    selected['operator_note']='changed'
    second=tool.NativeAdapter(selected,tmp_path/'run.json',ssh_host='ubuntu@10.10.1.52',known_hosts=hosts).bind()
    assert first!=second


def test_single_combined_fixture_cannot_be_reported_as_resilient(tmp_path):
    tool=module();hosts=tmp_path/'known-hosts';hosts.write_text('dummy')
    selected={**value(),'profile':'resilient'}
    with pytest.raises(ValueError):tool.NativeAdapter(selected,tmp_path/'run.json',ssh_host='ubuntu@10.10.1.52',known_hosts=hosts)
