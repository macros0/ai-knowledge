import pytest
from table_acceptance_model_events import ModelBusyEvents,ModelEventUnknown


def state():return ModelBusyEvents(pid=10,invocation='inv',stream='stream',boot='boot',origin_lower_us=1000000)
def event(kind='launch_slot_',task=7,native='0.01.000.000',ingested='2010000',cursor='a'):
 return {'_PID':'10','_SYSTEMD_INVOCATION_ID':'inv','_STREAM_ID':'stream','_BOOT_ID':'boot','__CURSOR':cursor,'__REALTIME_TIMESTAMP':ingested,'MESSAGE':f'{native} I slot {kind}: id 0 | task {task} | metadata'}


def test_start_confirms_busy_but_release_never_confirms_idle():
 s=state();assert not s.snapshot(read_duration_seconds=.01,observation_age_seconds=0)['idle_proven']
 s.feed(event());assert s.snapshot(read_duration_seconds=.01,observation_age_seconds=0)['busy_confirmed']
 s.feed(event('release',native='0.02.000.000',ingested='3010000',cursor='b'))
 result=s.snapshot(read_duration_seconds=.01,observation_age_seconds=0);assert not result['busy_confirmed'] and not result['idle_proven']


@pytest.mark.parametrize('fault',['pid','invocation','stream','boot','late','missing_clock','clock_back','overlap','wrong_release','duplicate','suppressed','slots'])
def test_unknown_or_changed_event_stream_fails_closed(fault):
 s=state();s.feed(event());row=event('release',native='0.02.000.000',ingested='3010000',cursor='b')
 if fault=='pid':row['_PID']='11'
 elif fault=='invocation':row['_SYSTEMD_INVOCATION_ID']='other'
 elif fault=='stream':row['_STREAM_ID']='other'
 elif fault=='boot':row['_BOOT_ID']='other'
 elif fault=='late':row['__REALTIME_TIMESTAMP']='5000000'
 elif fault=='missing_clock':del row['__REALTIME_TIMESTAMP']
 elif fault=='clock_back':row['MESSAGE']=row['MESSAGE'].replace('0.02.000.000','0.00.900.000')
 elif fault=='overlap':row=event(native='0.02.000.000',ingested='3010000',cursor='b')
 elif fault=='wrong_release':row=event('release',task=8,native='0.02.000.000',ingested='3010000',cursor='b')
 elif fault=='duplicate':row=event()
 elif fault=='suppressed':row['MESSAGE']='Suppressed 8 messages'
 else:row['MESSAGE']=row['MESSAGE'].replace('id 0','id 1')
 with pytest.raises(ModelEventUnknown):s.feed(row)
 with pytest.raises(ModelEventUnknown):s.snapshot(read_duration_seconds=.01,observation_age_seconds=0)


@pytest.mark.parametrize('duration',[1.251,-1,True,None])
def test_acquisition_bound_not_relaxed(duration):
 with pytest.raises(ModelEventUnknown):state().snapshot(read_duration_seconds=duration,observation_age_seconds=0)


@pytest.mark.parametrize('age', [None, 1.251, -1, True, float('nan')])
def test_observation_age_is_required_and_bounded(age):
 with pytest.raises(ModelEventUnknown):
  state().snapshot(read_duration_seconds=.01, observation_age_seconds=age)


def test_source_timestamp_guard_also_covers_non_slot_records():
 s=state();row=event();row['MESSAGE']='Unrelated metadata';s.feed(row)
 other=event(cursor='b',ingested='2009999');other['MESSAGE']='Unrelated metadata'
 with pytest.raises(ModelEventUnknown):s.feed(other)


def test_incomplete_busy_lifetime_cannot_finish_qualification():
 s=state();s.feed(event())
 with pytest.raises(ModelEventUnknown):s.finish()


