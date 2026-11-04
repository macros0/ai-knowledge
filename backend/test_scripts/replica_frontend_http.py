"""Bounded HTTP actions for a native owned-lab coordinator (stdlib only).

The coordinator must first bind the three exact-Pod loopback forwards. This
module owns cookies and HTTP intents in its private directory; it is not a CLI,
a Kubernetes admission gate, or a signed qualification producer.
"""
from __future__ import annotations

from hashlib import sha256
from http.cookiejar import Cookie, CookieJar
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

MAX_BYTES = 8 * 1024 * 1024


class PrivateFiles:
    """Append-only native receipts; incomplete writes conservatively block use."""
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or self.directory.is_symlink()
                or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
                or (os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
            raise ValueError('Existing private native directory required')

    def path(self, name):
        if not re.fullmatch('[a-z0-9][a-z0-9.-]{0,80}', name): raise ValueError('Invalid receipt name')
        path = self.directory / name
        try: info = path.lstat()
        except FileNotFoundError: return path
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
                or (os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
            raise ValueError('Native receipt is linked, aliased or not private')
        return path

    def sync(self):
        if os.name != 'nt':
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)

    def once(self, name, value):
        path = self.path(name)
        try: fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError: raise ValueError('Existing native intent or receipt; observe instead') from None
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        self.sync()

    def read(self, name):
        path = self.path(name)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            before, current = os.fstat(stream.fileno()), path.lstat()
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino)):
                raise ValueError('Native receipt changed')
            raw = stream.read(1024 * 1024 + 1)
        self.path(name)
        if len(raw) > 1024 * 1024: raise ValueError('Native receipt exceeds its bound')
        return json.loads(raw)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Owned HTTP redirect refused')


def synthetic_docx(execution_id):
    body = BytesIO()
    entries = {
        '[Content_Types].xml': '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        '_rels/.rels': '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        'word/document.xml': '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Aquila frontend continuity. Synthetic replica acceptance execution ' + execution_id + '. The Vela calibration ledger records a signal window and operational verification checklist for an isolated training deployment. Shared sessions, durable original downloads and document search must survive replacement of a frontend process.</w:t></w:r></w:p><w:sectPr/></w:body></w:document>',
    }
    with ZipFile(body, 'w', compression=ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            entry = ZipInfo(name, (2026, 11, 4, 0, 0, 0)); entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, value)
    return body.getvalue()


