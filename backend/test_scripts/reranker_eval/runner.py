"""One persistent worker, bounded protocol, no backlog, actual process cancellation."""
import json
import os
from queue import Empty, Queue
import subprocess
import threading
from time import perf_counter
import uuid

from .ranking import reorder_prefix


class RerankCancelled(Exception):
    pass


class WorkerRunner:
    def __init__(self, command: list[str], *, startup_timeout: float = 90.):
        self.command, self.startup_timeout = command, startup_timeout
        self.process, self.metadata, self.ready = None, {}, False
        self.lock = threading.Lock()
        self.responses = Queue(maxsize=1)

    def _read(self):
        try:
            while True:
                line = self.process.stdout.readline(65537)
                if not line:
                    self.responses.put(('unavailable', None))
                    return
                if len(line) > 65536 or not line.endswith(b'\n'):
                    self.responses.put(('invalid_response', None))
                    return
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    self.responses.put(('invalid_response', None))
                    return
                self.responses.put(('response', value))
        except (OSError, ValueError):
            try:
                self.responses.put_nowait(('unavailable', None))
            except Exception:
                pass

    def start(self):
        if self.process is not None:
            raise RuntimeError('worker already started')
        started = perf_counter()
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            kind, result = self.responses.get(timeout=self.startup_timeout)
        except Empty:
            self.close()
            raise RuntimeError('worker startup timeout') from None
        if kind != 'response' or not isinstance(result, dict) or result.get('status') != 'ready':
            self.close()
            raise RuntimeError('worker unavailable')
        self.metadata = {**result, 'cold_start_ms': (perf_counter() - started) * 1000}
        self.ready = True
        return self

    def close(self):
        self.ready = False
        process = self.process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *args):
        self.close()

    def run_rerank(self, query: str, blocks: list[dict], *, top_n: int,
                   deadline_seconds: float = 3., cancel=None, forms=()) -> dict:
        started = perf_counter()
        if cancel and cancel.is_set():
            raise RerankCancelled()
        def fallback(status):
            return {'blocks': list(blocks), 'status': status, 'reason': status,
                    'elapsed_ms': (perf_counter() - started) * 1000, 'scored_count': 0,
                    'model_revision': self.metadata.get('revision')}
        if not self.lock.acquire(blocking=False):
            return fallback('busy')
        try:
            if cancel and cancel.is_set():
                raise RerankCancelled()
            if not self.ready or self.process.poll() is not None:
                return fallback('unavailable')
            if type(top_n) is not int or top_n < 1 or deadline_seconds <= 0:
                return fallback('unsupported_input')
            request_id = uuid.uuid4().hex
            prefix = []
            for i, block in enumerate(blocks[:top_n]):
                prefix.append({'index': i, 'title': block.get('title', ''),
                               'content': block.get('content', ''),
                               'mail_fragment': block.get('mail_fragment'), 'forms': list(forms)})
            payload = (json.dumps({'request_id': request_id, 'query': query, 'blocks': prefix,
                                   'top_n': top_n}, ensure_ascii=False) + '\n').encode()
            if len(payload) > 16 * 1024 * 1024:
                return fallback('unsupported_input')
            write_errors = []
            def write():
                try:
                    self.process.stdin.write(payload)
                    self.process.stdin.flush()
                except (OSError, ValueError):
                    write_errors.append(True)
            writer = threading.Thread(target=write, daemon=True)
            writer.start()
            while True:
                if cancel and cancel.is_set():
                    self.close()
                    writer.join(2)
                    raise RerankCancelled()
                left = deadline_seconds - (perf_counter() - started)
                if left <= 0:
                    self.close()
                    writer.join(2)
                    return fallback('timeout')
                if write_errors:
                    self.close()
                    return fallback('unavailable')
                try:
                    kind, response = self.responses.get(timeout=min(left, .03))
                    break
                except Empty:
                    continue
            writer.join(2)
            if kind != 'response':
                self.close()
                return fallback(kind)
            if not isinstance(response, dict) or response.get('request_id') != request_id:
                self.close()
                return fallback('invalid_response')
            status = response.get('status')
            if status != 'applied':
                self.close()
                return fallback(status if status in {'unsupported_input', 'unavailable'} else
                                'invalid_response')
            indexed = response.get('scores', [])
            try:
                if [v['index'] for v in indexed] != list(range(len(prefix))):
                    raise ValueError('invalid index list')
                ranked = reorder_prefix(blocks, [v['score'] for v in indexed], top_n=top_n)
            except (KeyError, TypeError, ValueError):
                self.close()
                return fallback('invalid_response')
            return {'blocks': ranked, 'status': 'applied', 'reason': None,
                    'elapsed_ms': (perf_counter() - started) * 1000,
                    'model_revision': self.metadata.get('revision'), 'scored_count': len(prefix),
                    'scores': indexed, 'window_counts': response.get('window_counts', []),
                    'peak_gpu_bytes': response.get('peak_gpu_bytes')}
        finally:
            self.lock.release()


def run_rerank(query: str, blocks: list[dict], *, top_n: int,
               deadline_seconds: float = 3., runner: WorkerRunner | None = None, **kwargs) -> dict:
    if kwargs.get('cancel') is not None and kwargs['cancel'].is_set():
        raise RerankCancelled()
    if runner is None:
        return {'blocks': list(blocks), 'status': 'unavailable', 'reason': 'unavailable',
                'elapsed_ms': 0., 'scored_count': 0, 'model_revision': None}
    return runner.run_rerank(query, blocks, top_n=top_n, deadline_seconds=deadline_seconds, **kwargs)
