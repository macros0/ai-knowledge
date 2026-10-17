"""Prepare a fresh kind-only HTTPS/OIDC lab. Requires Linux, openssl and PyYAML.

Install the pinned Gateway controller first (see docs/KUBERNETES_HOME_ACCESS.md).
Private CA keys and synthetic credentials stay outside the repository. Failure
leaves resources for diagnosis; this script never replaces existing namespaces.
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GROUP = "gateway.networking.k8s.io"
ROLES = ("viewer", "editor", "admin", "security", "unmapped")


def validate_targets(context, namespace, application_namespace):
    if not re.fullmatch(r"kind-[a-z0-9-]+", context):
        raise ValueError("Only explicit kind contexts are supported")
    if namespace == application_namespace or any(
        not re.fullmatch(r"okf-[a-z0-9-]+", n) or len(n) > 63
        for n in (namespace, application_namespace)
    ):
        raise ValueError("Two distinct okf-* lab namespaces are required")


def validate_private_directory(path):
    if Path(path).resolve().is_relative_to(ROOT):
        raise ValueError("Private artifacts must be outside the repository")


def validate_profile(client_type="confidential", token_auth_method="client_secret_basic", pkce_method="none", userinfo_source="auto"):
    if (client_type, token_auth_method, pkce_method, userinfo_source) != (
        "confidential", "client_secret_basic", "none", "auto"
    ):
        raise ValueError("Current main supports only confidential/client_secret_basic/no-PKCE/auto UserInfo")


def realm_document(origin, client_secret, passwords, *, client_type="confidential", token_auth_method="client_secret_basic", pkce_method="none", userinfo_source="auto"):
    validate_profile(client_type,token_auth_method,pkce_method,userinfo_source)
    result = {
        "realm": "okf-home", "enabled": True, "sslRequired": "external",
        "registrationAllowed": False,
        "groups": [{"name": "lab", "subGroups": [{"name": role} for role in ROLES if role != "unmapped"]}],
        "clients": [{
            "clientId": "knowledge", "secret": client_secret, "protocol": "openid-connect",
            "publicClient": client_type=="public", "standardFlowEnabled": True,
            "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
            "redirectUris": [origin + "/api/auth/callback"], "webOrigins": [origin],
            "attributes": {"post.logout.redirect.uris": origin + "/"},
            "protocolMappers": [{"name": "full-groups", "protocol": "openid-connect",
                "protocolMapper": "oidc-group-membership-mapper", "config": {
                    "claim.name": "group", "full.path": "true", "id.token.claim": "true",
                    "access.token.claim": "true", "userinfo.token.claim": "true"}}],
        }],
        "users": [{"username": "lab-" + role, "enabled": True, "emailVerified": True,
                   "email": role + "@example.test", "firstName": "Synthetic", "lastName": role,
                   "groups": [] if role == "unmapped" else ["/lab/" + role],
                   "credentials": [{"type": "password", "value": password, "temporary": False}]}
                  for role, password in passwords.items()],
    }
    client=result["clients"][0]
    if client_type=="public":client.pop("secret",None)
    if pkce_method=="S256":client["attributes"]["pkce.code.challenge.method"]="S256"
    if userinfo_source=="endpoint":
        mapper=client["protocolMappers"][0]["config"]
        mapper["id.token.claim"]="false";mapper["access.token.claim"]="false"
    return result


def backend_tag(public_ca):
    return "home-sso-" + hashlib.sha256(public_ca).hexdigest()


def application_values(namespace, backend_image_tag, *, trust_configmap=None, client_type="confidential", token_auth_method="client_secret_basic", pkce_method="none", userinfo_source="auto"):
    validate_profile(client_type,token_auth_method,pkce_method,userinfo_source)
    origin = f"https://knowledge.{namespace}.svc.cluster.local"
    values = {
        "deployment": {"environment": "home"},
        "runtime": {"environment": "production", "config": {
            "AUTH_PROVIDER": "keycloak_oidc", "AUTH_SESSION_HTTPS_ONLY": "true",
            "LLM_PROFILE": "local_qwen",
            "KEYCLOAK_URL": f"https://identity.{namespace}.svc.cluster.local",
            "KEYCLOAK_REALM": "okf-home", "KEYCLOAK_CLIENT_ID": "knowledge",
            "KEYCLOAK_GROUP_PATH_MODE": "full_path",
            "KEYCLOAK_CLIENT_TYPE": client_type, "KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD": token_auth_method,
            "KEYCLOAK_PKCE_METHOD": pkce_method, "KEYCLOAK_USERINFO_SOURCE": userinfo_source,
            "KEYCLOAK_EXPECTED_ISSUER": f"https://identity.{namespace}.svc.cluster.local/realms/okf-home",
            "OIDC_LOGIN_STATE_TTL_SECONDS": "600",
            "AUTH_ROLE_GROUPS": json.dumps({"/lab/" + r: r for r in ROLES if r != "unmapped"}),
            "SSO_REDIRECT_URI": origin + "/api/auth/callback", "SSO_POST_LOGOUT_REDIRECT_URI": origin + "/",
        }},
        "images": {"backend": {"tag": backend_image_tag}, "stub": {"tag": "kube-smoke"}},
        "gateway": {"enabled": True, "hostname": origin.removeprefix("https://"),
                    "namespace": namespace, "name": "home-gateway", "certificateSecret": "lab-tls"},
        # NetworkPolicy behavior depends on the installed CNI; run explicit negative probes.
        "networkPolicy": {"externalEgress": [{"to": [{"namespaceSelector": {
            "matchLabels": {"kubernetes.io/metadata.name": namespace}}}],
            "ports": [{"protocol": "TCP", "port": 443}]}]},
    }

    if trust_configmap:
        values['trust'] = {'existingConfigMap': trust_configmap}
    return values


def runtime_bundle(standard_roots, public_ca):
    import ssl
    if not isinstance(standard_roots, bytes) or not isinstance(public_ca, bytes):
        raise ValueError('Public bundle requires bytes')
    for body in (standard_roots, public_ca):
        if b'PRIVATE KEY' in body:
            raise ValueError('Private keys are forbidden in runtime trust')
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=body.decode('utf-8'))
        except (ValueError, ssl.SSLError) as exc:
            raise ValueError('Invalid public CA bundle') from exc
    return standard_roots.rstrip() + b'\n' + public_ca.rstrip() + b'\n'



def platform_documents(namespace, application_namespace, keycloak_image):
    identity = f"identity.{namespace}.svc.cluster.local"
    return [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace, "labels": {"okf-home-access": "true"}}},
        {"apiVersion": "gateway.kgateway.dev/v1alpha1", "kind": "GatewayParameters", "metadata": {"name": "home-gateway"},
         "spec": {"kube": {"service": {"type": "ClusterIP"}, "envoyContainer": {"resources": {
             "requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}}}}}},
        {"apiVersion": GROUP + "/v1", "kind": "Gateway", "metadata": {"name": "home-gateway"}, "spec": {
            "infrastructure": {"parametersRef": {"name": "home-gateway", "kind": "GatewayParameters", "group": "gateway.kgateway.dev"}},
            "gatewayClassName": "kgateway", "allowedListeners": {"namespaces": {
                "from": "Selector", "selector": {"matchLabels": {"okf-home-access": "true"}}}},
            "listeners": [{"name": "http", "port": 80, "protocol": "HTTP", "allowedRoutes": {
                "namespaces": {"from": "Selector", "selector": {"matchLabels": {"okf-home-access": "true"}}}}}]}},
        {"apiVersion": GROUP + "/v1", "kind": "ListenerSet", "metadata": {"name": "identity-listeners"}, "spec": {
            "parentRef": {"name": "home-gateway", "kind": "Gateway", "group": GROUP},
            "listeners": [{"name": "identity-https", "port": 443, "protocol": "HTTPS", "hostname": identity,
                "tls": {"mode": "Terminate", "certificateRefs": [{"name": "lab-tls"}]},
                "allowedRoutes": {"namespaces": {"from": "Same"}}}]}},
        {"apiVersion": GROUP + "/v1", "kind": "HTTPRoute", "metadata": {"name": "identity"}, "spec": {
            "parentRefs": [{"name": "identity-listeners", "kind": "ListenerSet", "group": GROUP}],
            "hostnames": [identity], "rules": [{"backendRefs": [{"name": "keycloak", "port": 8080}]}]}},
        *[{"apiVersion": "v1", "kind": "Service", "metadata": {"name": name}, "spec": {
            "type": "ExternalName", "externalName": f"home-gateway.{namespace}.svc.cluster.local"}}
          for name in ("identity", "knowledge")],
        {"apiVersion": "v1", "kind": "Service", "metadata": {"name": "keycloak"}, "spec": {
            "selector": {"app": "home-keycloak"}, "ports": [{"port": 8080, "targetPort": 8080}]}},
        {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "keycloak"}, "spec": {
            "replicas": 1, "selector": {"matchLabels": {"app": "home-keycloak"}}, "template": {
                "metadata": {"labels": {"app": "home-keycloak"}}, "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [{"name": "keycloak", "image": keycloak_image,
                        "args": ["start-dev", "--import-realm", "--hostname=https://" + identity,
                                 "--proxy-headers=xforwarded", "--health-enabled=true"],
                        "env": [{"name": "JAVA_OPTS_KC_HEAP", "value": "-Xms128m -Xmx384m"}],
                        "resources": {"requests": {"cpu": "250m", "memory": "512Mi"},
                                      "limits": {"cpu": "2", "memory": "1Gi"}},
                        "ports": [{"containerPort": 8080}],
                        "readinessProbe": {"httpGet": {"path": "/health/ready", "port": 9000}, "periodSeconds": 5},
                        "startupProbe": {"httpGet": {"path": "/health/ready", "port": 9000},
                                         "failureThreshold": 60, "periodSeconds": 5},
                        "volumeMounts": [{"name": "realm", "mountPath": "/opt/keycloak/data/import", "readOnly": True}]}],
                    "volumes": [{"name": "realm", "secret": {"secretName": "synthetic-realm"}}],
                }}}},
    ]


def issue_host_certificate(private, role, namespace, ca, ca_key, key):
    if role not in ('identity', 'knowledge') or not re.fullmatch(r'okf-[a-z0-9-]+', namespace):
        raise ValueError('Invalid synthetic TLS host')
    csr, cert, ext = [private / (role + suffix) for suffix in ('.csr', '.crt', '.ext')]
    host = role + '.' + namespace + '.svc.cluster.local'
    ext.write_text('basicConstraints=critical,CA:FALSE\nsubjectAltName=DNS:' + host + '\n')
    subprocess.run(['openssl', 'req', '-new', '-key', str(key), '-out', str(csr), '-subj', '/CN=' + host], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['openssl', 'x509', '-req', '-in', str(csr), '-CA', str(ca), '-CAkey', str(ca_key), '-CAcreateserial', '-out', str(cert), '-days', '7', '-extfile', str(ext)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cert


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--application-namespace", required=True)
    parser.add_argument("--private-directory", required=True, type=Path)
    parser.add_argument("--keycloak-image", required=True)
    parser.add_argument("--runtime-trust-configmap")
    parser.add_argument("--backend-image-tag")
    parser.add_argument("--client-type",choices=["confidential","public"],default="confidential")
    parser.add_argument("--token-auth-method",choices=["client_secret_basic","client_secret_post","none"],default="client_secret_basic")
    parser.add_argument("--pkce-method",choices=["none","S256"],default="none")
    parser.add_argument("--userinfo-source",choices=["auto","endpoint"],default="auto")
    args = parser.parse_args()
    profile=dict(client_type=args.client_type,token_auth_method=args.token_auth_method,pkce_method=args.pkce_method,userinfo_source=args.userinfo_source)
    try:validate_profile(**profile)
    except ValueError as error:parser.error(str(error))
    if args.backend_image_tag and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', args.backend_image_tag):
        parser.error('Invalid explicit backend image tag')
    if args.runtime_trust_configmap and (len(args.runtime_trust_configmap)>253 or not args.backend_image_tag or not re.fullmatch(r'[a-z0-9]([-a-z0-9.]*[a-z0-9])?', args.runtime_trust_configmap)):
        parser.error('Runtime trust requires an existing backend image tag and a valid ConfigMap name')
    validate_targets(args.context, args.namespace, args.application_namespace)
    validate_private_directory(args.private_directory)
    if os.name != "posix":
        parser.error("Run on the Linux lab host so private files have POSIX permissions")
    if not re.fullmatch(r"quay.io/keycloak/keycloak@sha256:[a-f0-9]{64}", args.keycloak_image):
        parser.error("An immutable official Keycloak image digest is required")
    base = ["kubectl", "--context", args.context]
    for name in (args.namespace, args.application_namespace):
        result = subprocess.run(base + ["get", "namespace", name, "--ignore-not-found", "-o", "name"],
                                check=True, capture_output=True, text=True)
        if result.stdout.strip():
            parser.error("Refusing to replace an existing namespace")
    subprocess.run(base + ["get", "gatewayclass", "kgateway"], check=True, stdout=subprocess.DEVNULL)
    args.private_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    private = args.private_directory
    ca, key, cert, ca_key = [private / n for n in ("ca.crt", "tls.key", "tls.crt", "ca.key")]
    def openssl(*command):
        subprocess.run(["openssl", *map(str, command)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    openssl("req", "-x509", "-newkey", "rsa:3072", "-nodes", "-keyout", ca_key, "-out", ca,
            "-days", "7", "-subj", "/CN=Synthetic Kubernetes Lab CA", "-addext", "basicConstraints=critical,CA:TRUE")
    openssl("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", private / "tls.csr",
            "-subj", "/CN=Synthetic Kubernetes Lab")
    certs = {role: issue_host_certificate(private, role, args.namespace, ca, ca_key, key)
             for role in ("identity", "knowledge")}
    passwords = {r: secrets.token_urlsafe(24) for r in ROLES}
    client_secret = secrets.token_urlsafe(32) if args.client_type=="confidential" else None
    origin = f"https://knowledge.{args.namespace}.svc.cluster.local"
    probe = {"origin": origin, "passwords": passwords, **profile}
    (private / "probe.json").write_text(json.dumps(probe))
    documents = platform_documents(args.namespace, args.application_namespace, args.keycloak_image)
    def create(namespace, document):
        # Never include a Secret or credential in command arguments or output.
        subprocess.run(base + ["-n", namespace, "create", "-f", "-"],
                       input=yaml.safe_dump(document).encode(), check=True, stdout=subprocess.DEVNULL)
    create(args.namespace, documents[0])
    create(args.namespace, {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "synthetic-realm"},
        "type": "Opaque", "stringData": {"realm.json": json.dumps(realm_document(origin, client_secret, passwords, **profile))}})
    # Reuse existing lab bootstrap for four fresh PVCs and random DB/app secrets.
    values_file = private / "base.values.yaml"
    subprocess.run([sys.executable, str(ROOT / "scripts/kubernetes/lab.py"), "--context", args.context,
                    "--namespace", args.application_namespace, "--values-output", str(values_file)], check=True)
    subprocess.run(base + ["label", "namespace", args.application_namespace, "okf-home-access=true"],
                   check=True, stdout=subprocess.DEVNULL)
    runtime = json.loads(subprocess.run(base + ["-n", args.application_namespace, "get", "secret", "runtime-secret", "-o", "json"],
                                       check=True, capture_output=True, text=True).stdout)
    runtime["stringData"] = {"KEYCLOAK_CLIENT_SECRET": client_secret} if client_secret else {}
    if not client_secret:runtime.get("data",{}).pop("KEYCLOAK_CLIENT_SECRET",None)
    subprocess.run(base + ["-n", args.application_namespace, "replace", "-f", "-"],
                   input=json.dumps(runtime).encode(), check=True, stdout=subprocess.DEVNULL)
    for role, ns in (("identity", args.namespace), ("knowledge", args.application_namespace)):
        create(ns, {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "lab-tls"}, "type": "kubernetes.io/tls",
                    "stringData": {"tls.crt": certs[role].read_text(), "tls.key": key.read_text()}})
    create(args.application_namespace, {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "synthetic-probe"},
        "type": "Opaque", "stringData": {"probe.json": json.dumps(probe), "ca.crt": ca.read_text()}})
    if args.runtime_trust_configmap:
        tag = args.backend_image_tag
        image = 'okf-backend:' + tag
        standard = subprocess.run(['docker', 'run', '--rm', '--pull=never', '--network=none', '--read-only',
            '--entrypoint', 'python', image, '-c',
            "from pathlib import Path; import os,sys; sys.stdout.buffer.write(Path(os.environ['SSL_CERT_FILE']).read_bytes())"],
            check=True, capture_output=True).stdout
        bundle = runtime_bundle(standard, ca.read_bytes())
        (private / 'runtime-bundle.pem').write_bytes(bundle)
        create(args.application_namespace, {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': {'name': args.runtime_trust_configmap}, 'immutable': True,
            'data': {'ca-bundle.crt': bundle.decode('utf-8')}})
    for document in documents[1:]:
        create(args.namespace, document)
    (private / "sso.values.yaml").write_text(yaml.safe_dump(application_values(args.namespace, args.backend_image_tag or backend_tag(ca.read_bytes()), trust_configmap=args.runtime_trust_configmap, **profile)))
    print('Prepared isolated runtime-trust lab; run managed install before OIDC probe.' if args.runtime_trust_configmap
          else 'Prepared isolated lab; build CA image and run managed install before the OIDC probe.')


if __name__ == "__main__":
    main()
