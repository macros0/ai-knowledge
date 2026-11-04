"""Real TLS wire fixtures; these do not certify an actual IdP or Gateway."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import importlib.util
import json
from io import BytesIO
import ssl
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlsplit
from zipfile import ZipFile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

APP = 'https://knowledge.okf-oidc-test.svc.cluster.local'
IDP = 'https://identity.okf-oidc-test.svc.cluster.local'
SECRET = 'synthetic-password-only'


def module():
    name = 'test_scripts.replica_frontend_oidc_http'
    assert importlib.util.find_spec(name) is not None, 'Bounded real-OIDC HTTP component missing'
    return importlib.import_module(name)


def certificate(directory, hosts):
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic OIDC TLS')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
          .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
          .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), False).sign(key, hashes.SHA256()))
    cert = directory/'ca.pem';cert.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    private = directory/'tls.key';private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert, private


@pytest.fixture
def gateway(tmp_path):
    cert, private = certificate(tmp_path, [urlsplit(APP).hostname, urlsplit(IDP).hostname])
    state = dict(mode='normal', session=False, idp=False, calls=[], credential_posts=0, callbacks=0, logout_posts=0,
        business_calls=[], raw=None, stream_posts=0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_GET(self): self.handle_request()

        def do_POST(self): self.handle_request()

        def handle_request(self):
            host = self.headers['Host'];path = urlsplit(self.path).path
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            state['calls'].append((host, self.command, path))
            status, value, headers = 200, {}, []
            cookie = self.headers.get('Cookie', '')
            signed_in = state['session'] and 'session=private-session' in cookie
            app = host == urlsplit(APP).hostname
            business = path.startswith(('/api/documents/', '/api/jobs/')) or path in ['/api/documents','/api/search','/api/chat/stream']
            if app and business:
                state['business_calls'].append(dict(path=path,slot=self.headers.get('X-Okf-Probe-Slot'),
                    csrf=self.headers.get('X-CSRF-Token'),signed_in=signed_in))
            if app and business and not signed_in:status=401
            elif app and business and self.command=='POST' and self.headers.get('X-CSRF-Token')!='private-csrf':
                status=403;value={'code':'csrf_failed'}
            elif app and path == '/api/documents' and self.command=='POST':
                state['raw']=body.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n--',1)[0];value={'id':'a'*16}
            elif app and path == '/api/documents/bulk-export':value={'id':7}
            elif app and path == '/api/documents/'+'a'*16:value={'id':'a'*16,'status':'done','problem':None}
            elif app and path == '/api/documents/'+'a'*16+'/download':value=state['raw']
            elif app and path == '/api/search':value={'hits':[{'doc_id':'a'*16}]}
            elif app and path == '/api/jobs/7':value={'status':'completed','result':{'parts':[{'number':1}]}}
            elif app and path == '/api/jobs/7/export/1':
                out=BytesIO()
                with ZipFile(out,'w') as archive:archive.writestr('fixture.docx',state['raw'])
                value=out.getvalue()
            elif app and path == '/api/chat/stream':
                import time
                state['stream_posts']+=1
                self.send_response(200)
                self.send_header('Content-Type','application/x-ndjson');self.send_header('Cache-Control','no-store')
                self.send_header('X-Accel-Buffering','no');self.end_headers()
                if state['mode']=='buffered':time.sleep(2.1)
                self.wfile.write(b'{"type":"sources"}\n');self.wfile.flush()
                if state['mode']=='interrupted':self.close_connection=True;return
                if state['mode'] in ['malformed-stream','unknown-event']:
                    self.wfile.write(b'not-json\n' if state['mode']=='malformed-stream' else b'{"type":"foreign"}\n')
                    self.wfile.flush();return
                if state['mode']!='buffered':time.sleep(2.1)
                self.wfile.write(b'{"type":"delta"}\n{"type":"result"}\n');self.wfile.flush();return
            elif app and path == '/api/auth/me':
                value = {'mode': 'sso', 'user': {'username': 'lab-admin' if signed_in else 'anonymous',
                    'roles': ['admin'] if signed_in else []}}
                if state['mode']=='simulation':value['mode']='simulation'
                headers.append(('Set-Cookie','csrf_token=private-csrf; Path=/; Secure; SameSite=Lax'))
            elif app and path == '/api/documents': status = 200 if signed_in else 401
            elif app and path == '/api/auth/login':
                params = dict(client_id='knowledge',redirect_uri=APP+'/api/auth/callback',response_type='code',
                    scope='openid profile email',state='fixed-state',nonce='fixed-nonce')
                if state['mode']=='wrong-client':params['client_id']='foreign'
                if state['mode']=='wrong-redirect':params['redirect_uri']='https://customer.invalid/callback'
                if state['mode']=='duplicate-state':params['state']=['fixed-state','other']
                status=302;headers.append(('Location',IDP+'/realms/okf-home/protocol/openid-connect/auth?'+urlencode(params,doseq=True)))
                if state['mode']=='foreign-auth':headers[-1]=('Location','https://customer.invalid/login')
            elif not app and path == '/realms/okf-home/protocol/openid-connect/auth':
                action=IDP+'/realms/okf-home/login-actions/authenticate?session_code=fixture&execution=flow&client_id=knowledge&tab_id=tab'
                if state['mode']=='foreign-form':action='https://customer.invalid/credentials'
                if state['mode']=='wrong-form-client':action=action.replace('client_id=knowledge','client_id=foreign')
                value=f'<form id="kc-form-login" method="post" action="{action}"><input type="hidden" name="credentialId" value=""></form>'
                if state['mode']=='duplicate-form':value+=value
                if state['mode']=='oversized':value='x'*(1024*1024+1)
            elif not app and path == '/realms/okf-home/login-actions/authenticate':
                state['credential_posts']+=1;params=parse_qs(body.decode())
                assert params['username']==['lab-admin'] and params['password']==[SECRET]
                params=dict(code='private-code',state='fixed-state',iss=IDP+'/realms/okf-home')
                if state['mode']=='wrong-state':params['state']='other'
                if state['mode']=='wrong-issuer':params['iss']='https://customer.invalid/realm'
                status=302;headers.append(('Location',APP+'/api/auth/callback?'+urlencode(params)))
                if state['mode']=='foreign-callback':headers[-1]=('Location','https://customer.invalid/callback?code=private-code')
            elif app and path == '/api/auth/callback':
                state['callbacks']+=1;state['session']=state['idp']=True;status=303;headers.append(('Location','/'))
                flags='; Path=/; Secure; HttpOnly; SameSite=Lax'
                if state['mode']=='insecure-cookie':flags='; Path=/; HttpOnly; SameSite=Lax'
                headers.append(('Set-Cookie','session=private-session'+flags))
                if state['mode']=='lost-callback-ack':self.close_connection=True;return
            elif app and path == '/api/auth/logout':
                state['logout_posts']+=1
                token=self.headers.get('X-CSRF-Token')
                if token!='private-csrf':
                    status=403;value={'code':'csrf_failed'}
                    if state['mode']=='unrelated-403':value={'code':'insufficient_role'}
                    if state['mode']=='csrf-bypass':status=200;state['session']=False
                else:
                    state['session']=False
                    value={'redirect_url':IDP+'/realms/okf-home/protocol/openid-connect/logout?'+urlencode(dict(id_token_hint='private-id-token',post_logout_redirect_uri=APP+'/'))}
                    if state['mode']=='foreign-logout':value={'redirect_url':'https://customer.invalid/logout'}
                    if state['mode']=='lost-logout-ack':self.close_connection=True;return
            elif not app and path == '/realms/okf-home/protocol/openid-connect/logout':
                state['idp']=False;status=302;headers.append(('Location',APP+'/'))
            elif app and path == '/':value='<html>owned frontend</html>'
            else:status=404
            raw=value if isinstance(value,bytes) else value.encode() if isinstance(value,str) else json.dumps(value).encode()
            self.send_response(status)
            for key,value in headers:self.send_header(key,value)
            self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,private)
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    yield dict(port=server.server_port,ca=cert,state=state)
    server.shutdown();server.server_close();thread.join(timeout=3)


def client(tmp_path,gateway):
    directory=tmp_path/'private';directory.mkdir(mode=0o700,exist_ok=True)
    (directory/'oidc-ca.pem').write_bytes(gateway['ca'].read_bytes());(directory/'oidc-ca.pem').chmod(0o600)
    return module().OIDCProbe(directory,APP,IDP,gateway['port'],'lab-admin','admin','e'*32)


def test_real_tls_flow_checks_csrf_logout_revocation_and_private_receipts(tmp_path,gateway):
    c=client(tmp_path,gateway)
    assert c.reject_unknown_issuer()=={'untrusted_tls_rejected':True}
    assert not gateway['state']['calls']
    value=c.login(SECRET)
    assert value=={'mode':'sso','role':'admin','secure_session':True,'callback_verified':True}
    assert c.csrf_guards()=={'missing_csrf_denied':True,'wrong_csrf_denied':True,'authenticated_session_preserved':True}
    assert c.logout()=={'application_session_revoked':True,'idp_credentials_required':True}
    assert gateway['state']['credential_posts']==gateway['state']['callbacks']==1
    assert gateway['state']['logout_posts']==3
    for receipt in [value,c.files.read('oidc.csrf.result.json'),c.files.read('oidc.logout.result.json')]:
        assert not any(token in json.dumps(receipt) for token in [SECRET,'private-session','private-code','private-id-token','private-csrf'])
    saved=(tmp_path/'private'/'oidc.cookies.json').read_text();assert 'private-session' in saved


@pytest.mark.parametrize('bad',['foreign-auth','wrong-client','wrong-redirect','duplicate-state','foreign-form','wrong-form-client','duplicate-form','oversized'])
def test_invalid_login_destinations_never_receive_credentials(tmp_path,gateway,bad):
    gateway['state']['mode']=bad;c=client(tmp_path,gateway)
    with pytest.raises(ValueError):c.login(SECRET)
    assert gateway['state']['credential_posts']==0
    assert {host for host,_,_ in gateway['state']['calls']}<={urlsplit(APP).hostname,urlsplit(IDP).hostname}


@pytest.mark.parametrize('bad',['wrong-state','wrong-issuer','foreign-callback'])
def test_invalid_callback_is_refused_before_code_redemption(tmp_path,gateway,bad):
    gateway['state']['mode']=bad;c=client(tmp_path,gateway)
    with pytest.raises(ValueError):c.login(SECRET)
    assert gateway['state']['credential_posts']==1 and gateway['state']['callbacks']==0


@pytest.mark.parametrize('bad',['insecure-cookie','simulation'])
def test_simulation_or_insecure_cookie_cannot_be_a_real_oidc_pass(tmp_path,gateway,bad):
    gateway['state']['mode']=bad;c=client(tmp_path,gateway)
    with pytest.raises(ValueError):c.login(SECRET)
    assert not c.files.path('oidc.login.result.json').exists()


@pytest.mark.parametrize('bad',['unrelated-403','csrf-bypass'])
def test_csrf_requires_its_exact_denial_and_live_session(tmp_path,gateway,bad):
    c=client(tmp_path,gateway);c.login(SECRET);gateway['state']['mode']=bad
    with pytest.raises(ValueError):c.csrf_guards()
    assert not c.files.path('oidc.csrf.result.json').exists()


def test_lost_callback_ack_and_reopen_never_resubmit_credentials(tmp_path,gateway):
    c=client(tmp_path,gateway);gateway['state']['mode']='lost-callback-ack'
    with pytest.raises(ValueError):c.login(SECRET)
    c=client(tmp_path,gateway)
    with pytest.raises(ValueError):c.login(SECRET)
    assert gateway['state']['credential_posts']==gateway['state']['callbacks']==1


def test_lost_logout_ack_cannot_replay_even_after_reopen(tmp_path,gateway):
    c=client(tmp_path,gateway);c.login(SECRET);gateway['state']['mode']='lost-logout-ack'
    with pytest.raises(ValueError):c.logout()
    c=client(tmp_path,gateway)
    with pytest.raises(ValueError):c.logout()
    assert gateway['state']['logout_posts']==1


def test_terminal_receipt_reopens_without_network_replay(tmp_path,gateway):
    c=client(tmp_path,gateway);proof=c.login(SECRET);before=len(gateway['state']['calls'])
    c=client(tmp_path,gateway);assert c.files.read('oidc.login.result.json')==proof
    with pytest.raises(ValueError):c.login(SECRET)
    assert len(gateway['state']['calls'])==before


def test_foreign_logout_is_not_followed(tmp_path,gateway):
    c=client(tmp_path,gateway);c.login(SECRET);gateway['state']['mode']='foreign-logout'
    with pytest.raises(ValueError):c.logout()
    assert gateway['state']['idp']


@pytest.mark.parametrize('origin,identity,port',[(APP.replace('https:','http:'),IDP,1234),(APP,'https://customer.invalid',1234),
    (APP,IDP.replace('okf-oidc-test','okf-other'),1234),(APP,IDP,0),(APP+'/',IDP,1234)])
def test_nonowned_scope_is_refused_before_tls_or_cookie_files(tmp_path,origin,identity,port):
    before=set(tmp_path.iterdir())  # Shared conftest has already created its isolated test.db.
    with pytest.raises(ValueError):module().OIDCProbe(tmp_path,origin,identity,port,'lab-admin','admin','e'*32)
    assert set(tmp_path.iterdir())==before


def test_wrong_tls_hostname_and_ambient_proxy_cannot_redirect_request(tmp_path,gateway,monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY','http://customer.invalid:80')
    directory=tmp_path/'private';directory.mkdir(mode=0o700)
    (directory/'oidc-ca.pem').write_bytes(gateway['ca'].read_bytes());(directory/'oidc-ca.pem').chmod(0o600)
    c=module().OIDCProbe(directory,APP.replace('oidc-test','other'),IDP.replace('oidc-test','other'),gateway['port'],'lab-admin','admin','e'*32)
    with pytest.raises(ValueError):c.login(SECRET)
    assert not gateway['state']['calls']
