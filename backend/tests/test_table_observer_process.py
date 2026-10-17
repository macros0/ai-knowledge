"""Native independent service polling; synthetic probes exercise measurement only."""
import ctypes
import hashlib
import sys
import time
from pathlib import Path
import pytest


def probe(tmp_path):
 p=tmp_path/'probe.py'
 p.write_text("import time\nclass Probe:\n def __init__(self,c):self.c=c\n def sample(self,owned):\n  if self.c.get('hang'):time.sleep(5)\n  return {'at_unix':time.time(),'pg_other_active':0,'model_other_busy':0,'primary_generating':False,'primary_age_s':0,'owned_seen':owned()}\n def close(self):pass\ndef create_probe(c):return Probe(c)\n")
 return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}


def sampler(tmp_path,**config):
 from table_acceptance_observer_process import NativeObservationProcess
 return NativeObservationProcess(probe=probe(tmp_path),config=config)


def hold_gil():
 if sys.platform=='win32':
  call=ctypes.PyDLL('kernel32').Sleep;call.argtypes=[ctypes.c_ulong];call.restype=None;call(1800)
 else:
  call=ctypes.PyDLL(None).usleep;call.argtypes=[ctypes.c_uint];call.restype=ctypes.c_int;assert call(1800000)==0


def test_native_direct_samples_continue_during_parent_gil_hold(tmp_path):
 observer=sampler(tmp_path)
 try:
  observer.start();observer.update_model(1);start=time.monotonic();hold_gil();end=time.monotonic()
  observer.update_model(0)
 finally:observer.close()
 times=[x['observer_at_monotonic'] for x in observer.samples]
 assert not observer.errors
 assert sum(start<=t<=end for t in times)>=3
 assert max(b-a for a,b in zip(times,times[1:]))<=1.25
 assert any(x['owned_seen']==1 for x in observer.samples)
 assert observer.model_inflight==0 and observer.process.poll() is not None


def test_changed_factory_is_rejected_before_any_probe_and_child_cleaned(tmp_path):
 observer=sampler(tmp_path);Path(observer.probe['path']).write_text('raise AssertionError("do not run")')
 with pytest.raises(ValueError):observer.start()
 assert not observer.samples and observer.errors and observer.process.poll() is not None


def test_unexpected_worker_exit_invalidates_observation_and_bounded_shutdown(tmp_path):
 observer=sampler(tmp_path);observer.start();observer.process.kill();observer.process.wait(timeout=2)
 start=time.monotonic();observer.close()
 assert time.monotonic()-start<3 and observer.errors and observer.process.poll() is not None


@pytest.mark.parametrize('count',[-1,True,2,1.5])
def test_invalid_model_transition_never_becomes_own_bracket(tmp_path,count):
 observer=sampler(tmp_path)
 try:
  observer.start()
  with pytest.raises(ValueError):observer.update_model(count)
  assert observer.model_inflight==0
 finally:observer.close()


def test_failed_start_does_not_leave_owned_process(tmp_path):
 observer=sampler(tmp_path,hang=True);observer.start_timeout_seconds=.2;observer.shutdown_timeout_seconds=.2
 with pytest.raises(ValueError):observer.start()
 assert observer.process.poll() is not None and observer.errors



def test_failed_probe_close_invalidates_observation(tmp_path):
 observer=sampler(tmp_path)
 path=Path(observer.probe['path']);path.write_text(path.read_text().replace('def close(self):pass',"def close(self):raise RuntimeError('Synthetic final reconciliation failure')"))
 observer.probe['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 observer.start();observer.close()
 assert observer.process.poll()!=0
 assert observer.errors, 'A failed final probe close must invalidate the complete observation'



def test_native_model_control_updates_probe_lifetime_before_acknowledgement(tmp_path):
 observer=sampler(tmp_path)
 path=Path(observer.probe['path'])
 path.write_text("import time\nclass Probe:\n def __init__(self,c):self.started=0;self.finished=0\n def model_changed(self,before,after):\n  if after>before:self.started+=1\n  else:self.finished+=1\n def sample(self,owned):return {'at_unix':time.time(),'pg_other_active':0,'model_other_busy':0,'primary_generating':0,'primary_age_s':0,'owned_seen':owned(),'owned_started':self.started,'owned_finished':self.finished}\n def close(self):pass\ndef create_probe(c):return Probe(c)\n")
 observer.probe['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
 try:
  observer.start();observer.update_model(1);observer.sample();observer.update_model(0)
 finally:observer.close()
 assert not observer.errors
 assert any(s['owned_started']==1 and s['owned_finished']==0 and s['owned_seen']==1 for s in observer.samples)
 assert observer.samples[-1]['owned_started']==observer.samples[-1]['owned_finished']==1



def test_missing_final_model_reconciliation_invalidates_source_observation(tmp_path):
 observer=sampler(tmp_path,model_events_identity={'producer_nonce':'frozen'})
 observer.start();observer.close()
 assert observer.process.poll()==0
 assert observer.errors, 'Source mode must return a verified final reconciliation, not just exit0'


@pytest.mark.parametrize('corrupt',[False,True])
def test_native_final_reconciliation_reaches_parent_and_matches_controls(tmp_path,corrupt):
 identity={'producer_nonce':'frozen'};observer=sampler(tmp_path,model_events_identity=identity)
 path=Path(observer.probe['path']);path.write_text("import time\nclass Probe:\n def __init__(self,c):self.c=c;self.started=0;self.finished=0\n def model_changed(self,before,after):\n  if after>before:self.started+=1\n  else:self.finished+=1\n def sample(self,owned):return {'at_unix':time.time(),'pg_other_active':0,'model_other_busy':0,'primary_generating':0,'primary_age_s':0}\n def close(self):self.model_reconciliation={'known':True,'terminal_api_idle':True,'source_identity':self.c['model_events_identity'],'owned_started':self.started,'owned_completed':self.finished,'native_reserved':self.started,'native_started':self.started+int(self.c.get('corrupt',False)),'native_completed':self.finished}\ndef create_probe(c):return Probe(c)\n")
 observer.probe['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();observer.config['corrupt']=corrupt
 try:observer.start();observer.update_model(1);observer.update_model(0)
 finally:observer.close()
 assert observer.process.poll()==0
 if corrupt:assert observer.errors and observer.final_model_reconciliation is None
 else:
  assert not observer.errors and observer.final_model_reconciliation['owned_started']==1
  assert observer.model_started_calls==observer.model_completed_calls==1
