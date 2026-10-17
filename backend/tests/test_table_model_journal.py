"""Cursor-contiguous native model evidence with process and clock guards."""
import pytest
from table_acceptance_model_events import ModelEventUnknown
from table_acceptance_model_journal import NativeModelJournal


def entry(cursor='seed', message='Metadata', realtime='2010000'):
 return {'_PID':'10','_SYSTEMD_INVOCATION_ID':'inv','_STREAM_ID':'stream','_BOOT_ID':'boot','__CURSOR':cursor,'__REALTIME_TIMESTAMP':realtime,'MESSAGE':message}


def factory(**overrides):
 clock={'mono':10.0,'wall':1000.0};pin={'pid':10,'birth':20,'command_sha256':'a'*64};anchor=entry();batches=[]
 def query(cursor):return batches.pop(0) if batches else [entry(cursor=cursor)]
 config=dict(initial_entry=anchor,initial_slots=[{'is_processing':False}],initial_slots_seconds=.01,origin_lower_us=1000000,identity=pin,check_identity=lambda:dict(pin),query=query,monotonic=lambda:clock['mono'],wallclock=lambda:clock['wall'])
 config.update(overrides);reader=NativeModelJournal(**config)
 return reader,clock,pin,batches


def test_empty_contiguous_read_never_proves_idle():
 reader,_,_,_=factory();frame=reader.read()
 assert frame['known'] and not frame['busy_confirmed'] and not frame['idle_proven']
 assert frame['initial_idle_proven'] and frame['started_tasks']==frame['completed_tasks']==0


def test_start_and_release_are_bound_to_cursor_and_process():
 reader,_,_,batches=factory();start=entry('start','0.01.000.000 I slot launch_slot_: id 0 | task 7 | metadata');reserved=entry('reserve','0.00.900.000 I slot get_availabl: id 0 | task -1 | selected slot by LRU, t_last = 1','1910000');batches.append([entry(),reserved,start]);first=reader.read()
 assert first['busy_confirmed'] and first['active_task']=={'slot':0,'task':7}
 release=entry('end','0.02.000.000 I slot release: id 0 | task 7 | metadata','3010000');batches.append([start,release]);last=reader.finish(final_slots=[{'is_processing':False}],final_slots_seconds=.01)
 assert last['started_tasks']==last['completed_tasks']==1 and not last['idle_proven']
 assert last['terminal_idle_proven']


@pytest.mark.parametrize('slots',[[{'is_processing':True}],[],[{'is_processing':False},{'is_processing':False}],[{'is_processing':None}]])
def test_initial_api_must_prove_the_single_slot_idle(slots):
 with pytest.raises(ModelEventUnknown):factory(initial_slots=slots)


@pytest.mark.parametrize('seconds',[True,None,-1,1.251,float('nan')])
def test_initial_api_read_duration_is_bounded(seconds):
 with pytest.raises(ModelEventUnknown):factory(initial_slots_seconds=seconds)


@pytest.mark.parametrize('fault',['identity','cursor_loss','old_anchor_identity','clock_jump','gap','backward_clock','empty','too_many','release_without_start','late_event'])
def test_native_acquisition_unknown_is_permanent(fault):
 reader,clock,pin,batches=factory();reader.read()
 if fault=='identity':pin['birth']+=1
 elif fault=='cursor_loss':batches.append([entry('other')])
 elif fault=='old_anchor_identity':row=entry();row['_PID']='11';batches.append([row])
 elif fault=='clock_jump':clock['wall']+=.2
 elif fault=='gap':clock['mono']+=1.251;clock['wall']+=1.251
 elif fault=='backward_clock':clock['mono']-=.01;clock['wall']-=.01
 elif fault=='empty':batches.append([])
 elif fault=='too_many':batches.append([entry()]*4097)
 elif fault=='release_without_start':batches.append([entry(),entry('end','0.02.000.000 I slot release: id 0 | task 7 | metadata','3010000')])
 else:batches.append([entry(),entry('late','0.01.000.000 I slot launch_slot_: id 0 | task 7 | metadata','5000000')])
 with pytest.raises(ModelEventUnknown):reader.read()
 with pytest.raises(ModelEventUnknown):reader.read()


def test_query_duration_includes_identity_check_and_cannot_move_timestamp_forward():
 clock={'mono':10.,'wall':1000.}
 def slow(cursor):clock['mono']+=1.26;clock['wall']+=1.26;return [entry(cursor)]
 reader,_,_,_=factory(query=slow,monotonic=lambda:clock['mono'],wallclock=lambda:clock['wall'])
 with pytest.raises(ModelEventUnknown):reader.read()


def test_finish_requires_no_unresolved_busy_task_even_when_api_says_idle():
 reader,_,_,batches=factory();start=entry('start','0.01.000.000 I slot launch_slot_: id 0 | task 7 | metadata');reserved=entry('reserve','0.00.900.000 I slot get_availabl: id 0 | task -1 | selected slot by LRU, t_last = 1','1910000');batches.append([entry(),reserved,start]);reader.read()
 with pytest.raises(ModelEventUnknown):reader.finish(final_slots=[{'is_processing':False}],final_slots_seconds=.01)


def test_finish_does_not_replace_api_with_a_release_event():
 reader,_,_,_=factory()
 with pytest.raises(ModelEventUnknown):reader.finish(final_slots=[{'is_processing':True}],final_slots_seconds=.01)
