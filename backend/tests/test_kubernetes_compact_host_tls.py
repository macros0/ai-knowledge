from pathlib import Path
import importlib.util
ROOT=Path(__file__).resolve().parent

def load_lab(monkeypatch):
    path=ROOT.parents[1]/'scripts/kubernetes/home_access.py'
    spec=importlib.util.spec_from_file_location('compact_home_access',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def test_tls_leaf_is_single_host_so_browser_cannot_coalesce_idp_and_app(tmp_path,monkeypatch):
    module=load_lab(monkeypatch); commands=[]
    monkeypatch.setattr(module.subprocess,'run',lambda args,**kwargs: commands.append(args))
    ca=tmp_path/'ca.crt';ca_key=tmp_path/'ca.key';key=tmp_path/'tls.key'
    certs=[module.issue_host_certificate(tmp_path,role,'okf-access',ca,ca_key,key) for role in ['identity','knowledge']]
    assert certs[0]!=certs[1]
    for role in ['identity','knowledge']:
        ext=(tmp_path/(role+'.ext')).read_text()
        assert ext.count('DNS:')==1
        assert 'DNS:'+role+'.okf-access.svc.cluster.local' in ext
        assert ('knowledge' if role=='identity' else 'identity') not in ext
    assert len(commands)==4 and all(c[0]=='openssl' for c in commands)
