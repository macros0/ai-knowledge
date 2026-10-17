"""Read-only direct service evidence and model peer ownership invariants."""
import json
import time
from types import SimpleNamespace
import pytest


def configuration(tmp_path):
 now=time.time();paths={key:str(tmp_path/(key+'.json')) for key in ('windows','primary','host')}
 windows={'known':True,'at_unix':now,'age_at_receipt_upper_s':0,'completed_requests':2,'embedding_pid':5,'embedding_birth':7,'audit_identity':'own'}
 primary={'known':True,'at_unix':now,'age_at_receipt_upper_s':0,'primary_generating':0}
 host={'at_unix':now,'model_connections':{'known':True,'own_established':1,'other_established':0}}
 for key,value in [('windows',windows),('primary',primary),('host',host)]:
  with open(paths[key],'w') as f:json.dump(value,f)
 return dict(paths=paths,windows_identity={k:windows[k] for k in ('embedding_pid','embedding_birth','audit_identity')},
             database_url='postgresql+psycopg://r2_lab:fixture@127.0.0.1:15432/r2_synthetic',
             slots_url='http://127.0.0.1:1/slots')


def make_probe(tmp_path,monkeypatch,busy=False):
 from table_acceptance_direct_probe import DirectServiceProbe
 config=configuration(tmp_path)
 connection=SimpleNamespace(execute=lambda sql:SimpleNamespace(scalar_one=lambda:0))
 class Context:
  def __enter__(self):return connection
  def __exit__(self,*args):pass
 engine=SimpleNamespace(connect=Context,dispose=lambda:None)
 monkeypatch.setattr('table_acceptance_direct_probe.create_engine',lambda *a,**kw:engine)
 monkeypatch.setattr('table_acceptance_direct_probe.install_connection_limits',lambda *a:None)
 response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:[{'is_processing':busy}])
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',lambda *a,**kw:response)
 return DirectServiceProbe(config),config


def test_direct_probe_owns_busy_slot_only_inside_known_bracket_and_peer(tmp_path,monkeypatch):
 probe,_=make_probe(tmp_path,monkeypatch,busy=True)
 assert probe.sample(lambda:1)['model_other_busy']==0
 assert probe.sample(lambda:0)['model_other_busy']==1


@pytest.mark.parametrize('fault',['windows_identity','windows_stale','primary_stale','primary_unknown','peer_stale','peer_competing'])
def test_direct_probe_rejects_stale_or_foreign_evidence(tmp_path,monkeypatch,fault):
 probe,c=make_probe(tmp_path,monkeypatch,busy=True)
 role='windows' if fault.startswith('windows') else 'primary' if fault.startswith('primary') else 'host'
 path=c['paths'][role]
 with open(path) as f:body=json.load(f)
 if fault=='windows_identity':body['embedding_birth']+=1
 elif 'stale' in fault:body['at_unix']-=3
 elif fault=='primary_unknown':body['known']=False
 else:body['model_connections']['other_established']=1
 with open(path,'w') as f:json.dump(body,f)
 with pytest.raises(ValueError):probe.sample(lambda:1)


def test_direct_probe_marks_unrelated_primary_work_and_pg_work(tmp_path,monkeypatch):
 probe,c=make_probe(tmp_path,monkeypatch)
 with open(c['paths']['primary']) as f:body=json.load(f)
 body['primary_generating']=1
 with open(c['paths']['primary'],'w') as f:json.dump(body,f)
 assert probe.sample(lambda:0)['primary_generating']==1


@pytest.mark.parametrize('count', [0, 1, 3])
def test_primary_sql_count_is_valid_numeric_load_evidence(tmp_path, monkeypatch, count):
 from benchmark_table_acceptance import _finite
 probe, config = make_probe(tmp_path, monkeypatch)
 path = config['paths']['primary']
 with open(path) as f: body = json.load(f)
 body['primary_generating'] = count
 with open(path, 'w') as f: json.dump(body, f)
 sample = probe.sample(lambda: 0)
 assert type(sample['primary_generating']) is int
 assert sample['primary_generating'] == count
 assert _finite(sample['primary_generating'])


@pytest.mark.parametrize('invalid', [False, True, None, -1, 0.5, '0', {}, []])
def test_primary_count_rejects_boolean_or_unknown_frame(tmp_path, monkeypatch, invalid):
 probe, config = make_probe(tmp_path, monkeypatch)
 path = config['paths']['primary']
 with open(path) as f: body = json.load(f)
 body['primary_generating'] = invalid
 with open(path, 'w') as f: json.dump(body, f)
 with pytest.raises(ValueError): probe.sample(lambda: 0)
