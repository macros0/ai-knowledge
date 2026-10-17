"""Native event hints supplement API observations and preserve peer ownership."""
import json,time
import pytest
import requests
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('direct_probe_fixture',Path(__file__).with_name('test_table_direct_probe.py'))
_fixture=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_fixture)
make_probe=_fixture.make_probe


def prepare(tmp_path,monkeypatch):
 probe,config=make_probe(tmp_path,monkeypatch)
 identity={'pid':10,'birth':20,'command_sha256':'a'*64,'invocation':'inv','stream':'stream','boot':'boot','producer_nonce':'n'*48,'journal_schema_version':2}
 config['model_events_identity']=identity
 def frame(busy=False,started=0,completed=0):
  return {'known':True,'identity':dict(identity),'at_unix':time.time(),'acquisition_seconds':.01,'initial_idle_proven':True,'busy_confirmed':busy,'active_task':{'slot':0,'task':7} if busy else None,'started_tasks':started,'completed_tasks':completed,'idle_proven':False,'reservation_required':True,'reserved_tasks':started,'busy_phase':'processing' if busy else None}
 def publish(value):
  path=config['paths']['host'];body=json.loads(open(path).read());body['model_events']=value;body['snapshot_read_seconds']=.01
  with open(path,'w') as sink:json.dump(body,sink)
 publish(frame());probe.sample(lambda:0)
 return probe,config,frame,publish


def test_known_busy_interval_avoids_queued_slots_api_without_skipping_peers(tmp_path,monkeypatch):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch);probe.model_changed(0,1);publish(frame(True,1,0))
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',lambda *a,**kw:pytest.fail('Busy hint must avoid queued slots'))
 result=probe.sample(lambda:1)
 assert result['model_other_busy']==0 and result['model_evidence_source']=='native-journal-busy'


@pytest.mark.parametrize('fault',['missing','unknown','identity','stale','future','initial_idle','age_type','counts_type','counts_mismatch','slot','idle_claim'])
def test_bad_event_frame_never_falls_back_to_api_success(tmp_path,monkeypatch,fault):
 probe,config,frame,publish=prepare(tmp_path,monkeypatch);value=frame(True,1,0)
 if fault=='missing':value=None
 elif fault=='unknown':value['known']=False
 elif fault=='identity':value['identity']['producer_nonce']='changed'
 elif fault=='stale':value['at_unix']-=2
 elif fault=='future':value['at_unix']+=2
 elif fault=='initial_idle':value['initial_idle_proven']=False
 elif fault=='age_type':value['acquisition_seconds']=True
 elif fault=='counts_type':value['started_tasks']=True
 elif fault=='counts_mismatch':value['completed_tasks']=1
 elif fault=='slot':value['active_task']['slot']=1
 else:value['idle_proven']=True
 publish(value)
 with pytest.raises(ValueError):probe.sample(lambda:1)


def test_busy_source_still_requires_owned_call_bracket_and_fresh_exclusive_peer(tmp_path,monkeypatch):
 probe,config,frame,publish=prepare(tmp_path,monkeypatch);publish(frame(True,1,0))
 response=type('Response',(),{'raise_for_status':lambda self:None,'json':lambda self:[{'is_processing':True}]})()
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',lambda *a,**kw:response)
 assert probe.sample(lambda:0)['model_other_busy']==1
 body=json.loads(open(config['paths']['host']).read());body['model_connections']['other_established']=1
 with open(config['paths']['host'],'w') as sink:json.dump(body,sink)
 with pytest.raises(ValueError):probe.sample(lambda:1)


def test_api_timeout_may_use_a_new_fresh_busy_event(tmp_path,monkeypatch):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch);probe.model_changed(0,1)
 def timeout(*a,**kw):publish(frame(True,1,0));raise requests.Timeout('Synthetic queued slots')
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',timeout)
 assert probe.sample(lambda:1)['model_other_busy']==0


def test_api_timeout_without_busy_evidence_remains_unknown(tmp_path,monkeypatch):
 probe,_,_,_=prepare(tmp_path,monkeypatch)
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',lambda *a,**kw:(_ for _ in ()).throw(requests.Timeout('Synthetic queued slots')))
 with pytest.raises(ValueError):probe.sample(lambda:0)


