"""Real authorization-code HTTPS probe for the isolated synthetic home realm.

Run inside the unchanged backend image with independent probe CA with /probe/probe.json and /probe/ca.crt
from the synthetic-probe Secret. No tokens, cookies or passwords are logged.
"""
import json
import os
import re
import ssl
import time
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs
from zipfile import ZIP_STORED, ZipFile



def upload_fixture(size_bytes, marker='SyntheticFixture'):
    from docx import Document
    document = Document()
    document.add_heading('Synthetic HTTPS fixture', level=1)
    document.add_paragraph(('Synthetic Kubernetes knowledge fixture. ' + marker + ' ') * 30)
    original = BytesIO(); document.save(original)
    raw = original.getvalue()
    empty = BytesIO(raw)
    with ZipFile(empty, 'a', compression=ZIP_STORED) as package:
        package.writestr('synthetic-padding.bin', b'')
    padding = size_bytes - len(empty.getvalue())
    if padding < 0:
        raise ValueError('Fixture size is smaller than the DOCX headers')
    output = BytesIO(raw)
    with ZipFile(output, 'a', compression=ZIP_STORED) as package:
        package.writestr('synthetic-padding.bin', os.urandom(padding))
    result = output.getvalue()
    assert len(result) == size_bytes
    return result


def run_cluster_probe():
    import argparse
    import subprocess
    import uuid

    import yaml

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--delay-seconds", type=int, choices=range(31), default=0)
    parser.add_argument('--upload-limit-mib', type=int, choices=range(1, 1001), default=2)
    parser.add_argument('--marker', default='SyntheticFixture')
    parser.add_argument('--absent-marker', default='')
    parser.add_argument('--client-type',choices=['confidential','public'])
    parser.add_argument('--token-auth-method',choices=['client_secret_basic','client_secret_post','none'])
    parser.add_argument('--pkce-method',choices=['none','S256'])
    parser.add_argument('--userinfo-source',choices=['auto','endpoint'])
    args = parser.parse_args()
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{0,128}', value) for value in (args.marker, args.absent_marker)):
        parser.error('Only synthetic marker tokens are accepted')
    if not re.fullmatch(r"kind-[a-z0-9-]+", args.context) or not re.fullmatch(r"okf-[a-z0-9-]+", args.namespace):
        parser.error("Only explicit kind contexts and okf-* lab namespaces are supported")
    base = ["kubectl", "--context", args.context, "--namespace", args.namespace]
    def read(resource, name):
        return json.loads(subprocess.run(base + ["get", resource, name, "-o", "json"],
                                        check=True, capture_output=True, text=True).stdout)
    config = read("configmap", "runtime")["data"]
    if not all(config.get(k) == v for k, v in {"DEPLOYMENT_TIER": "home", "MODEL_MODE": "stub",
                                              "AUTH_PROVIDER": "keycloak_oidc", "KEYCLOAK_REALM": "okf-home"}.items()):
        parser.error("Probe requires the synthetic home OIDC profile")
    for attr,key in [("client_type","KEYCLOAK_CLIENT_TYPE"),("token_auth_method","KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD"),("pkce_method","KEYCLOAK_PKCE_METHOD"),("userinfo_source","KEYCLOAK_USERINFO_SOURCE")]:
        expected=getattr(args,attr)
        if expected is not None and config.get(key)!=expected:parser.error('Probe profile does not match runtime configuration')
    deployment = read("deployment", "application")
    image = next(c["image"] for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "backend")
    name = "home-access-probe-" + uuid.uuid4().hex[:8]
    script = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name},
              "data": {"home_probe.py": Path(__file__).read_text()}}
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name}, "spec": {
        "backoffLimit": 0, "activeDeadlineSeconds": 420, "ttlSecondsAfterFinished": 3600,
        "template": {"metadata": {"labels": {"okf-home-probe": "true"}}, "spec": {
            "restartPolicy": "Never", "automountServiceAccountToken": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{"name": "probe", "image": image, "command": ["python", "/script/home_probe.py"],
                "env": [{"name": "HOME_PROBE_DELAY_SECONDS", "value": str(args.delay_seconds)},
                        {"name": "HOME_PROBE_UPLOAD_MIB", "value": str(args.upload_limit_mib)},
                        {"name": "HOME_PROBE_MARKER", "value": args.marker},
                        {"name": "HOME_PROBE_ABSENT_MARKER", "value": args.absent_marker}],
                "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}},
                "volumeMounts": [{"name": "script", "mountPath": "/script", "readOnly": True},
                                 {"name": "credentials", "mountPath": "/probe", "readOnly": True}]}],
            "volumes": [{"name": "script", "configMap": {"name": name}},
                        {"name": "credentials", "secret": {"secretName": "synthetic-probe"}}],
        }}}}
    for document in (script, job):
        subprocess.run(base + ["create", "-f", "-"], input=yaml.safe_dump(document).encode(), check=True)
    deadline = time.monotonic() + 450
    while time.monotonic() < deadline:
        status = read("job", name).get("status", {})
        if status.get("succeeded") or status.get("failed"):
            subprocess.run(base + ["logs", "job/" + name], check=True)
            if status.get("failed"):
                raise RuntimeError("Home access probe Job failed; retained for diagnosis")
            subprocess.run(base + ["delete", "job", name], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(base + ["delete", "configmap", name], check=True, stdout=subprocess.DEVNULL)
            return
        time.sleep(3)
    raise RuntimeError("Home access probe deadline exceeded; resources retained")


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None
        self.inputs = {}
        self.active = False

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "form":
            self.active = attributes.get("id") == "kc-form-login"
            if self.active:
                self.action = attributes.get("action")
        if self.active and tag == "input" and attributes.get("type") == "hidden" and attributes.get("name"):
            self.inputs[attributes["name"]] = attributes.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False


def validate_login_action(identity_origin, action):
    action = urljoin(identity_origin, action or "")
    actual, expected = urlsplit(action), urlsplit(identity_origin)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc) or not actual.path.startswith(
        "/realms/okf-home/login-actions/authenticate"
    ):
        raise ValueError("Unexpected identity login form destination")
    return action


