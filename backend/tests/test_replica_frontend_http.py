"""Real loopback HTTP proves one-shot mutations and cross-frontend sessions."""
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import importlib
import json
from pathlib import Path
from threading import Thread
import time
from zipfile import ZipFile

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / 'test_scripts/replica_frontend_http.py'
    assert path.is_file(), 'Native frontend HTTP client is missing'
    return importlib.import_module('test_scripts.replica_frontend_http')


@pytest.fixture
def servers():
    state = dict(calls=[], session=False, raw=None, done=True, job_done=True, buffered=False)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self): self.respond()
        def do_POST(self): self.respond()

        def respond(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state['calls'].append((self.command, self.path, raw))
            path = self.path
            code, value, headers = 200, {}, {}
            authenticated = state['session'] and self.headers.get('Cookie') == 'session=PRIVATE_COOKIE_CANARY'
            if path == '/': value = b'<html>Owned frontend</html>'
            elif path == '/redirect':
                code, headers['Location'] = 302, 'http://127.0.0.1:1/foreign'
            elif path == '/health/ready': value = {'status': 'ready'}
            elif path == '/api/auth/simulate':
                state['session'] = True
                headers['Set-Cookie'] = 'session=PRIVATE_COOKIE_CANARY; Path=/; HttpOnly'
                value = {'user': {'username': 'replica.admin'}}
            elif path == '/api/auth/me':
                value = {'user': {'username': 'replica.admin' if authenticated else 'anonymous', 'roles': []}}
            elif path == '/api/auth/logout': state['session'] = False
            elif path in {'/internal/diagnostics/control', '/api/internal/diagnostics/control'}:
                code = 401 if self.server.role == 2 else 404
            elif path == '/api/documents':
                boundary = self.headers['Content-Type'].split('boundary=')[1]
                state['raw'] = raw.split(b'\r\n\r\n', 1)[1].rsplit(('\r\n--' + boundary).encode(), 1)[0]
                value = {'id': 'a' * 16}
            elif path == '/api/documents/' + 'a' * 16:
                value = {'id': 'a' * 16, 'status': state.get('document_status') or ('done' if state['done'] else 'processing'), 'problem': None}
            elif path.endswith('/download'):
                code, value = (200, state['raw']) if authenticated else (401, {})
            elif path == '/api/search': value = {'hits': [{'doc_id': 'a' * 16}]}
            elif path == '/api/documents/bulk-export': value = {'id': 7}
            elif path == '/api/jobs/7':
                value = {'status': 'completed' if state['job_done'] else 'running', 'result': {'parts': [{'number': 1}]}}
            elif path == '/api/jobs/7/export/1':
                out = BytesIO()
                with ZipFile(out, 'w') as archive: archive.writestr('fixture.docx', state['raw'])
                value = out.getvalue()
            elif path == '/api/chat/stream':
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Accel-Buffering', 'no')
                self.end_headers()
                if state['buffered']: time.sleep(2.1)
                self.wfile.write(b'{"type":"sources"}\n'); self.wfile.flush()
                if not state['buffered']: time.sleep(2.1)
                self.wfile.write(b'{"type":"delta"}\n{"type":"result"}\n'); self.wfile.flush()
                return
            else: code = 404
            if not isinstance(value, bytes): value = json.dumps(value).encode()
            self.send_response(code)
            for name, value_header in headers.items(): self.send_header(name, value_header)
            self.send_header('Content-Length', str(len(value)))
            self.end_headers(); self.wfile.write(value)
    opened, threads = [], []
    for role in range(3):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.role = role
        thread = Thread(target=server.serve_forever, daemon=True); thread.start()
        opened.append(server); threads.append(thread)
    yield [f'http://127.0.0.1:{s.server_port}' for s in opened], state
    for server in opened: server.shutdown(); server.server_close()
    for thread in threads: thread.join(timeout=3)


def client(tmp_path, servers):
    urls, state = servers
    return module().FrontendHTTP(tmp_path, urls, 'replica.admin', 'f' * 32), state


def test_real_http_session_upload_publication_export_and_logout(tmp_path, servers):
    current, state = client(tmp_path, servers)
    assert current.session()['shared_routes'] == 3
    assert 'PRIVATE_COOKIE_CANARY' not in json.dumps(current.session_receipt())
    uploaded = current.upload()
    assert uploaded['document_id'] == 'a' * 16
    assert uploaded['original_sha256'] == sha256(state['raw']).hexdigest()
    with ZipFile(BytesIO(state['raw'])) as archive:
        assert 'f' * 32 in archive.read('word/document.xml').decode()
    assert current.publish()['status'] == 'passed'
    assert current.export()['job_id'] == 7
    assert current.export_observe()['status'] == 'passed'
    logged_out = current.logout()
    assert logged_out['old_session_revoked_routes'] == 3
    posts = [path for method, path, _ in state['calls'] if method == 'POST']
    assert posts.count('/api/auth/simulate') == posts.count('/api/documents') == 1
    assert posts.count('/api/documents/bulk-export') == posts.count('/api/auth/logout') == 1
    for action in (current.session, current.upload, current.export, current.logout):
        with pytest.raises(ValueError, match='intent'): action()
    assert len([1 for method, _, _ in state['calls'] if method == 'POST']) == len(posts)


def test_readonly_pending_observers_do_not_submit_more_business_actions(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session(); current.upload()
    state['done'] = False
    assert current.publish()['status'] == 'pending'
    state['done'] = True
    assert current.publish()['status'] == 'passed'
    current.export(); state['job_done'] = False
    assert current.export_observe()['status'] == 'pending'
    state['job_done'] = True
    assert current.export_observe()['status'] == 'passed'
    assert sum(path == '/api/documents' for _, path, _ in state['calls']) == 1
    assert sum(path == '/api/documents/bulk-export' for _, path, _ in state['calls']) == 1


def test_legitimate_splitting_publication_is_pending_not_a_failed_mutation(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session(); current.upload()
    state['document_status'] = 'splitting'
    assert current.publish()['status'] == 'pending'


def test_streams_observe_first_frame_before_delayed_result_on_both_frontends(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session()
    result = current.stream()
    assert result['unbuffered_frontends'] == 2
    assert all(row['first_event_seconds'] + 2 < row['total_seconds'] for row in result['streams'])
    assert 'PRIVATE_COOKIE_CANARY' not in json.dumps(result)
    with pytest.raises(ValueError): current.stream()
    assert sum(path == '/api/chat/stream' for _, path, _ in state['calls']) == 2


def test_buffered_stream_is_refused_and_not_replayed(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session(); state['buffered'] = True
    with pytest.raises(ValueError, match='buffer'): current.stream()
    with pytest.raises(ValueError, match='intent'): current.stream()
    assert sum(path == '/api/chat/stream' for _, path, _ in state['calls']) == 1


def test_lost_upload_ack_preserves_intent_without_resubmission(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session()
    original = current.request
    def lost(index, path, **kwargs):
        value = original(index, path, **kwargs)
        if path == '/api/documents': raise TimeoutError('PRIVATE_COOKIE_CANARY')
        return value
    current.request = lost
    with pytest.raises(TimeoutError): current.upload()
    current = module().FrontendHTTP(tmp_path, servers[0], 'replica.admin', 'f' * 32)
    with pytest.raises(ValueError, match='intent'): current.upload()
    assert sum(path == '/api/documents' for _, path, _ in state['calls']) == 1


def test_accepted_upload_is_not_a_terminal_receipt_before_cross_route_visibility(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session()
    original = current.json
    def disconnected(index, path, **kwargs):
        if path == '/api/documents/' + 'a' * 16: raise TimeoutError('lost-read-only-observation')
        return original(index, path, **kwargs)
    current.json = disconnected
    with pytest.raises(TimeoutError): current.upload()
    assert not (tmp_path / 'upload.result.json').exists()
    assert json.loads((tmp_path / 'upload.accepted.json').read_bytes())['document_id'] == 'a' * 16
    with pytest.raises(ValueError): current.upload()
    assert sum(path == '/api/documents' for _, path, _ in state['calls']) == 1


def test_server_redirect_never_changes_owned_route_or_sends_cookie_to_another_target(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session()
    before = len(state['calls'])
    with pytest.raises(ValueError, match='redirect'): current.request(0, '/redirect')
    assert len(state['calls']) == before + 1


@pytest.mark.parametrize('urls', [
    ['http://example.com:80'] * 3, ['http://127.0.0.1:50/path'] * 3,
    ['http://user:secret@127.0.0.1:50'] * 3, ['http://127.0.0.1:50'] * 2,
    ['http://127.0.0.1:50'] * 3])
def test_only_three_distinct_owned_loopback_routes_are_accepted(tmp_path, urls):
    with pytest.raises(ValueError): module().FrontendHTTP(tmp_path, urls, 'replica.admin', 'f' * 32)


def test_existing_aliased_cookie_file_refused_before_http(tmp_path, servers):
    current, state = client(tmp_path, servers)
    current.session()
    (tmp_path / 'alias').hardlink_to(tmp_path / 'cookies.json')
    before = len(state['calls'])
    with pytest.raises(ValueError): module().FrontendHTTP(tmp_path, servers[0], 'replica.admin', 'f' * 32)
    assert len(state['calls']) == before