def test_inactive_event_state_never_proves_idle_instead_of_api(tmp_path,monkeypatch):
 probe,_,_,_=prepare(tmp_path,monkeypatch)
 response=type('Response',(),{'raise_for_status':lambda self:None,'json':lambda self:[{'is_processing':True}]})()
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',lambda *a,**kw:response)
 assert probe.sample(lambda:0)['model_other_busy']==1


@pytest.mark.parametrize('fault',['unreleased','missing_start','extra_task'])
def test_final_reconciliation_rejects_lost_or_unowned_task_lifetimes(tmp_path,monkeypatch,fault):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch);probe.model_changed(0,1);probe.model_changed(1,0)
 publish(frame(True,1,0) if fault=='unreleased' else frame(False,0,0) if fault=='missing_start' else frame(False,2,2))
 with pytest.raises(ValueError):probe.close()


def test_final_reconciliation_requires_api_idle_and_matching_own_task_counts(tmp_path,monkeypatch):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch);probe.model_changed(0,1);probe.model_changed(1,0);publish(frame(False,1,1));probe.close()
 assert probe.model_final_reconciled is True


@pytest.mark.parametrize('identity',[None,{}, {'pid':True}])
def test_invalid_expected_identity_never_activates_source(tmp_path,monkeypatch,identity):
 probe,config,frame,publish=prepare(tmp_path,monkeypatch);config['model_events_identity']=identity;value=frame();value['identity']=identity;publish(value)
 with pytest.raises(ValueError):probe.sample(lambda:0)


def test_regular_slots_api_cannot_claim_multislot_idle(tmp_path,monkeypatch):
 probe,_,_,_=prepare(tmp_path,monkeypatch)
 response=type('Response',(),{'raise_for_status':lambda self:None,'json':lambda self:[{'is_processing':False},{'is_processing':False}]})()
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',lambda *a,**kw:response)
 with pytest.raises(ValueError):probe.sample(lambda:0)



def test_confirmed_reservation_covers_own_cache_initialization(tmp_path,monkeypatch):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch);probe.model_changed(0,1)
 value=frame(True,0,0);value.update(reserved_tasks=1,busy_phase='reservation',active_task={'slot':0,'task':-1});publish(value)
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',lambda *a,**kw:pytest.fail('Confirmed reservation must avoid queued API'))
 assert probe.sample(lambda:1)['model_other_busy']==0


@pytest.mark.parametrize('fault',['reserved_type','reservation_count','reservation_task','missing_policy'])
def test_untrusted_reservation_frame_is_unknown(tmp_path,monkeypatch,fault):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch);value=frame(True,0,0);value.update(reserved_tasks=1,busy_phase='reservation',active_task={'slot':0,'task':-1})
 if fault=='reserved_type':value['reserved_tasks']=True
 elif fault=='reservation_count':value['reserved_tasks']=2
 elif fault=='reservation_task':value['active_task']['task']=7
 else:value['reservation_required']=False
 publish(value)
 with pytest.raises(ValueError):probe.sample(lambda:1)


def test_missing_reservation_counter_cannot_pass_final_reconciliation(tmp_path,monkeypatch):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch);probe.model_changed(0,1);probe.model_changed(1,0);value=frame(False,1,1);value['reserved_tasks']=0;publish(value)
 with pytest.raises(ValueError):probe.close()


@pytest.mark.parametrize('api_busy',[False,True])
def test_no_owned_call_rechecks_api_instead_of_reusing_old_busy_frame(tmp_path,monkeypatch,api_busy):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch)
 probe.model_changed(0,1);probe.model_changed(1,0)
 value=frame(True,1,0);publish(value)
 calls=[]
 response=type('Response',(),{'raise_for_status':lambda self:None,'json':lambda self:[{'is_processing':api_busy}]})()
 def get(*args,**kwargs):calls.append(kwargs['timeout']);return response
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',get)
 result=probe.sample(lambda:0)
 assert calls==[.25]
 assert result['model_other_busy']==int(api_busy)
 assert result['model_evidence_source']=='slots-api'


def test_no_owned_call_and_unavailable_api_cannot_turn_busy_hint_into_idle(tmp_path,monkeypatch):
 probe,_,frame,publish=prepare(tmp_path,monkeypatch)
 probe.model_changed(0,1);probe.model_changed(1,0);publish(frame(True,1,0))
 def timeout(*args,**kwargs):raise requests.Timeout('Synthetic queued slots')
 monkeypatch.setattr('table_acceptance_direct_probe.requests.get',timeout)
 assert probe.sample(lambda:0)['model_other_busy']==1