def test_event_cursor_tracking_memory_is_bounded():
 import gc,tracemalloc
 s=state()
 def feed_many(start,end):
  for i in range(start,end):
   row=event(cursor=f'cursor-{i}',ingested=str(2010000+i));row['MESSAGE']='Unrelated metadata';s.feed(row)
 tracemalloc.start()
 try:
  feed_many(0,500);gc.collect();before=tracemalloc.get_traced_memory()[0]
  feed_many(500,20500);gc.collect();after=tracemalloc.get_traced_memory()[0]
  assert after-before<256*1024
 finally:tracemalloc.stop()


def test_complete_lifetime_finishes_without_proving_idle():
 s=state();s.feed(event());s.feed(event('release',native='0.02.000.000',ingested='3010000',cursor='b'))
 assert s.finish()=={'started_tasks':1,'completed_tasks':1,'idle_proven':False}


def test_acquisition_and_receipt_age_share_the_freshness_limit():
 with pytest.raises(ModelEventUnknown):state().snapshot(read_duration_seconds=.8,observation_age_seconds=.5)



def test_distinct_journal_records_can_share_one_timestamp():
 s=state();s.feed(event())
 row=event(cursor='b');row['MESSAGE']='Unrelated metadata';s.feed(row)
 assert s.snapshot(read_duration_seconds=.01,observation_age_seconds=0)['busy_confirmed']


def test_equal_timestamp_cursor_group_has_a_fixed_capacity():
 s=state()
 for i in range(4096):
  row=event(cursor=f'cursor-{i}');row['MESSAGE']='Unrelated metadata';s.feed(row)
 overflow=event(cursor='cursor-4096');overflow['MESSAGE']='Unrelated metadata'
 with pytest.raises(ModelEventUnknown):s.feed(overflow)


def test_nonadjacent_replay_in_same_timestamp_group_is_unknown():
 s=state()
 for cursor in ('a','b','c'):
  row=event(cursor=cursor);row['MESSAGE']='Unrelated metadata';s.feed(row)
 old=event(cursor='a');old['MESSAGE']='Unrelated metadata'
 with pytest.raises(ModelEventUnknown):s.feed(old)



def reservation_state():
 return ModelBusyEvents(pid=10,invocation='inv',stream='stream',boot='boot',origin_lower_us=1000000,require_reservation=True)


def reservation(cursor='reserve',ingested='2010000'):
 row=event(cursor=cursor,ingested=ingested)
 row['MESSAGE']='0.01.000.000 I slot get_availabl: id 0 | task -1 | selected slot by LRU, t_last = 1'
 return row


def test_confirmed_slot_reservation_covers_cache_work_before_launch():
 s=reservation_state();s.feed(reservation());frame=s.snapshot(read_duration_seconds=.01,observation_age_seconds=0)
 assert frame['busy_confirmed'] and frame['busy_phase']=='reservation' and frame['active_task']=={'slot':0,'task':-1}
 assert frame['reserved_tasks']==1 and frame['started_tasks']==0
 s.feed(event(cursor='launch',native='0.02.000.000',ingested='3010000'))
 assert s.snapshot(read_duration_seconds=.01,observation_age_seconds=0)['busy_phase']=='processing'
 s.feed(event('release',cursor='end',native='0.03.000.000',ingested='4010000'))
 s.finish()


@pytest.mark.parametrize('fault',['missing_reservation','overlapping_reservation','unlaunched_reservation','busy_old_task'])
def test_unmatched_or_untrusted_reservation_cannot_qualify(fault):
 s=reservation_state()
 with pytest.raises(ModelEventUnknown):
  if fault=='missing_reservation':s.feed(event())
  elif fault=='busy_old_task':
   row=reservation();row['MESSAGE']=row['MESSAGE'].replace('task -1','task 7');s.feed(row)
  else:
   s.feed(reservation())
   if fault=='overlapping_reservation':s.feed(reservation(cursor='other',ingested='2010001'))
   else:s.finish()
