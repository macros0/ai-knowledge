"""Bounded native OIDC wire actions, not a namespace or qualification gate.

Call only after owned fixture/forward admission. Cookies and business-action
intents stay in the private directory; there is no mutation retry or redirect
handler. Real provider/Gateway acceptance is separate from component tests.
"""
from __future__ import annotations

from copy import copy
from contextlib import contextmanager
from hashlib import sha256
from html.parser import HTMLParser
import http.client
from http.cookiejar import Cookie, CookieJar
import json
import re
import ssl
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import Request

from .replica_frontend_http import PrivateFiles

MAX_BYTES = 1024 * 1024


class OwnedHTTPSUnavailable(ValueError):
    """A connection/read failure, distinct from a malformed application stream."""


class _BoundedResponse:
    def __init__(self, response):
        self._response, self._size, self._started = response, 0, time.monotonic()
        self.status, self.headers = response.status, response.headers

    def _read(self, method, size):
        if time.monotonic() - self._started > 45: raise ValueError('Owned HTTPS body deadline exceeded')
        remaining = MAX_BYTES - self._size
        if remaining < 0: raise ValueError('Owned HTTPS response too large')
        amount = remaining + 1 if size is None or size < 0 else min(size, remaining + 1)
        try: data = getattr(self._response, method)(amount)
        except ssl.SSLCertVerificationError: raise
        except (OSError, http.client.HTTPException):
            raise OwnedHTTPSUnavailable('Owned HTTPS body unavailable; observe intent') from None
        self._size += len(data)
        if self._size > MAX_BYTES: raise ValueError('Owned HTTPS response too large')
        if time.monotonic() - self._started > 45: raise ValueError('Owned HTTPS body deadline exceeded')
        return data

    def read(self, size=-1): return self._read('read', size)

    def readline(self, size=-1): return self._read('readline', size)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, port, hostname, context):
        super().__init__('127.0.0.1', port, context=context, timeout=20)
        self.hostname = hostname

    def connect(self):
        http.client.HTTPConnection.connect(self)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.hostname)


