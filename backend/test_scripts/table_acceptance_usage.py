"""Measurement-only actual LiteLLM usage and owned model-call lifetime.

Stream usage is requested explicitly for both benchmark variants. This changes
only measurement metadata; prompt/model/application budgets stay unchanged.
No prompt or generated text is retained by this helper.
"""
from contextlib import AbstractContextManager
import threading


def _tokens(value):
    return type(value) is int and value >= 0


class _CapturedStream:
    def __init__(self, response, capture, record):
        self.response=response; self.iterator=iter(response)
        self.capture=capture; self.record=record; self.closed=False

    def __iter__(self):return self

    def __next__(self):
        if self.closed:raise StopIteration
        try:
            chunk=next(self.iterator)
        except StopIteration:
            self.close(completed=True);raise
        except BaseException:
            self.close();raise
        self.capture._usage(self.record,chunk)
        return chunk

    def close(self, *, completed=False):
        if self.closed:return
        self.closed=True
        try:
            closer=getattr(self.response,'close',None)
            if closer is not None:closer()
        finally:
            self.capture._finish(self.record,completed=completed)

    def __getattr__(self,name):return getattr(self.response,name)


class CompletionUsageCapture(AbstractContextManager):
    def __init__(self, provider, *, started=lambda:None, finished=lambda:None):
        self.provider=provider; self.started=started; self.finished=finished
        self.lock=threading.RLock(); self.records=[]; self.streams=[]; self.original=None

    def _usage(self,record,response):
        usage=getattr(response,'usage',None)
        if usage is None:return
        values=(getattr(usage,'prompt_tokens',None),getattr(usage,'completion_tokens',None))
        with self.lock:
            if all(_tokens(v) for v in values):record['usage']=values
            else:record['invalid_usage']=True

    def _finish(self,record,*,completed):
        with self.lock:
            if not record['inflight']:return
            record.update(inflight=False,completed=completed)
        self.finished()

    def _completion(self,*args,**kwargs):
        self.started()
        record={'usage':None,'inflight':True,'completed':False,'invalid_usage':False}
        with self.lock:self.records.append(record)
        if kwargs.get('stream') is True:
            kwargs=dict(kwargs)
            kwargs['stream_options']={**kwargs.get('stream_options',{}),'include_usage':True}
        try:
            response=self.original(*args,**kwargs)
            if kwargs.get('stream') is True:
                stream=_CapturedStream(response,self,record)
                with self.lock:self.streams.append(stream)
                return stream
            self._usage(record,response);self._finish(record,completed=True)
            return response
        except BaseException:
            self._finish(record,completed=False);raise

    def result(self):
        with self.lock:
            known=[r['usage'] for r in self.records if r['completed'] and r['usage'] is not None and not r['invalid_usage']]
            return {'llm_completion_invocations':len(self.records),
                    'input_tokens':sum(v[0] for v in known),'output_tokens':sum(v[1] for v in known),
                    'llm_usage_missing':len(self.records)-len(known),
                    'llm_calls_inflight':sum(r['inflight'] for r in self.records)}

    def __enter__(self):
        if self.original is not None:raise RuntimeError('Capture already entered')
        self.original=self.provider.completion;self.provider.completion=self._completion
        return self

    def __exit__(self,*args):
        first_error = None
        try:
            for stream in self.streams:
                try:
                    stream.close()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        finally:
            self.provider.completion = self.original
        if first_error is not None:
            raise first_error
        return False
