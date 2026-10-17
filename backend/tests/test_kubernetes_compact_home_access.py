"""Synthetic SSO lab boundaries, without mutating a cluster."""
import importlib.util
import hashlib
from pathlib import Path
import ssl
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def lab():
    source = Path(__file__).resolve().parents[2] / "scripts/kubernetes/home_access.py"
    spec = importlib.util.spec_from_file_location("home_access", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("context,namespace,app", [
    ("production", "okf-access", "okf-sso"),
    ("kind-home", "default", "okf-sso"),
    ("kind-home", "okf-access", "okf-access"),
    ("kind-home", "okf-access", "prod"),
])
def test_lab_refuses_ambiguous_or_non_lab_targets(lab, context, namespace, app):
    with pytest.raises(ValueError):
        lab.validate_targets(context, namespace, app)


def test_realm_uses_code_flow_full_groups_and_exact_redirects(lab):
    realm = lab.realm_document("https://knowledge.example.test", "private", {"viewer": "private"})
    client = realm["clients"][0]
    assert client["standardFlowEnabled"] is True
    assert client["directAccessGrantsEnabled"] is False
    assert client["publicClient"] is False
    assert client["redirectUris"] == ["https://knowledge.example.test/api/auth/callback"]
    mapper = client["protocolMappers"][0]["config"]
    assert mapper["full.path"] == "true" and mapper["claim.name"] == "group"
    assert realm["users"][0]["groups"] == ["/lab/viewer"]


def test_gateway_delegation_is_scoped_and_secrets_not_in_values(lab):
    documents = lab.platform_documents("okf-access", "okf-sso", "quay.io/keycloak/keycloak@sha256:" + "a" * 64)
    gateway = next(d for d in documents if d["kind"] == "Gateway")
    assert gateway["spec"]["allowedListeners"]["namespaces"]["selector"]["matchLabels"] == {"okf-home-access": "true"}
    parameters = next(d for d in documents if d["kind"] == "GatewayParameters")
    assert parameters["spec"]["kube"]["service"]["type"] == "ClusterIP"
    deployment = next(d for d in documents if d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "--import-realm" in container["args"]
    assert deployment["spec"]["template"]["spec"]["automountServiceAccountToken"] is False
    assert any(v.get("secret", {}).get("secretName") == "synthetic-realm" for v in deployment["spec"]["template"]["spec"]["volumes"])
    values = lab.application_values("okf-access", "home-sso-test-ca")
    assert values["runtime"]["environment"] == "production"
    assert values["deployment"]["environment"] == "home"
    config = values["runtime"]["config"]
    assert config["AUTH_PROVIDER"] == "keycloak_oidc"
    assert config["KEYCLOAK_GROUP_PATH_MODE"] == "full_path"
    assert "KEYCLOAK_CLIENT_SECRET" not in config


def test_backend_image_identity_depends_on_public_ca(lab):
    assert lab.backend_tag(b"first CA") == "home-sso-" + hashlib.sha256(b"first CA").hexdigest()
    assert lab.backend_tag(b"first CA") != lab.backend_tag(b"second CA")
    assert lab.application_values("okf-access", lab.backend_tag(b"first CA"))["images"]["backend"]["tag"] == lab.backend_tag(b"first CA")


def test_private_artifacts_refuse_repository_paths(lab):
    with pytest.raises(ValueError):
        lab.validate_private_directory(lab.ROOT / "tests/tmp/home-access")
    lab.validate_private_directory(lab.ROOT.parent / "private")


@pytest.fixture
def probe():
    source = Path(__file__).resolve().parents[2] / "scripts/kubernetes/home_probe.py"
    spec = importlib.util.spec_from_file_location("home_probe", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_oidc_probe_does_not_post_credentials_to_other_origins(probe):
    parser = probe.LoginForm()
    parser.feed('<form id="kc-form-login" action="https://identity.okf-access.svc.cluster.local/realms/okf-home/login-actions/authenticate?session=x"><input type="hidden" name="credentialId" value=""></form>')
    expected = "https://identity.okf-access.svc.cluster.local"
    assert probe.validate_login_action(expected, parser.action).startswith(expected)
    with pytest.raises(ValueError):
        probe.validate_login_action(expected, "https://example.test/realms/okf-home/login-actions/authenticate")
    with pytest.raises(ValueError):
        probe.validate_login_action(expected, expected + "/admin/")


def test_cookie_flags_are_case_insensitive_and_both_required(probe):
    assert probe.secure_session_cookie(SimpleNamespace(secure=True, _rest={"httponly": None}))
    assert probe.secure_session_cookie(SimpleNamespace(secure=True, _rest={"HttpOnly": None}))
    assert not probe.secure_session_cookie(SimpleNamespace(secure=False, _rest={"HttpOnly": None}))
    assert not probe.secure_session_cookie(SimpleNamespace(secure=True, _rest={"samesite": "lax"}))


def test_negative_tls_requires_certificate_failure(probe):
    wrapped = RuntimeError("Connection failed")
    wrapped.__cause__ = ssl.SSLCertVerificationError("untrusted issuer")
    assert probe.certificate_verification_failure(wrapped)
    assert not probe.certificate_verification_failure(ConnectionRefusedError("refused"))
    assert not probe.certificate_verification_failure(RuntimeError("certificate verify failed"))


def test_logout_confirmation_page_is_not_completed_logout(probe):
    client = Mock()
    client.get.return_value = SimpleNamespace(status_code=200, url="https://identity.example.test/confirm-logout")
    with pytest.raises(AssertionError):
        probe.verify_idp_logout(client, "destination", "https://knowledge.example.test", "https://identity.example.test")


def test_automatic_relogin_is_not_completed_idp_logout(probe):
    client = Mock()
    client.get.side_effect = [SimpleNamespace(status_code=200, url="https://knowledge.example.test/"),
                              SimpleNamespace(status_code=200, text="Application page")]
    with pytest.raises(AssertionError):
        probe.verify_idp_logout(client, "destination", "https://knowledge.example.test", "https://identity.example.test")


def test_logout_returns_to_app_and_requires_credentials_again(probe):
    client = Mock()
    client.get.side_effect = [SimpleNamespace(status_code=200, url="https://knowledge.example.test/"),
        SimpleNamespace(status_code=200, text='<form id="kc-form-login" action="https://identity.example.test/realms/okf-home/login-actions/authenticate"></form>'),
        SimpleNamespace(status_code=401)]
    probe.verify_idp_logout(client, "destination", "https://knowledge.example.test", "https://identity.example.test")


def test_runtime_ca_profile_preserves_supplied_image_identity(lab):
    values=lab.application_values('okf-access','configuration-v2',trust_configmap='runtime-ca-a')
    assert values['trust']=={'existingConfigMap':'runtime-ca-a'}
    assert values['images']['backend']['tag']=='configuration-v2'
    assert lab.application_values('okf-access','legacy-tag')['images']['backend']['tag']=='legacy-tag'


def test_public_runtime_bundle_adds_image_roots_and_has_no_private_key(lab):
    root=(Path(__file__).parent/'fixtures/kubernetes/public-ca.pem').read_bytes()
    bundle=lab.runtime_bundle(root,root)
    assert bundle.count(b'BEGIN CERTIFICATE')==2
    with pytest.raises(ValueError):lab.runtime_bundle(root,b'-----BEGIN PRIVATE KEY-----')
    with pytest.raises(ValueError):lab.runtime_bundle(b'invalid',root)


def test_upload_fixture_hits_exact_file_boundary(probe):
    size=3*1024*1024
    body=probe.upload_fixture(size,'UniqueLabMarker')
    assert len(body)==size
    from zipfile import ZipFile
    from io import BytesIO
    with ZipFile(BytesIO(body)) as package:
        assert 'word/document.xml' in package.namelist()
        assert 'UniqueLabMarker' in package.read('word/document.xml').decode()


@pytest.mark.parametrize('tag,cm,message',[
    ('bad:tag','runtime-ca','Invalid explicit backend image tag'),
    ('-bad','runtime-ca','Invalid explicit backend image tag'),
    ('x'*129,'runtime-ca','Invalid explicit backend image tag'),
    ('valid','x'*254,'valid ConfigMap name')])
def test_runtime_arguments_rejected_before_external_commands(lab,tmp_path,monkeypatch,capsys,tag,cm,message):
    lab.ROOT=tmp_path/'repository'
    command=Mock(side_effect=AssertionError('External command reached'))
    monkeypatch.setattr(lab.subprocess,'run',command)
    monkeypatch.setattr(lab.sys,'argv',['home_access','--context','kind-lab','--namespace','okf-access',
        '--application-namespace','okf-app','--private-directory',str(tmp_path/'private'),
        '--keycloak-image','quay.io/keycloak/keycloak@sha256:'+'a'*64,
        '--runtime-trust-configmap',cm,'--backend-image-tag='+tag])
    with pytest.raises(SystemExit) as error:lab.main()
    assert error.value.code==2
    assert message in capsys.readouterr().err
    command.assert_not_called()
    assert not (tmp_path/'private').exists()


@pytest.mark.parametrize('options', [
    dict(client_type='public', token_auth_method='none', pkce_method='S256'),
    dict(token_auth_method='client_secret_post'),
    dict(pkce_method='S256'),
    dict(userinfo_source='endpoint'),
])
def test_current_main_unsupported_profiles_refused_before_commands(lab, options):
    with pytest.raises(ValueError, match='Current main supports only'):
        lab.application_values('okf-access', 'oidc-v1', **options)

@pytest.mark.parametrize('options', [dict(client_type='public'),dict(client_type='public',token_auth_method='none'),dict(client_type='confidential',token_auth_method='none'),dict(pkce_method='plain')])
def test_lab_mixed_profiles_refused_before_commands(lab,options):
    with pytest.raises(ValueError):lab.application_values('okf-access','oidc-v1',**options)


def test_probe_checks_actual_authorization_challenge(probe):
    good=SimpleNamespace(headers={'location':'https://idp.example/auth?code_challenge_method=S256&code_challenge='+'a'*43},history=[])
    probe.assert_authorization_profile(good,'S256')
    bad=SimpleNamespace(headers={'location':'https://idp.example/auth'},history=[])
    with pytest.raises(AssertionError):probe.assert_authorization_profile(bad,'S256')


def test_unsupported_public_cli_refused_before_resources(lab, tmp_path, monkeypatch, capsys):
    command = Mock(side_effect=AssertionError('External command reached'))
    monkeypatch.setattr(lab.subprocess, 'run', command)
    monkeypatch.setattr(lab.sys, 'argv', ['home_access', '--context', 'kind-lab',
        '--namespace', 'okf-access', '--application-namespace', 'okf-app',
        '--private-directory', str(tmp_path / 'private'),
        '--keycloak-image', 'quay.io/keycloak/keycloak@sha256:' + 'a' * 64,
        '--client-type', 'public', '--token-auth-method', 'none', '--pkce-method', 'S256'])
    with pytest.raises(SystemExit) as error:
        lab.main()
    assert error.value.code == 2
    assert 'Current main supports only' in capsys.readouterr().err
    command.assert_not_called()
    assert not (tmp_path / 'private').exists()