class _LoginForm(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.action, self.inputs, self.active, self.forms = None, {}, False, 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if len(attributes) != len(attrs): raise ValueError('Ambiguous login attributes')
        if tag == 'form':
            if self.active: raise ValueError('Nested login form refused')
            self.active = attributes.get('id') == 'kc-form-login'
            if self.active:
                self.forms += 1
                if self.forms != 1 or attributes.get('method', '').lower() != 'post':
                    raise ValueError('One POST login form required')
                self.action = attributes.get('action')
        if tag == 'input' and self.active and attributes.get('type', '').lower() == 'hidden':
            name, value = attributes.get('name', ''), attributes.get('value', '')
            if (not re.fullmatch('[A-Za-z0-9_.-]{1,128}', name) or name.lower() in {'username', 'password'}
                    or name in self.inputs or len(value) > 2048 or len(self.inputs) >= 16):
                raise ValueError('Ambiguous login inputs')
            self.inputs[name] = value

    def handle_endtag(self, tag):
        if tag == 'form': self.active = False


class OIDCProbe:
    def __init__(self, directory, origin, identity, port, username, role, execution_id):
        match = re.fullmatch(r'https://knowledge\.(okf-[a-z0-9-]{1,40})\.svc\.cluster\.local', origin)
        if (not match or identity != origin.replace('https://knowledge.', 'https://identity.')
                or type(port) is not int or not 1 <= port <= 65535
                or not re.fullmatch('lab-[a-z0-9-]{1,32}', username)
                or role not in {'viewer', 'editor', 'admin', 'security'}
                or not re.fullmatch('[0-9a-f]{32}', execution_id)):
            raise ValueError('Explicit owned OIDC scope required')
        self.origin, self.identity, self.port = origin, identity, port
        self.username, self.role, self.execution_id = username, role, execution_id
        self.files = PrivateFiles(directory)
        ca = self.files.path('oidc-ca.pem')
        if not ca.is_file() or ca.stat().st_size > 65536: raise ValueError('Bounded private CA required')
        self.context = ssl.create_default_context(cafile=str(ca))
        binding = dict(origin=origin, identity=identity, port=port, username=username, role=role,
            execution_id=execution_id, ca_sha256=sha256(ca.read_bytes()).hexdigest())
        if self.files.path('oidc.binding.json').exists():
            if self.files.read('oidc.binding.json') != binding: raise ValueError('OIDC binding changed')
        else: self.files.once('oidc.binding.json', binding)
        self.jar = CookieJar()
        if self.files.path('oidc.cookies.json').exists():
            for value in self.files.read('oidc.cookies.json'):
                cookie = Cookie(**value)
                if cookie.domain.lstrip('.') not in {urlsplit(origin).hostname, urlsplit(identity).hostname}:
                    raise ValueError('Persisted cookie scope differs')
                self.jar.set_cookie(cookie)

    def _scope(self, url, origin, path):
        if not isinstance(url, str) or len(url) > 16384 or re.search(r'[\x00-\x20\x7f]', url):
            raise ValueError('Invalid owned transition')
        parsed, expected = urlsplit(url), urlsplit(origin)
        if (parsed.scheme != 'https' or parsed.netloc != expected.netloc or parsed.path != path
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError('Foreign OIDC transition refused')
        return parsed

    def _query(self, parsed, allowed, required):
        try: values = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True, max_num_fields=16)
        except ValueError: raise ValueError('Invalid OIDC parameters') from None
        if (set(values) - set(allowed) or not set(required) <= set(values)
                or any(len(v) != 1 or not v[0] for v in values.values())):
            raise ValueError('Ambiguous OIDC parameters')
        return {k: v[0] for k, v in values.items()}

    @contextmanager
    def open_response(self, method, url, body=None, headers=None, jar=None, context=None):
        """Yield the verified response before consuming its body; never follow redirects."""
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc not in {urlsplit(self.origin).netloc, urlsplit(self.identity).netloc}
                or parsed.fragment or parsed.username or parsed.password):
            raise ValueError('Only two pinned HTTPS origins allowed')
        jar = self.jar if jar is None else jar
        request = Request(url, data=body, method=method, headers=headers or {})
        jar.add_cookie_header(request)
        connection = _PinnedHTTPS(self.port, parsed.hostname, self.context if context is None else context)
        try:
            try:
                connection.request(method, parsed.path + ('?' + parsed.query if parsed.query else ''), body=body,
                    headers={**dict(request.header_items()), 'Host': parsed.netloc, 'Connection': 'close'})
                response = connection.getresponse()
                jar.extract_cookies(SimpleNamespace(info=lambda: response.headers), request)
            except ssl.SSLCertVerificationError: raise
            except (OSError, http.client.HTTPException):
                raise OwnedHTTPSUnavailable('Owned HTTPS transport unavailable; observe intent') from None
            # Consumer/receipt I/O errors must not be relabelled as network loss.
            yield _BoundedResponse(response)
        finally: connection.close()

    def _request(self, method, url, body=None, headers=None, jar=None, context=None):
        with self.open_response(method, url, body=body, headers=headers, jar=jar, context=context) as response:
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES: raise ValueError('Owned HTTPS response too large')
            return response.status, raw, response.headers.get('Location')

    def reject_unknown_issuer(self):
        # No ambient/default roots. This control cannot inherit the fixture CA.
        empty = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        try: self._request('GET', self.origin + '/api/auth/me', context=empty, jar=CookieJar())
        except ssl.SSLCertVerificationError: return {'untrusted_tls_rejected': True}
        raise ValueError('Unknown TLS issuer was not rejected')

    def _json(self, method, url, **kwargs):
        status, raw, _ = self._request(method, url, **kwargs)
        try: value = json.loads(raw)
        except (ValueError, UnicodeError): raise ValueError('Invalid owned JSON response') from None
        return status, value

    def _session(self):
        status, value = self._json('GET', self.origin + '/api/auth/me')
        if (status != 200 or value.get('mode') != 'sso' or value.get('user', {}).get('username') != self.username
                or value['user'].get('roles') != [self.role]):
            raise ValueError('Expected real OIDC identity absent')

    def _cookie(self, name):
        found = [c for c in self.jar if c.name == name and c.domain.lstrip('.') == urlsplit(self.origin).hostname
            and c.path == '/' and c.secure]
        if len(found) != 1: raise ValueError('Exact secure application cookie required')
        return found[0]

    def _authorization(self):
        status, _, location = self._request('GET', self.origin + '/api/auth/login')
        if status not in {302, 303}: raise ValueError('OIDC authorization redirect missing')
        path = '/realms/okf-home/protocol/openid-connect/auth'
        parsed = self._scope(location, self.identity, path)
        q = self._query(parsed, {'client_id', 'redirect_uri', 'response_type', 'scope', 'state', 'nonce'},
            {'client_id', 'redirect_uri', 'response_type', 'scope', 'state', 'nonce'})
        if (q['client_id'] != 'knowledge' or q['redirect_uri'] != self.origin + '/api/auth/callback'
                or q['response_type'] != 'code' or set(q['scope'].split()) != {'openid', 'profile', 'email'}):
            raise ValueError('Unsupported OIDC authorization profile')
        return location, q['state']

    def _form(self, url):
        status, raw, _ = self._request('GET', url)
        if status != 200: raise ValueError('Credential form missing')
        form = _LoginForm()
        try: form.feed(raw.decode('utf-8')); form.close()
        except UnicodeError: raise ValueError('Invalid credential form') from None
        if form.forms != 1 or not form.action: raise ValueError('One credential form required')
        action = urljoin(self.identity, form.action)
        parsed = self._scope(action, self.identity, '/realms/okf-home/login-actions/authenticate')
        q = self._query(parsed, {'session_code', 'execution', 'client_id', 'tab_id', 'client_data'}, {'client_id'})
        if q['client_id'] != 'knowledge': raise ValueError('Foreign credential client refused')
        return action, form.inputs

    def login(self, password):
        if not isinstance(password, str) or not 1 <= len(password) <= 512: raise ValueError('Synthetic credential required')
        self.files.once('oidc.login.intent.json', {'execution_id': self.execution_id})
        status, public = self._json('GET', self.origin + '/api/auth/me')
        if status != 200 or public.get('mode') != 'sso' or public.get('user', {}).get('roles'):
            raise ValueError('Fresh real OIDC session required')
        authorization, state = self._authorization()
        action, hidden = self._form(authorization)
        status, _, callback = self._request('POST', action,
            body=urlencode({**hidden, 'username': self.username, 'password': password}).encode(),
            headers={'Content-Type': 'application/x-www-form-urlencoded'})
        if status not in {302, 303}: raise ValueError('OIDC callback redirect missing')
        q = self._query(self._scope(callback, self.origin, '/api/auth/callback'),
            {'code', 'state', 'iss', 'session_state'}, {'code', 'state', 'iss'})
        if q['state'] != state or q['iss'] != self.identity + '/realms/okf-home':
            raise ValueError('OIDC callback state or issuer differs')
        status, _, location = self._request('GET', callback)
        if status != 303 or location != '/': raise ValueError('OIDC callback did not complete')
        self._session()
        session = self._cookie('session')
        if 'httponly' not in {k.lower() for k in session._rest}: raise ValueError('HttpOnly session required')
        serialized = [{**{k: getattr(c, k) for k in ('version', 'name', 'value', 'port', 'port_specified', 'domain',
            'domain_specified', 'domain_initial_dot', 'path', 'path_specified', 'secure', 'expires', 'discard',
            'comment', 'comment_url', 'rfc2109')}, 'rest': c._rest} for c in self.jar]
        self.files.once('oidc.cookies.json', serialized)
        value = dict(mode='sso', role=self.role, secure_session=True, callback_verified=True)
        self.files.once('oidc.login.result.json', value)
        return value

    def csrf_guards(self):
        self.files.read('oidc.login.result.json')
        self.files.once('oidc.csrf.intent.json', {'execution_id': self.execution_id})
        for headers in ({}, {'X-CSRF-Token': 'invalid-' + self.execution_id}):
            self._session()
            status, value = self._json('POST', self.origin + '/api/auth/logout', body=b'', headers=headers)
            if status != 403 or value.get('code') != 'csrf_failed': raise ValueError('Exact CSRF denial required')
            self._session()
        value = dict(missing_csrf_denied=True, wrong_csrf_denied=True, authenticated_session_preserved=True)
        self.files.once('oidc.csrf.result.json', value)
        return value

    def logout(self):
        self.files.read('oidc.login.result.json')
        self.files.once('oidc.logout.intent.json', {'execution_id': self.execution_id})
        self._session()
        old = CookieJar(); old.set_cookie(copy(self._cookie('session')))
        status, value = self._json('POST', self.origin + '/api/auth/logout', body=b'',
            headers={'X-CSRF-Token': self._cookie('csrf_token').value})
        if status != 200: raise ValueError('Correct CSRF logout refused')
        destination = value.get('redirect_url')
        q = self._query(self._scope(destination, self.identity, '/realms/okf-home/protocol/openid-connect/logout'),
            {'id_token_hint', 'post_logout_redirect_uri'}, {'id_token_hint', 'post_logout_redirect_uri'})
        if q['post_logout_redirect_uri'] != self.origin + '/': raise ValueError('Foreign post-logout destination')
        status, _, location = self._request('GET', destination)
        if status not in {302, 303} or location != self.origin + '/': raise ValueError('IdP logout return differs')
        if self._request('GET', location)[0] != 200: raise ValueError('Post-logout frontend unavailable')
        if self._request('GET', self.origin + '/api/documents', jar=old)[0] != 401:
            raise ValueError('Previous application session remained valid')
        authorization, _ = self._authorization()
        self._form(authorization)  # A fresh form, not an automatic SSO callback.
        if self._request('GET', self.origin + '/api/documents')[0] != 401:
            raise ValueError('A new authenticated session appeared after logout')
        value = dict(application_session_revoked=True, idp_credentials_required=True)
        self.files.once('oidc.logout.result.json', value)
        return value
