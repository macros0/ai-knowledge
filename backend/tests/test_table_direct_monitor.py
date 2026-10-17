"""Operational monitor handoff preserves counters, idle tail and cleanup."""
from types import SimpleNamespace
import threading
import pytest


def setup(monkeypatch):
 from table_acceptance_direct_monitor import IndependentDirectMonitorMixin
 events=[]
 class Direct:
  def __init__(self,**kw):self.samples=[];self.errors=[];self.started=False;self.model_inflight=0;self.thread=SimpleNamespace(is_alive=lambda:False);self.process=SimpleNamespace(poll=lambda:0)
  def start(self):self.started=True;events.append('child-start')
  def update_model(self,count):self.model_inflight=count;events.append(('model-ack',count))
  def sample(self):events.append('child-sample')
  def close(self):events.append('child-close')
  def error(self,error):self.errors.append({'type':type(error).__name__})
 class Legacy:
  def __init__(self,settings,protocol):
   self.settings=settings;self.errors=[];self.samples=[];self.model_lock=threading.RLock();self.model_inflight=0
   self.finished=threading.Event();self.boundary=SimpleNamespace(snapshots=lambda:({'Query':0},{'completed_requests':0}))
   self.engine=SimpleNamespace(dispose=lambda:events.append('parent-engine-disposed'))
  def metrics(self):return {'Query':0}
  def windows_audit(self):return {'completed_requests':0}
  def result(self):return {'load_monitor_complete':not self.errors,'window':{}}
 class Monitor(IndependentDirectMonitorMixin,Legacy):pass
 monkeypatch.setattr('table_acceptance_direct_monitor.NativeObservationProcess',Direct)
 monkeypatch.setattr('table_acceptance_direct_monitor.time.sleep',lambda *a:None)
 config={'resource_environment':{'windows_identity':{'embedding_pid':1,'embedding_birth':2,'audit_identity':'own'},
             'direct_observation':{'probe':{'path':'fixture','sha256':'1'*64},'paths':{}}}}
 settings=SimpleNamespace(db_url='private',llm_base_url='http://fixture/v1')
 return Monitor(settings,config),events


def test_operational_model_ack_precedes_call_and_completed_ack_drains(monkeypatch):
 monitor,events=setup(monkeypatch);monitor.start();monitor.model_started();events.append('actual-model-call');monitor.model_finished();monitor.stop()
 assert events.index(('model-ack',1))<events.index('actual-model-call')<events.index(('model-ack',0))
 assert monitor.model_inflight==0 and monitor.result()['load_monitor_complete']
 assert events[-1]=='parent-engine-disposed' and 'child-close' in events


def test_operational_failure_still_closes_owned_child(monkeypatch):
 monitor,events=setup(monkeypatch);monitor.start()
 monitor.direct.sample=lambda:(_ for _ in ()).throw(ValueError('observation failed'))
 monitor.stop()
 assert monitor.errors and 'child-close' in events and events[-1]=='parent-engine-disposed'


def test_open_model_bracket_cannot_qualify_result(monkeypatch):
 monitor,_=setup(monkeypatch);monitor.start();monitor.model_started();monitor.stop()
 assert not monitor.result()['load_monitor_complete']


def test_model_bracket_before_child_readiness_is_unknown(monkeypatch):
 monitor,_=setup(monkeypatch)
 with pytest.raises(ValueError):monitor.model_started()
 assert monitor.model_inflight==0



def test_operational_passes_frozen_native_event_identity_to_child(monkeypatch):
 monitor,_=setup(monkeypatch);base=type(monitor.direct)
 class Capture(base):
  def __init__(self,**kwargs):super().__init__(**kwargs);self.config=kwargs['config']
 monkeypatch.setattr('table_acceptance_direct_monitor.NativeObservationProcess',Capture)
 identity={'pid':10,'birth':20,'producer_nonce':'frozen'}
 protocol={'resource_environment':{'windows_identity':{},'direct_observation':{'probe':{},'paths':{},'model_events_identity':identity}}}
 actual=type(monitor)(SimpleNamespace(db_url='private',llm_base_url='http://fixture/v1'),protocol)
 assert actual.direct.config['model_events_identity']==identity



def test_operational_result_keeps_final_model_proof_and_rejects_missing(monkeypatch):
 monitor,_=setup(monkeypatch);monitor.model_events_required=True
 monitor.direct.final_model_reconciliation=None
 assert not monitor.result()['load_monitor_complete']
 good,_=setup(monkeypatch);good.model_events_required=True
 proof={'known':True,'native_reserved':1,'native_started':1,'native_completed':1}
 good.direct.final_model_reconciliation=proof
 assert good.result()['direct_model_reconciliation']==proof