def assert_authorization_profile(response, pkce_method):
    urls=[r.headers.get("location","") for r in [*response.history,response]]
    parameters=[parse_qs(urlsplit(url).query) for url in urls]
    if pkce_method=="S256":
        assert any(p.get("code_challenge_method")==["S256"] and len(p.get("code_challenge",[""])[0])==43 for p in parameters), "S256 authorization challenge missing"
    else:
        assert not any("code_challenge" in p for p in parameters), "Unexpected PKCE profile"


def secure_session_cookie(cookie):
    return cookie.secure and any(key.lower() == "httponly" for key in cookie._rest)


def certificate_verification_failure(error):
    pending, seen = [error], set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return False


def verify_idp_logout(client, destination, origin, identity):
    response = client.get(destination, follow_redirects=True)
    assert response.status_code == 200 and str(response.url) == origin + "/", "IdP logout did not return to the application"
    login = client.get("/api/auth/login", follow_redirects=True)
    assert login.status_code == 200, "Post-logout login unavailable"
    form = LoginForm()
    form.feed(login.text)
    assert form.action, "IdP session remained: login did not require credentials"
    validate_login_action(identity, form.action)
    assert client.get("/api/documents").status_code == 401, "Session remained after logout"


def main():
    import certifi
    import httpx
    from docx import Document

    configuration = json.loads(Path("/probe/probe.json").read_text())
    origin = configuration["origin"]
    if not re.fullmatch(r"https://knowledge\.okf-[a-z0-9-]+\.svc\.cluster\.local", origin):
        raise ValueError("Probe accepts only the isolated cluster-local lab origin")
    identity = origin.replace("https://knowledge.", "https://identity.")
    trust = ssl.create_default_context(cafile="/probe/ca.crt")
    clients = []
    try:
        # A valid endpoint must be rejected without the synthetic CA as well.
        # Use independent public roots even if an image or runtime bundle
        # already trusts the lab CA; never inherit that trust for this control.
        with httpx.Client(verify=ssl.create_default_context(cafile=certifi.where()), trust_env=False, timeout=15) as untrusted:
            try:
                untrusted.get(origin + "/api/auth/me")
            except httpx.ConnectError as error:
                assert certificate_verification_failure(error), "TLS negative control failed before certificate verification"
            else:
                raise AssertionError("TLS accepted an untrusted lab issuer")
        anonymous = httpx.Client(base_url=origin, verify=trust, trust_env=False, timeout=90)
        clients.append(anonymous)
        assert anonymous.get("/api/auth/me").status_code == 200, "Trusted HTTPS endpoint failed"
        assert anonymous.get("/api/documents").status_code == 401, "Anonymous corpus access allowed"
        redirect = anonymous.get(origin.replace("https://", "http://") + "/")
        assert redirect.status_code == 301 and redirect.headers["location"].startswith(origin), "HTTP redirect failed"
        print("PASS HTTPS trust and negative trust, HTTP redirect, anonymous access denial", flush=True)

        sessions = {}
        for role, password in configuration["passwords"].items():
            client = httpx.Client(base_url=origin, verify=trust, trust_env=False, timeout=90)
            clients.append(client)
            login = client.get("/api/auth/login", follow_redirects=True)
            assert login.status_code == 200, "OIDC login page unavailable"
            assert_authorization_profile(login,configuration.get("pkce_method","none"))
            form = LoginForm()
            form.feed(login.text)
            action = validate_login_action(identity, form.action)
            callback = client.post(action, data={**form.inputs, "username": "lab-" + role, "password": password}, follow_redirects=True)
            if role == "unmapped":
                assert callback.status_code == 403, "Unmapped identity was admitted"
                assert client.get("/api/documents").status_code == 401, "Unmapped identity acquired a session"
                continue
            assert callback.status_code == 200, "OIDC callback failed"
            user = client.get("/api/auth/me").json()["user"]
            assert user["username"] == "lab-" + role and user["roles"] == [role], "Full-path role mapping failed"
            cookie = next(c for c in client.cookies.jar if c.name == "session")
            assert secure_session_cookie(cookie), "Session cookie flags failed"
            assert client.post("/api/auth/logout").status_code == 403, "Missing CSRF was accepted"
            assert client.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403, "Wrong CSRF was accepted"
            client.headers["X-CSRF-Token"] = client.cookies.get("csrf_token")
            sessions[role] = client
        print("PASS OIDC code exchange, four roles, unmapped denial, secure cookies and CSRF", flush=True)
        print("PASS authorization PKCE profile " + configuration.get("pkce_method","none"),flush=True)
        if configuration.get("userinfo_source")=="endpoint":
            print("PASS roles from UserInfo-only registration",flush=True)

        editor = sessions["editor"]
        marker = os.environ.get('HOME_PROBE_MARKER', 'SyntheticFixture')
        absent = os.environ.get('HOME_PROBE_ABSENT_MARKER', '')
        if absent:
            isolated = editor.post('/api/search', json={'query': absent, 'mode': 'bm25', 'use_glossary': False})
            assert isolated.status_code == 200 and not isolated.json()['hits'], 'Foreign profile marker became visible'
            print('PASS independent profile marker isolation', flush=True)
        limit = int(os.environ.get('HOME_PROBE_UPLOAD_MIB', '2'))
        body = upload_fixture(limit * 1024 * 1024, marker)
        content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        upload = {'file': ('home-https.docx', body, content_type)}
        denial = {'file': ('viewer-denial.docx', upload_fixture(2 * 1024 * 1024), content_type)}
        assert sessions['viewer'].post('/api/documents', files=denial).status_code == 403, 'Viewer upload allowed'
        created = editor.post("/api/documents", files=upload, data={"allow_similar": "true"})
        assert created.status_code == 200, "HTTPS multipart upload failed"
        identifier = created.json()["id"]
        deadline = time.monotonic() + 180
        while True:
            state = editor.get("/api/documents/" + identifier).json()
            if state.get("status") == "done":
                assert not state.get("problem") and state.get("okf_concept_count", 0) > 0, "Fixture generation incomplete"
                break
            assert state.get("status") not in {"failed", "paused", "error"} and time.monotonic() < deadline, "Fixture generation failed"
            time.sleep(2)
        if limit == 100:
            before = editor.get('/api/documents').json()
            oversized = editor.post('/api/documents', files={'file': ('oversized.docx', upload_fixture(limit * 1024 * 1024 + 1, marker), content_type)}, data={'allow_similar': 'true'})
            assert oversized.status_code in (400, 413, 422), 'Oversized file was accepted or transport failed'
            assert editor.get('/api/documents').json() == before, 'Rejected upload left partial document state'
            print('PASS exact 100 MiB file plus multipart and oversized rejection without partial state', flush=True)
        query = {"query": "Synthetic Kubernetes knowledge", "mode": "bm25", "use_glossary": False}
        search = sessions["viewer"].post("/api/search", json=query)
        assert search.status_code == 200 and search.json()["hits"], "Authenticated BM25 failed"
        assert editor.get("/api/documents/" + identifier + "/sources").json()["sources"], "Source inventory empty"
        stream_started = time.monotonic()
        with editor.stream("POST", "/api/chat/stream", json=query) as response:
            assert response.status_code == 200, "HTTPS chat stream rejected"
            events = [json.loads(line) for line in response.iter_lines() if line.strip()]
        assert any(e["type"] == "result" for e in events) and not any(e["type"] == "error" for e in events), "HTTPS stream incomplete"
        print("PASS HTTPS 2 MiB upload, viewer write denial, generation, sources, BM25 and chat stream", flush=True)
        delay = int(os.environ.get("HOME_PROBE_DELAY_SECONDS", "0"))
        if delay:
            assert time.monotonic() - stream_started >= delay - 1, "Delayed model fixture did not delay the stream"
            assert any(e["type"] == "ping" for e in events), "Delayed HTTPS stream had no keepalive"
            with editor.stream("POST", "/api/chat/stream", json={**query, "response_mode": "fast"}) as response:
                assert response.status_code == 200, "Cancellation stream failed"
                lines = response.iter_lines()
                start = next(json.loads(line) for line in lines
                             if line.strip() and json.loads(line).get("type") == "start")
                next(json.loads(line) for line in lines if line.strip() and json.loads(line).get("type") == "sources")
                time.sleep(2)
                cancel = editor.post("/api/chat/attempts/" + start["attempt_id"] + "/cancel",
                                     json={"session_id": start["session_id"]})
                assert cancel.status_code == 200 and cancel.json()["status"] == "stopped", (
                    f"HTTPS cancellation failed: HTTP {cancel.status_code}, status={cancel.json().get('status')}, code={cancel.json().get('code')}"
                )
            # After the synthetic delay window, stopped history must remain stopped.
            time.sleep(delay + 2)
            history = editor.get("/api/chat/history/" + start["session_id"])
            assert history.status_code == 200, "Stopped history disappeared"
            message = next(m for m in history.json()["messages"] if
                           (m.get("retrieval_metadata") or {}).get("answer_attempt", {}).get("id") == start["attempt_id"])
            assert message["content"] == "" and message["retrieval_metadata"]["answer_attempt"]["status"] == "stopped", "Late model result overwrote cancellation"
            print("PASS delayed HTTPS stream with keepalive, cancellation and stopped history after delay window", flush=True)
        for client in sessions.values():
            logout = client.post("/api/auth/logout")
            assert logout.status_code == 200, "Valid-CSRF logout failed"
            destination = logout.json()["redirect_url"]
            parsed = urlsplit(destination)
            assert parsed.netloc == urlsplit(identity).netloc and parsed.path.endswith("/protocol/openid-connect/logout"), "RP logout destination incorrect"
            verify_idp_logout(client, destination, origin, identity)
        print("PASS RP-initiated IdP logout and application session removal", flush=True)
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    try:
        import sys
        run_cluster_probe() if len(sys.argv) > 1 else main()
    except Exception as error:  # noqa: BLE001 -- suppress OAuth URL/credential leakage at the executable boundary
        # HTTP client exceptions can contain OAuth codes in URLs; don't print them.
        print("FAIL home access probe: " + (str(error) if isinstance(error, AssertionError) else type(error).__name__), flush=True)
        raise SystemExit(1) from None
