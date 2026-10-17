"""Local generation identity validation shares the existing transport attempt budget."""
import json
import time
import pytest
from app.config import Settings
from app.services import llm_client as module, llm_profiles, gen_quality


def concept(identifier, content='Preserved fact'):
 return {'id':identifier,'title':'Fact','type':'concept','tags':[], 'content':content,'source_quotes':[content],'relations':[]}


def client(monkeypatch, **overrides):
 settings=Settings(_env_file=None,llm_profile='local_qwen',llm_model='openai/local',llm_base_url='http://127.0.0.1:1/v1',llm_api_key='local',llm_retry_attempts=2,llm_retry_backoff_seconds=0,**overrides)
 monkeypatch.setattr(module,'get_settings',lambda:settings)
 return module.LLMClient()


@pytest.mark.parametrize('bad',[ [concept('same','First fact'),concept('same','Second fact')], [concept(' ')] ])
def test_identity_invalid_response_retried_without_dropping_facts(monkeypatch,bad):
 c=client(monkeypatch);good=[concept('first','First fact'),concept('second','Second fact')];calls=[]
 def complete(system,user,**kwargs):
  calls.append({'system':system,'user':user,**kwargs});return json.dumps(bad if len(calls)==1 else good),'stop'
 monkeypatch.setattr(c,'_complete_once',complete);gen_quality.drain()
 assert c.chat_json('original system','original source',doc_id='test',chunk_idx=66)==good
 assert len(calls)==2 and calls[0]['system']=='original system' and calls[1]['system']!=calls[0]['system']
 assert all(call['user']=='original source' for call in calls)
 assert len({call['max_tokens'] for call in calls})==1
 assert not gen_quality.has_salvage(gen_quality.drain())


@pytest.mark.parametrize('network_first',[True,False])
def test_network_and_identity_errors_share_one_attempt_count(monkeypatch,network_first):
 c=client(monkeypatch);calls=[];bad=json.dumps([concept('same'),concept('same')])
 def complete(*args,**kwargs):
  calls.append(kwargs)
  if (len(calls)==1)==network_first:raise module.LLMTimeoutError('Synthetic timeout')
  return bad,'stop'
 monkeypatch.setattr(c,'_complete_once',complete)
 with pytest.raises((ValueError,module.LLMTimeoutError)):c.chat_json('s','u')
 assert len(calls)==2 and len({call['max_tokens'] for call in calls})==1


def test_persistent_bad_ids_remain_rejected_after_existing_budget(monkeypatch):
 c=client(monkeypatch);calls=[]
 def complete(*args,**kwargs):calls.append(kwargs);return json.dumps([concept('same'),concept('same')]),'stop'
 monkeypatch.setattr(c,'_complete_once',complete)
 with pytest.raises(ValueError,match='nonempty and unique'):c.chat_json('s','u')
 assert len(calls)==2


def test_valid_first_response_and_nonidentity_schema_error_do_not_retry(monkeypatch):
 c=client(monkeypatch);calls=[]
 def complete(*args,**kwargs):calls.append(kwargs);return json.dumps([concept('valid')]),'stop'
 monkeypatch.setattr(c,'_complete_once',complete)
 assert c.chat_json('s','u')==[concept('valid')];assert len(calls)==1
 calls.clear()
 monkeypatch.setattr(c,'_complete_once',lambda *a,**kw:(calls.append(kw) or json.dumps([{'id':'bad','title':'missing fields'}]),'stop'))
 with pytest.raises(ValueError):c.chat_json('s','u')
 assert len(calls)==1


def test_identity_retry_cannot_escape_shared_deadline(monkeypatch):
 c=client(monkeypatch);calls=[]
 def complete(*args,**kwargs):calls.append(kwargs);time.sleep(.04);return json.dumps([concept('same'),concept('same')]),'stop'
 monkeypatch.setattr(c,'_complete_once',complete)
 with llm_profiles.request_scope(deadline=time.monotonic()+.02):
  with pytest.raises(TimeoutError):c.chat_json('s','u')
 assert len(calls)==1


def test_output_truncation_keeps_its_existing_token_escalation(monkeypatch):
 c=client(monkeypatch,llm_max_tokens=128,llm_max_tokens_cap=256,llm_truncation_retry_attempts=1,llm_truncation_max_tokens_multiplier=2);calls=[]
 def complete(*args,**kwargs):
  calls.append(kwargs);return (json.dumps([concept('valid')]),'length') if len(calls)==1 else (json.dumps([concept('valid')]),'stop')
 monkeypatch.setattr(c,'_complete_once',complete)
 assert c.chat_json('s','u')==[concept('valid')]
 assert [call['max_tokens'] for call in calls]==[128,256]


@pytest.mark.parametrize('first_error', ['identity', 'network'])
def test_retry_count_is_per_token_level_and_truncation_keeps_shared_deadline(monkeypatch, first_error):
 c=client(monkeypatch,llm_max_tokens=128,llm_max_tokens_cap=256,llm_truncation_retry_attempts=1,llm_truncation_max_tokens_multiplier=2)
 calls=[];good=json.dumps([concept('valid')]);bad=json.dumps([concept('same'),concept('same')])
 def complete(system,user,**kwargs):
  calls.append({'system':system,'user':user,**kwargs})
  if len(calls) in (1,3):
   if first_error=='network':raise module.LLMTimeoutError('Synthetic timeout')
   return bad,'stop'
  return good,'length' if len(calls)==2 else 'stop'
 monkeypatch.setattr(c,'_complete_once',complete);gen_quality.drain()
 with llm_profiles.request_scope(deadline=time.monotonic()+5):
  assert c.chat_json('original system','original source')==[concept('valid')]
 assert [call['max_tokens'] for call in calls]==[128,128,256,256]
 assert all(call['user']=='original source' for call in calls)
 assert not gen_quality.has_salvage(gen_quality.drain())


def test_identity_then_truncation_does_not_reset_outer_deadline(monkeypatch):
 c=client(monkeypatch,llm_max_tokens=128,llm_max_tokens_cap=256,llm_truncation_retry_attempts=1,llm_truncation_max_tokens_multiplier=2)
 calls=[]
 def complete(*args,**kwargs):
  calls.append(kwargs)
  if len(calls)==1:return json.dumps([concept('same'),concept('same')]),'stop'
  time.sleep(.04);return json.dumps([concept('valid')]),'length'
 monkeypatch.setattr(c,'_complete_once',complete)
 with llm_profiles.request_scope(deadline=time.monotonic()+.02):
  with pytest.raises(TimeoutError):c.chat_json('s','u')
 assert [call['max_tokens'] for call in calls]==[128,128]
