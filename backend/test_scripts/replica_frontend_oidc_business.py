"""Frontend business checks over one admitted Gateway, two slots and its Service.

The native caller must independently prove each slot's actual frontend UID and
the Gateway process/route/config ownership. The fixed header is a test-route
selector, never a client-selected destination or authentication credential.
"""
from __future__ import annotations

from copy import copy
from http.cookiejar import CookieJar
import json
import re
import time

from test_scripts.replica_frontend_http import FrontendHTTP
from test_scripts.replica_frontend_oidc_http import OIDCProbe, MAX_BYTES, OwnedHTTPSUnavailable

SLOT_HEADER = 'X-Okf-Probe-Slot'


class GatewayFrontendHTTP(FrontendHTTP):
    """Reuse document/SHA/search/ZIP algorithms with an explicit TLS transport."""
    def __init__(self, probe):
        if not isinstance(probe, OIDCProbe): raise ValueError('Bounded owned OIDC transport required')
        self.probe, self.files = probe, probe.files
        self.username, self.execution_id = probe.username, probe.execution_id

    def _headers(self, index, method='GET', headers=None):
        if type(index) is not int or index not in {0, 1, 2}: raise ValueError('Unknown admitted Gateway route')
        headers = headers or {}
        forbidden = {'cookie', 'host', SLOT_HEADER.lower(), 'x-csrf-token', 'authorization', 'proxy-authorization'}
        if any(key.lower() in forbidden for key in headers): raise ValueError('Caller cannot override Gateway controls')
        result = dict(headers)
        if index != 2: result[SLOT_HEADER] = ('first', 'second')[index]
        if method == 'POST': result['X-CSRF-Token'] = self.probe._cookie('csrf_token').value
        return result

    def request(self, index, path, *, data=None, headers=None, plain=False):
        if plain or not isinstance(path, str) or not re.fullmatch(r'/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]*', path):
            raise ValueError('Only fixed application paths over TLS are accepted')
        method = 'POST' if data is not None else 'GET'
        headers = self._headers(index, method, headers)
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        status, raw, _ = self.probe._request(method, self.probe.origin + path, body=data, headers=headers)
        return status, raw

    def session(self, password):
        self.files.once('session.intent.json', {'execution_id': self.execution_id})
        self.probe.reject_unknown_issuer()
        self.probe.login(password)
        self.probe.csrf_guards()
        self.shared_session()
        for i in range(3):
            status, raw = self.request(i, '/')
            if status != 200 or b'<html' not in raw.lower(): raise ValueError('Frontend HTML unavailable through Gateway')
            for path in ('/internal/diagnostics/control', '/api/internal/diagnostics/control'):
                if self.request(i, path)[0] != 404: raise ValueError('Gateway frontend exposes internal API')
        value = dict(shared_routes=3, internal_routes_hidden=True, csrf='verified-real-sso',
            gateway_route_kind='two-slots-and-service', untrusted_tls_rejected=True)
        self.files.once('session.result.json', value)
        return value

    def _consume(self, index, progress=None, *, interrupted=False):
        start = time.monotonic()
        state = dict(terminal=False, sources_seen=False, result_seen=False, error_seen=False, event_count=0,
            protocol_error=False, transport_interrupted=False)
        first, size, delta = None, 0, False
        body = json.dumps(dict(query='Aquila frontend continuity', mode='bm25', use_glossary=False)).encode()
        try:
            with self.probe.open_response('POST', self.probe.origin + '/api/chat/stream', body=body,
                    headers=self._headers(index, 'POST', {'Content-Type': 'application/json'})) as response:
                if (response.status != 200 or not response.headers.get('Content-Type', '').startswith('application/x-ndjson')
                        or response.headers.get('Cache-Control') != 'no-store' or response.headers.get('X-Accel-Buffering') != 'no'):
                    raise ValueError('Gateway NDJSON headers differ')
                while True:
                    line = response.readline(MAX_BYTES + 1)
                    if not line: break
                    size += len(line)
                    if size > MAX_BYTES or state['event_count'] >= 512 or time.monotonic() - start > 45:
                        raise ValueError('Gateway NDJSON bound exceeded')
                    if not line.strip(): continue
                    kind = json.loads(line)['type']
                    if kind not in {'sources', 'ping', 'delta', 'result', 'error'}: raise ValueError('Unknown Gateway NDJSON event')
                    if first is None: first = time.monotonic() - start
                    state['event_count'] += 1
                    state['sources_seen'] |= kind == 'sources'
                    state['result_seen'] |= kind == 'result'
                    state['error_seen'] |= kind == 'error'
                    delta |= kind == 'delta'
                    if progress: progress(dict(state))
                    if state['error_seen']: raise ValueError('Gateway returned an application stream error')
        except Exception as exc:
            if not interrupted: raise
            state['transport_interrupted'] = isinstance(exc, OwnedHTTPSUnavailable)
            state['protocol_error'] = not state['transport_interrupted']
        state['terminal'] = True
        if progress: progress(dict(state))
        if interrupted: return state
        elapsed = time.monotonic() - start
        if not state['sources_seen'] or not state['result_seen'] or not delta or state['error_seen']:
            raise ValueError('Gateway NDJSON result incomplete')
        if first is None or first + 2 >= elapsed: raise ValueError('Gateway buffered NDJSON until result')
        return dict(first_event_seconds=round(first, 3), total_seconds=round(elapsed, 3), event_count=state['event_count'])

    def read_stream(self, index): return self._consume(index)

    def loss_stream(self, progress):
        self.files.once('oidc.loss-http.intent.json', {'execution_id': self.execution_id})
        return self._consume(0, progress, interrupted=True)

    def logout(self):
        self.files.once('logout.intent.json', {'execution_id': self.execution_id})
        old = CookieJar(); old.set_cookie(copy(self.probe._cookie('session')))
        self.probe.logout()
        document = self.files.read('upload.result.json')['document_id']
        for i in range(3):
            status, raw, _ = self.probe._request('GET', self.probe.origin + '/api/auth/me',
                headers=self._headers(i), jar=old)
            value = json.loads(raw)
            if status != 200 or value.get('mode') != 'sso' or value.get('user', {}).get('roles'):
                raise ValueError('Old Gateway session remained authenticated')
            status, _, _ = self.probe._request('GET', self.probe.origin + '/api/documents/' + document + '/download',
                headers=self._headers(i), jar=old)
            if status != 401: raise ValueError('Old Gateway session retained document access')
        value = dict(old_session_revoked_routes=3, csrf='verified-real-sso', idp_credentials_required=True,
            gateway_route_kind='two-slots-and-service')
        self.files.once('logout.result.json', value)
        return value
