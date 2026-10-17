"""Slow observations must not add their duration to a fixed sampling period."""
import importlib.util
from pathlib import Path


def api():
    path=Path(__file__).parents[1]/'test_scripts/table_acceptance_polling.py'
    assert path.exists(), 'fixed cadence polling helper is missing'
    spec=importlib.util.spec_from_file_location('r2_polling',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_slow_read_does_not_add_half_second_sleep_to_each_sample_gap():
    clock=[0.0];times=[];waits=[]
    class Stop:
        def wait(self,delay):
            waits.append(delay)
            if len(times)==3:return True
            clock[0]+=delay;return False
    def observe():times.append(clock[0]);clock[0]+=.8
    api().poll_loop(observe,Stop(),interval_seconds=.5,monotonic=lambda:clock[0])
    assert times==[.5,1.3,2.1]
    assert max(b-a for a,b in zip(times,times[1:]))<1.25
    assert waits[1:3]==[0,0]


def test_fast_reads_keep_half_second_target_period():
    clock=[0.0];times=[]
    class Stop:
        def wait(self,delay):
            if len(times)==3:return True
            clock[0]+=delay;return False
    def observe():times.append(clock[0]);clock[0]+=.01
    api().poll_loop(observe,Stop(),interval_seconds=.5,monotonic=lambda:clock[0])
    assert times==[.5,1.0,1.5]
