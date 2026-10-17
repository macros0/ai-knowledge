"""Native observers across real Pipeline; synthetic providers, no acceptance."""
import ctypes,hashlib,importlib.util,json,os,sys,threading,time
from pathlib import Path
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize('enabled',[False,True])
def test_native_observers_across_complete_pipeline(tmp_path,monkeypatch,enabled):
 from table_acceptance_direct_monitor import IndependentDirectMonitorMixin
 spec=importlib.util.spec_from_file_location('native_fixture',Path(__file__).with_name('test_table_resource_collector.py'));fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture);collector=fixture.api()
 probe=tmp_path/'synthetic_probe.py';probe.write_text("import time\nclass Probe:\n def sample(self,owned):return {'at_unix':time.time(),'pg_other_active':0,'model_other_busy':0,'primary_generating':0,'primary_age_s':0,'owned_seen':owned()}\n def close(self):pass\ndef create_probe(c):return Probe()\n")
 class Legacy:
  def __init__(self,settings,protocol):
   self.samples=[];self.errors=[];self.model_lock=threading.RLock();self.model_inflight=0;self.finished=threading.Event();self.boundary=SimpleNamespace(snapshots=lambda:({'Query':0},{'completed_requests':0}));self.engine=SimpleNamespace(dispose=lambda:None)
  def metrics(self):return {'Query':0}
  def windows_audit(self):return {'completed_requests':0}
  def result(self):return {'load_monitor_complete':not self.errors,'window':{}}
 class Monitor(IndependentDirectMonitorMixin,Legacy):pass
 protocol={'resource_environment':{'windows_identity':{},'direct_observation':{'probe':{'path':str(probe),'sha256':hashlib.sha256(probe.read_bytes()).hexdigest()},'paths':{}}}}
 native=Monitor(SimpleNamespace(db_url='synthetic',llm_base_url='http://synthetic/v1'),protocol);samplers=[];holds=[];captured=[];Base=collector.PipelineRssSampler
 class Sampler(Base):
  def __init__(self,*a,**kw):super().__init__(*a,**kw);samplers.append(self)
 monkeypatch.setattr(collector,'PipelineRssSampler',Sampler)
 class Observation:
  def start(self):native.start()
  def finish(self,coverage):native.stop()
 original=collector.collect_pipeline
 def collect(**kwargs):
  original_factory=kwargs['factory']
  def factory():
   import app.services.pipeline as module
   pipeline=original_factory();stage=module.start_stage
   def generation(name,*a,**kw):
    if name!='generate':return stage(name,*a,**kw)
    native.model_started();began=time.monotonic()
    try:
     if sys.platform=='win32':
      call=ctypes.PyDLL('kernel32').Sleep;call.argtypes=[ctypes.c_ulong];call.restype=None;call(1800)
     else:
      call=ctypes.PyDLL(None).usleep;call.argtypes=[ctypes.c_uint];call.restype=ctypes.c_int;assert call(1800000)==0
     return stage(name,*a,**kw)
    finally:holds.append((began,time.monotonic()));native.model_finished()
   monkeypatch.setattr(module,'start_stage',generation);return pipeline
  result=original(**dict(kwargs,factory=factory,observation=Observation()));captured.append(result);return result
 monkeypatch.setattr(collector,'collect_pipeline',collect);monkeypatch.setattr(fixture,'api',lambda:collector)
 fixture.test_real_synthetic_pipeline_all_phases_and_successful_publication(tmp_path,monkeypatch,enabled)
 result=captured[0];direct=native.result();times=[s['observer_at_monotonic'] for s in native.samples];rss_times=result['rss_coverage']['sample_times'];sampler=samplers[0]
 assert holds and result['pipeline_complete'] and result['rss_sampling_complete'] and direct['load_monitor_complete']
 assert all(sum(a<=t<=b for t in times)>=3 for a,b in holds) and all(sum(a<=t<=b for t in rss_times)>=6 for a,b in holds)
 assert max(b-a for a,b in zip(times,times[1:]))<=1.25 and native.direct.process.pid in sampler.births
 assert any(s['owned_seen']==1 for s in native.samples) and native.direct.model_inflight==native.model_inflight==0
 assert native.direct.process.poll() is not None and sampler.process.poll() is not None
 area=os.environ.get('R2_DIRECT_INTEGRATION_PROOF_DIR')
 if area:
  target=Path(area);target.mkdir(parents=True,exist_ok=True);file=target/('after.json' if enabled else 'baseline.json');assert not file.exists()
  file.write_text(json.dumps({'pipeline_complete':True,'rss_complete':True,'all7_phases':len(result['sampled_phases'])==7,'direct_errors':native.errors,'direct_count':len(times),'direct_gap_seconds':max(b-a for a,b in zip(times,times[1:])),'rss_gap_seconds':max(b-a for a,b in zip(rss_times,rss_times[1:])),'direct_during_generation':[sum(a<=t<=b for t in times) for a,b in holds],'rss_during_generation':[sum(a<=t<=b for t in rss_times) for a,b in holds],'direct_child_in_rss_tree':True,'children_stopped':True,'owned_model_drained':True,'scope':'Actual Pipeline, SQLite publication and local Qdrant; fake providers/direct counters, no C/D2/CPU acceptance'},indent=2))
