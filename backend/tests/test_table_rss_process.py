"""Native acquisition while the measured Python parent holds its GIL."""
import ctypes
import io
import json
from types import SimpleNamespace

import pytest
import os
import sys
import time
from pathlib import Path
import importlib.util


def api():
 path=Path(__file__).parents[1]/'test_scripts/collect_table_resources.py'
 spec=importlib.util.spec_from_file_location('rss_process_collector',path)
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def finish(sampler):
 for phase in sampler.trace.phase_counts:sampler.phase(phase)
 return sampler.finish(publication_completed=True)


def hold_gil():
 if sys.platform=='win32':
  sleep=ctypes.PyDLL('kernel32').Sleep;sleep.argtypes=[ctypes.c_ulong];sleep.restype=None;sleep(1800)
 else:
  sleep=ctypes.PyDLL(None).usleep;sleep.argtypes=[ctypes.c_uint];sleep.restype=ctypes.c_int;assert sleep(1800000)==0


def test_default_native_sampler_keeps_acquiring_during_parent_gil_hold():
 sampler=api().PipelineRssSampler();sampler.start();sampler.phase('generation');time.sleep(.25)
 start=time.monotonic();hold_gil();end=time.monotonic();result=finish(sampler)
 times=result['rss_coverage']['sample_times']
 assert result['rss_sampling_complete'],result['rss_coverage_error']
 assert sum(start<=x<=end for x in times)>=6
 assert sampler.process.pid!=os.getpid() and sampler.process.poll() is not None
 assert sampler.process.pid in sampler.births


def test_unexpected_owned_observer_exit_is_invalid_and_shutdown_bounded():
 sampler=api().PipelineRssSampler(join_timeout_seconds=.4);sampler.start()
 assert sampler.process is not None
 sampler.process.kill();sampler.process.wait(timeout=2)
 began=time.monotonic();result=finish(sampler)
 assert time.monotonic()-began<3
 assert not result['rss_sampling_complete'] and result['rss_sampler_error_count']>0
 assert sampler.process.poll() is not None


def test_native_observer_is_in_declared_tree_and_stops_after_publication():
 sampler=api().PipelineRssSampler();sampler.start()
 assert sampler.process.pid in sampler.last_sample['pids']
 result=finish(sampler)
 assert result['rss_sampling_complete'] and result['rss_sampler_error_count']==0
 assert sampler.process.poll() is not None
 assert result['rss_acquisition']=='independent-child-process'
 assert result['rss_observer_included_in_tree'] is True


def test_observer_identity_mismatch_prevents_readiness_and_cleans_up(monkeypatch):
 from run_table_acceptance import _birth
 parent=os.getpid()
 monkeypatch.setattr('run_table_acceptance._birth',lambda pid:_birth(pid)+(0 if pid==parent else 1))
 sampler=api().PipelineRssSampler()
 with pytest.raises(ValueError,match='unready'):sampler.start()
 assert sampler.trace.error_count>0 and sampler.process.poll() is not None
 assert not sampler.thread.is_alive()


@pytest.mark.parametrize('fault',['nonce','pid','birth','native_error','unacknowledged','malformed'])
def test_corrupt_or_unmatched_ipc_never_becomes_complete(fault):
 from table_acceptance_rss_process import NativeRssProcess
 from table_acceptance_rss import RssTrace
 trace=RssTrace();observer=NativeRssProcess(lambda *args:None,trace,.1);observer.birth=2
 frame={'nonce':observer.nonce,'observer_pid':1,'observer_birth':2,'phase':'construction','sequence':3,
        'sample':{'pids':[1],'birth_ids':{'1':2},'rss_bytes':100},'at_monotonic':1}
 if fault=='nonce':frame['nonce']='other'
 elif fault=='pid':frame['observer_pid']=3
 elif fault=='birth':frame['observer_birth']=3
 elif fault=='native_error':frame['error']=True
 raw=b'broken\n' if fault=='malformed' else json.dumps(frame).encode()+b'\n'
 observer.process=SimpleNamespace(pid=1,stdout=io.BytesIO(raw));observer._read()
 assert trace.error_count>0 and observer.acknowledged==0


def test_eof_during_final_boundary_does_not_acknowledge_publication():
 import threading
 from table_acceptance_rss_process import NativeRssProcess
 from table_acceptance_rss import RssTrace
 trace=RssTrace();observer=NativeRssProcess(lambda *args:None,trace,.03)
 observer.process=SimpleNamespace(poll=lambda:None,stdin=io.BytesIO())
 observer.thread=SimpleNamespace(is_alive=lambda:True)
 # Even a wakeup from a dead reader does not substitute for an acknowledgement.
 def wake():
  while not observer.pending:time.sleep(.001)
  next(iter(observer.pending.values())).set()
 helper=threading.Thread(target=wake);helper.start();observer.request('publication',finish=True);helper.join(timeout=1)
 assert observer.acknowledged==0 and trace.error_count==1
