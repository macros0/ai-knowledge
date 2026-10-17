"""Actual loopback HTTP verifies total Ollama deadlines, without model inference."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

import pytest

from app.config import Settings
from app.services.embedder import Embedder
from app.services.errors import EmbedderError


@contextmanager
def provider(mode='normal'):
    requests=[];disconnected=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            requests.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            data={'embeddings':[[1.0]*8], 'prompt_eval_count':3}
            if mode=='wrong_count':data['embeddings']=[]
            if mode=='invalid_vector':data['embeddings']=[[True]*8]
            body=json.dumps(data).encode()
            try:
                if mode=='headers':time.sleep(1.2)
                status=400 if mode=='oversize' else 200
                if mode=='physical_limit' and requests[-1][1].get('options',{}).get('num_batch')!=4096:status=413
                self.send_response(status)
                self.send_header('Content-Length',str(len(body)));self.end_headers()
                if mode=='drip':
                    for byte in body:
                        self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.025)
                else:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):disconnected.set()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',requests,disconnected
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def settings(url,timeout=.35):
    return Settings(_env_file=None,embedding_provider='http',embedding_model='ollama/bge-m3',
        embedding_api_base=url,embedding_dimensions=8,embedding_batch_size=64,
        embedding_timeout_seconds=timeout,embedding_retry_attempts=1,
        embedding_retry_backoff_seconds=0,diagnostics_enabled=False)


@pytest.mark.parametrize('mode',['headers','drip'])
def test_real_http_total_deadline_rejects_late_or_continuously_dripping_response(monkeypatch,mode):
    with provider(mode) as (url,requests,disconnected):
        monkeypatch.setattr('app.services.embedder.get_settings',lambda:settings(url))
        began=time.monotonic()
        with pytest.raises(EmbedderError):Embedder().embed('whole input')
        assert time.monotonic()-began<1.5
        assert len(requests)==1 and requests[0][1]['truncate'] is False
        if mode=='drip':assert disconnected.wait(1)


@pytest.mark.parametrize('mode',['oversize','wrong_count','invalid_vector'])
def test_native_http_bad_result_never_becomes_a_vector(monkeypatch,mode):
    with provider(mode) as (url,requests,_):
        monkeypatch.setattr('app.services.embedder.get_settings',lambda:settings(url,2))
        with pytest.raises(EmbedderError):Embedder().embed('whole input')
        assert len(requests)==1


def test_native_http_preserves_body_and_order(monkeypatch):
    with provider() as (url,requests,_):
        monkeypatch.setattr('app.services.embedder.get_settings',lambda:settings(url,2))
        assert Embedder().embed('0 / False')==[1.0]*8
        assert requests==[('/api/embed',{'model':'bge-m3','input':['0 / False'],'truncate':False})]


def test_sync_embedder_also_works_inside_running_event_loop(monkeypatch):
    with provider() as (url,_,_):
        monkeypatch.setattr('app.services.embedder.get_settings',lambda:settings(url,2))
        async def invoke():return Embedder().embed('from async route')
        assert asyncio.run(invoke())==[1.0]*8



def test_sync_response_decoding_cannot_return_success_after_total_deadline(monkeypatch):
    import httpx
    from types import SimpleNamespace
    async def post(self,*args,**kwargs):
        def decode():
            time.sleep(.5)
            return {'embeddings':[[1.0]*8]}
        return SimpleNamespace(raise_for_status=lambda:None,json=decode)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    monkeypatch.setattr('app.services.embedder.get_settings',lambda:settings('http://127.0.0.1:1',.1))
    with pytest.raises(EmbedderError):Embedder().embed('whole input')


def test_explicit_ollama_physical_batch_preserves_full_input_without_context_override(monkeypatch):
    with provider('physical_limit') as (url,requests,_):
        base=settings(url,2)
        monkeypatch.setattr('app.services.embedder.get_settings',lambda:base)
        with pytest.raises(EmbedderError):Embedder().embed('full unchanged input')
        configured=Settings(_env_file=None,**base.model_dump(exclude={'embedding_ollama_num_batch'}),embedding_ollama_num_batch=4096)
        monkeypatch.setattr('app.services.embedder.get_settings',lambda:configured)
        assert Embedder().embed('full unchanged input')==[1.0]*8
        assert requests[0][1]=={'model':'bge-m3','input':['full unchanged input'],'truncate':False}
        assert requests[1][1]=={'model':'bge-m3','input':['full unchanged input'],'truncate':False,'options':{'num_batch':4096}}


@pytest.mark.parametrize('batch',[0,-1,True,4096.5,32769])
def test_ollama_physical_batch_validation_happens_before_network(batch):
    from app.services.ollama_embeddings import embed_ollama
    with pytest.raises(ValueError):
        embed_ollama(api_base='http://127.0.0.1:1',model='ollama/bge-m3',inputs=['unchanged'],timeout_seconds=1,dimensions=8,num_batch=batch)


@pytest.mark.parametrize('mode,expected',[('physical_limit',1),('drip',0)])
def test_actual_native_error_completion_only_after_received_http_response(monkeypatch,mode,expected):
    from app.services import embedder
    from table_acceptance_pipeline_capture import instrument_transport_calls, completed_ollama_http_error
    completed=[]
    with provider(mode) as (url,_,_):
        monkeypatch.setattr('app.services.embedder.get_settings',lambda:settings(url))
        with instrument_transport_calls([(embedder,['embed_ollama'])],lambda:completed.append(1),
                                        completed_error=completed_ollama_http_error):
            with pytest.raises(EmbedderError):Embedder().embed('unchanged full input')
    assert len(completed)==expected
