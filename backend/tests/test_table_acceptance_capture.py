"""Provider identity and shared-client instrumentation must describe actual calls."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

def module():
    path=Path(__file__).parents[1]/'test_scripts/table_acceptance_capture.py'
    assert path.is_file(), 'stable identity/shared-client capture is missing'
    spec=importlib.util.spec_from_file_location('table_acceptance_capture',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def test_model_response_clock_is_not_model_identity_but_id_and_capacity_are():
    capture=module()
    a={'data':[{'id':'model-a','created':10,'meta':{'n_ctx':32768,'size':32}}]}
    b={'data':[{'id':'model-a','created':11,'meta':{'n_ctx':32768,'size':32}}]}
    assert capture.model_identity(a)==capture.model_identity(b)
    assert a['data'][0]['created']==10
    b['data'][0]['meta']['n_ctx']=16384
    assert capture.model_identity(a)!=capture.model_identity(b)
    b['data'][0]['meta']['n_ctx']=32768;b['data'][0]['id']='model-b'
    assert capture.model_identity(a)!=capture.model_identity(b)


@pytest.mark.parametrize('body',[None,{}, {'data':[]}, {'data':[{'id':None}]}])
def test_unknown_model_identity_is_not_a_stable_fingerprint(body):
    with pytest.raises(ValueError):module().model_identity(body)


def test_shared_client_aliases_are_instrumented_once_and_restored():
    capture=module();actual=[];completed=[]
    client=SimpleNamespace(query_points=lambda **kw:actual.append(kw) or 'result')
    original=client.query_points
    with capture.instrument_query_clients([client,client],lambda:completed.append(1)):
        assert client.query_points(limit=1)=='result'
    assert actual==[{'limit':1}] and completed==[1]
    assert client.query_points is original


def test_multiple_distinct_clients_and_exception_restore_without_fake_completion():
    capture=module();completed=[]
    def fail(**kw):raise RuntimeError('expected failure')
    first=SimpleNamespace(query_points=lambda **kw:'ok');second=SimpleNamespace(query_points=fail)
    original=second.query_points
    with pytest.raises(RuntimeError),capture.instrument_query_clients([first,second],lambda:completed.append(1)):
        first.query_points();second.query_points()
    assert completed==[1] and second.query_points is original
