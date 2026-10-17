"""Actual provider usage accounting, including stream lifetime and cancellation."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest


def api():
    path=Path(__file__).parents[1]/'test_scripts/table_acceptance_usage.py'
    assert path.exists(), 'actual completion usage capture is missing'
    spec=importlib.util.spec_from_file_location('r2_usage',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def chunk(prompt=10,completion=4):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=prompt,completion_tokens=completion))


def test_stream_keeps_model_owned_until_exhaustion_and_counts_usage_once():
    events=[];requests=[]
    def completion(**kwargs):
        requests.append(kwargs);return iter([SimpleNamespace(usage=None),chunk(),chunk()])
    provider=SimpleNamespace(completion=completion)
    with api().CompletionUsageCapture(provider, started=lambda:events.append('start'), finished=lambda:events.append('finish')) as capture:
        stream=provider.completion(stream=True,messages=[{'role':'user','content':'test'}])
        assert events==['start'] and capture.result()['llm_usage_missing']==1
        assert len(list(stream))==3
        assert events==['start','finish']
        assert capture.result()=={'llm_completion_invocations':1,'input_tokens':10,'output_tokens':4,'llm_usage_missing':0,'llm_calls_inflight':0}
    assert provider.completion is completion and requests[0]['stream_options']=={'include_usage':True}


@pytest.mark.parametrize('value', [None, True, -1, '10'])
def test_missing_or_invalid_provider_tokens_are_unknown(value):
    provider=SimpleNamespace(completion=lambda **kw:iter([chunk(value,4)]))
    with api().CompletionUsageCapture(provider) as capture:
        list(provider.completion(stream=True))
        assert capture.result()['llm_usage_missing']==1
        assert capture.result()['input_tokens']==0


def test_provider_failure_balances_bracket_and_retains_unknown_usage():
    events=[]
    def fail(**kwargs):raise OSError('provider unavailable')
    provider=SimpleNamespace(completion=fail)
    with api().CompletionUsageCapture(provider,started=lambda:events.append('start'),finished=lambda:events.append('finish')) as capture:
        with pytest.raises(OSError):provider.completion(stream=True)
        assert capture.result()['llm_completion_invocations']==1 and capture.result()['llm_usage_missing']==1
    assert events==['start','finish']


def test_cancelled_stream_closes_underlying_response_and_releases_model_once():
    class Response:
        closed=0
        def __iter__(self):return self
        def __next__(self):return SimpleNamespace(usage=None)
        def close(self):self.closed+=1
    response=Response();events=[];provider=SimpleNamespace(completion=lambda **kw:response)
    with api().CompletionUsageCapture(provider,finished=lambda:events.append('finish')) as capture:
        stream=provider.completion(stream=True);next(stream);stream.close();stream.close()
        assert response.closed==1 and events==['finish']
        assert capture.result()['llm_usage_missing']==1 and capture.result()['llm_calls_inflight']==0


def test_context_exit_closes_outstanding_stream_before_restoring_provider():
    events=[]
    # A real iterator with close, not instance-level special-method overrides.
    class Response:
        def __iter__(self):return self
        def __next__(self):return chunk()
        def close(self):events.append('closed')
    original=lambda **kw:Response();provider=SimpleNamespace(completion=original)
    with api().CompletionUsageCapture(provider,finished=lambda:events.append('finished')):
        provider.completion(stream=True)
    assert events==['closed','finished'] and provider.completion is original


def test_nonstream_usage_is_counted_without_changing_request_options():
    requests=[]
    def completion(**kwargs):requests.append(kwargs);return chunk(20,7)
    provider=SimpleNamespace(completion=completion)
    with api().CompletionUsageCapture(provider) as capture:
        assert provider.completion(stream=False).usage.prompt_tokens==20
        assert capture.result()['input_tokens']==20 and capture.result()['output_tokens']==7
    assert 'stream_options' not in requests[0]


def test_close_failure_still_releases_every_stream_and_restores_provider():
    events=[]
    class Response:
        def __iter__(self):return self
        def __next__(self):return chunk()
        def close(self):
            events.append('close')
            raise OSError('close failed')
    original=lambda **kw:Response();provider=SimpleNamespace(completion=original)
    capture=api().CompletionUsageCapture(provider,finished=lambda:events.append('finish'))
    with pytest.raises(OSError):
        with capture:
            provider.completion(stream=True);provider.completion(stream=True)
    assert events==['close','finish','close','finish']
    assert capture.result()['llm_calls_inflight']==0 and provider.completion is original