class FrontendHTTP:
    def __init__(self, directory, routes, username, execution_id):
        if (not isinstance(routes, list) or len(routes) != 3 or len(set(routes)) != 3
                or not isinstance(username, str) or not re.fullmatch('[a-zA-Z0-9_.-]{1,64}', username)
                or not isinstance(execution_id, str) or not re.fullmatch('[0-9a-f]{32}', execution_id)):
            raise ValueError('Explicit owned HTTP identity required')
        for route in routes:
            url = urlsplit(route)
            if (url.scheme != 'http' or url.hostname != '127.0.0.1' or not url.port or url.path
                    or url.username or url.password or url.query or url.fragment):
                raise ValueError('Owned loopback routes required')
        self.files = PrivateFiles(directory)
        self.routes, self.username, self.execution_id = routes, username, execution_id
        binding = {'execution_id': execution_id, 'username': username}
        if self.files.path('http-binding.json').exists():
            if self.files.read('http-binding.json') != binding: raise ValueError('HTTP execution binding differs')
        else: self.files.once('http-binding.json', binding)
        self.jar = CookieJar()
        if self.files.path('cookies.json').exists():
            for value in self.files.read('cookies.json'): self.jar.set_cookie(Cookie(**value))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                                 urllib.request.HTTPCookieProcessor(self.jar))
        self.plain = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, index, path, *, data=None, headers=None, plain=False):
        if isinstance(data, dict):
            data = json.dumps(data).encode(); headers = {**(headers or {}), 'Content-Type': 'application/json'}
        request = urllib.request.Request(self.routes[index] + path, data=data, headers=headers or {})
        try:
            with (self.plain if plain else self.opener).open(request, timeout=35) as response:
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES: raise ValueError('Owned HTTP response exceeds its bound')
                return response.status, raw
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 404}: return exc.code, b''
            raise ValueError('Owned HTTP request refused') from None

    def json(self, index, path, **kwargs):
        status, raw = self.request(index, path, **kwargs)
        if status not in {200, 202}: raise ValueError('Owned HTTP JSON request refused')
        return json.loads(raw)

    def session(self):
        self.files.once('session.intent.json', {'execution_id': self.execution_id})
        if self.json(2, '/health/ready')['status'] != 'ready' or self.request(0, '/')[0] != 200:
            raise ValueError('Owned application is not ready')
        if self.json(0, '/api/auth/simulate', data={'username': self.username})['user']['username'] != self.username:
            raise ValueError('Simulation login differs')
        serialized = []
        for c in self.jar:
            serialized.append({**{key: getattr(c, key) for key in ('version', 'name', 'value', 'port', 'port_specified',
                'domain', 'domain_specified', 'domain_initial_dot', 'path', 'path_specified', 'secure', 'expires',
                'discard', 'comment', 'comment_url', 'rfc2109')}, 'rest': c._rest})
        if not serialized: raise ValueError('No native session cookie')
        self.files.once('cookies.json', serialized)
        self.shared_session()
        for i in (0, 1):
            for path in ('/internal/diagnostics/control', '/api/internal/diagnostics/control'):
                if self.request(i, path)[0] != 404: raise ValueError('Frontend exposed internal route')
        if self.request(2, '/internal/diagnostics/control')[0] != 401:
            raise ValueError('Collector accepted missing credential')
        value = {'shared_routes': 3, 'internal_routes_hidden': True, 'csrf': 'simulation-exempt'}
        self.files.once('session.result.json', value)
        return value

    def shared_session(self):
        for i in range(3):
            if self.json(i, '/api/auth/me')['user']['username'] != self.username:
                raise ValueError('Session did not survive across routes')

    def session_receipt(self):
        return self.files.read('session.result.json')

    def upload(self):
        raw = synthetic_docx(self.execution_id)
        expected = sha256(raw).hexdigest()
        self.files.once('upload.intent.json', {'original_sha256': expected})
        boundary = 'okf' + self.execution_id
        body = ('--' + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="frontend-' +
                self.execution_id + '.docx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n').encode()
        body += raw + ('\r\n--' + boundary + '--\r\n').encode()
        doc = self.json(0, '/api/documents', data=body,
                        headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        if not re.fullmatch('[0-9a-f]{16,32}', doc.get('id', '')): raise ValueError('Invalid uploaded document identity')
        value = {'document_id': doc['id'], 'original_sha256': expected, 'legacy_idempotency_claimed': False}
        self.files.once('upload.accepted.json', value)
        if self.json(1, '/api/documents/' + doc['id'])['id'] != doc['id']:
            raise ValueError('Upload is not visible through second frontend')
        self.files.once('upload.result.json', value)
        return value

    def publication(self):
        upload = self.files.read('upload.result.json')
        doc = self.json(1, '/api/documents/' + upload['document_id'])
        if doc['status'] != 'done':
            if doc['status'] not in {'uploaded', 'queued', 'parsing', 'processing', 'splitting', 'indexing', 'converting'}:
                raise ValueError('Document publication failed')
            return None
        if doc.get('problem') is not None: raise ValueError('Incomplete document publication')
        for i in range(3):
            status, raw = self.request(i, '/api/documents/' + upload['document_id'] + '/download')
            if status != 200 or sha256(raw).hexdigest() != upload['original_sha256']:
                raise ValueError('Original content differs')
            hits = self.json(i, '/api/search', data={'query': 'Aquila frontend continuity', 'mode': 'bm25', 'top_k': 40})['hits']
            if upload['document_id'] not in {v['doc_id'] for v in hits}: raise ValueError('Search omitted uploaded document')
        return upload

    def publish(self):
        upload = self.publication()
        return {'status': 'passed', 'verified_routes': 3} if upload else {'status': 'pending'}

    def export(self):
        upload = self.files.read('upload.result.json')
        self.files.once('export.intent.json', {'document_id': upload['document_id']})
        job = self.json(1, '/api/documents/bulk-export', data={'doc_ids': [upload['document_id']]})
        if type(job.get('id')) is not int or job['id'] <= 0: raise ValueError('Invalid export job identity')
        value = {'job_id': job['id']}
        self.files.once('export.result.json', value)
        return value

    def export_observe(self):
        job_id = self.files.read('export.result.json')['job_id']
        job = self.json(0, '/api/jobs/' + str(job_id))
        if job['status'] != 'completed':
            if job['status'] not in {'pending', 'queued', 'running'}: raise ValueError('Export job failed')
            return {'status': 'pending'}
        parts = job['result']['parts']
        if len(parts) != 1 or type(parts[0]['number']) is not int or parts[0]['number'] <= 0:
            raise ValueError('Expected one bounded ZIP part')
        expected = self.files.read('upload.result.json')['original_sha256']
        hashes = []
        for i in range(3):
            status, raw = self.request(i, '/api/jobs/' + str(job_id) + '/export/' + str(parts[0]['number']))
            if status != 200: raise ValueError('ZIP download failed')
            with ZipFile(BytesIO(raw)) as archive:
                docs = [v for v in archive.infolist() if v.filename.endswith('.docx')]
                if len(docs) != 1 or docs[0].file_size > MAX_BYTES or sha256(archive.read(docs[0])).hexdigest() != expected:
                    raise ValueError('ZIP original differs')
            hashes.append(sha256(raw).hexdigest())
        if len(set(hashes)) != 1: raise ValueError('ZIP bytes differ across routes')
        return {'status': 'passed', 'verified_routes': 3, 'export_sha256': hashes[0]}

    def read_stream(self, index):
        started = time.monotonic()
        request = urllib.request.Request(self.routes[index] + '/api/chat/stream',
            data=json.dumps({'query': 'Aquila frontend continuity', 'mode': 'bm25', 'use_glossary': False}).encode(),
            headers={'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=40) as response:
            if (response.status != 200 or not response.headers['Content-Type'].startswith('application/x-ndjson')
                    or response.headers['Cache-Control'] != 'no-store' or response.headers['X-Accel-Buffering'] != 'no'):
                raise ValueError('NDJSON stream headers differ')
            events, first, size = [], None, 0
            while True:
                line = response.readline(1024 * 1024 + 1)
                if not line: break
                size += len(line)
                if size > 1024 * 1024 or len(events) >= 512: raise ValueError('NDJSON stream exceeds its bound')
                if not line.strip(): continue
                if first is None: first = time.monotonic() - started
                kind = json.loads(line)['type']
                if kind not in {'sources', 'ping', 'delta', 'result', 'error'}: raise ValueError('Unknown NDJSON event')
                events.append(kind)
        elapsed = time.monotonic() - started
        if 'result' not in events or 'delta' not in events or 'error' in events: raise ValueError('NDJSON result incomplete')
        if first is None or first + 2 >= elapsed: raise ValueError('NDJSON stream buffered until result')
        return {'first_event_seconds': round(first, 3), 'total_seconds': round(elapsed, 3), 'event_count': len(events)}

    def stream(self):
        self.files.once('stream.intent.json', {'execution_id': self.execution_id})
        value = {'unbuffered_frontends': 2, 'streams': [self.read_stream(i) for i in (0, 1)]}
        self.files.once('stream.result.json', value)
        return value

    def logout(self):
        self.files.once('logout.intent.json', {'execution_id': self.execution_id})
        old_cookie = '; '.join(c.name + '=' + c.value for c in self.jar)
        if not old_cookie: raise ValueError('No old session to revoke')
        self.json(1, '/api/auth/logout', data={})
        upload = self.files.read('upload.result.json')
        for i in range(3):
            value = self.json(i, '/api/auth/me', headers={'Cookie': old_cookie}, plain=True)
            if value['user'].get('username') != 'anonymous' or value['user'].get('roles') != []:
                raise ValueError('Old session was not revoked')
            if self.request(i, '/api/documents/' + upload['document_id'] + '/download',
                            headers={'Cookie': old_cookie}, plain=True)[0] != 401:
                raise ValueError('Old cookie retained protected access')
        value = {'old_session_revoked_routes': 3, 'csrf': 'simulation-exempt'}
        self.files.once('logout.result.json', value)
        return value
