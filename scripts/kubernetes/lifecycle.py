"""Explicit Kubernetes maintenance operations; never automatically undo a migration.

Requires Python 3.11+, PyYAML, kubectl and Helm. Secrets belong in existing Secrets.
Every command supplies its context and namespace; subprocess arguments are arrays.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import ssl
import time
import uuid
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy/helm/ai-knowledge"
ARTIFACTS = {"postgres.dump", "data.tar", "qdrant.tar", "totals.json", "release.json"}


AUDIT_HOLD_SOURCE = r'''"""Fixed model-free restore hold, also executable in a saved old application image."""
import re
from sqlalchemy import DateTime, Integer, String, Text, inspect, text


def _expression(value):
    parts = []
    for token in re.findall(r"'(?:''|[^'])*'|[^']+", value):
        if token.startswith("'"):
            parts.append(token)
        else:
            token = re.sub(r"\s+", "", token.lower())
            token = re.sub(r"::(?:charactervarying|text)(?:\[\])?", "", token)
            parts.append(token.replace('(', '').replace(')', '').replace('"', ''))
    return ''.join(parts)


def validate_outbox_contract(connection):
    inspector = inspect(connection)
    expected = {
        'audit_id': (Integer(), False), 'payload': (Text(), False),
        'status': (String(16), False), 'attempt_count': (Integer(), False),
        'next_attempt_at': (DateTime(timezone=True), False),
        'last_error_code': (String(32), True), 'stdout_written_at': (DateTime(timezone=True), True),
    }
    columns = {c['name']: c for c in inspector.get_columns('audit_outbox')}
    if set(columns) != set(expected) or 'audit_log' not in inspector.get_table_names():
        raise ValueError('Unknown audit outbox schema')
    for name, (kind, nullable) in expected.items():
        actual = columns[name]
        if actual['nullable'] != nullable or actual['type'].compile(dialect=connection.dialect) != kind.compile(dialect=connection.dialect):
            raise ValueError('Unknown audit outbox schema')
    if inspector.get_pk_constraint('audit_outbox').get('constrained_columns') != ['audit_id']:
        raise ValueError('Unknown audit outbox schema')
    if inspector.get_pk_constraint('audit_log').get('constrained_columns') != ['id']:
        raise ValueError('Unknown audit outbox schema')
    keys = inspector.get_foreign_keys('audit_outbox')
    if len(keys) != 1:
        raise ValueError('Unknown audit outbox schema')
    key = keys[0]
    options = key.get('options') or {}
    if (key.get('constrained_columns') != ['audit_id'] or key.get('referred_table') != 'audit_log'
        or key.get('referred_columns') != ['id'] or key.get('referred_schema') is not None
        or any(options.get(k) not in (None, 'NO ACTION') for k in ('ondelete', 'onupdate'))
        or options.get('deferrable')):
        raise ValueError('Unknown audit outbox schema')
    checks = {c.get('name'): _expression(c['sqltext']) for c in inspector.get_check_constraints('audit_outbox')}
    if checks.get('ck_audit_outbox_status') not in {
        "statusin'pending','held','stdout_written'",
        "status=anyarray['pending','held','stdout_written']",
    } or checks.get('ck_audit_outbox_attempt_count') != 'attempt_count>=0':
        raise ValueError('Unknown audit outbox schema')
    if not any(c['column_names'] == ['status', 'next_attempt_at', 'audit_id']
               and not any(v is not None for k, v in (c.get('dialect_options') or {}).items() if k.endswith('_where'))
               for c in inspector.get_indexes('audit_outbox')):
        raise ValueError('Unknown audit outbox schema')


def hold_pending(engine, reason="restored_backup"):
    if reason not in {"restored_backup", "binary_rollback"}:
        raise ValueError("Invalid hold reason")
    with engine.begin() as connection:
        if "audit_outbox" not in inspect(connection).get_table_names():
            return 0
        validate_outbox_contract(connection)
        states = set(connection.execute(text("SELECT DISTINCT status FROM audit_outbox")).scalars())
        if not states.issubset({"pending", "held", "stdout_written"}):
            raise ValueError("Unknown audit outbox state")
        result = connection.execute(text("UPDATE audit_outbox SET status='held',last_error_code=:reason WHERE status='pending'"), {"reason": reason})
        return result.rowcount

if __name__ == "__main__":
    import json
    from app.db.session import get_engine
    print(json.dumps({"held": hold_pending(get_engine())}))
'''


def audit_outbox_hold_command(reason="restored_backup") -> list[str]:
    if reason not in {"restored_backup", "binary_rollback"}:
        raise OperationError("Invalid audit hold reason")
    source = AUDIT_HOLD_SOURCE.replace("hold_pending(get_engine())", f"hold_pending(get_engine(), {reason!r})")
    return ["python", "-c", source]


class OperationError(RuntimeError):
    pass




TRUST_PATH = '/etc/okf/trust/ca-bundle.crt'
TLS_KEYS = {'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS', 'SSL_VERIFY', 'NODE_TLS_REJECT_UNAUTHORIZED'}


def runtime_trust_reference(pod: dict) -> str | None:
    volumes = [v for v in pod.get('volumes', []) if v.get('name') == 'runtime-trust']
    containers = pod.get('containers', [])
    used = any(m.get('name') == 'runtime-trust' for c in containers for m in c.get('volumeMounts', []))
    used = used or any(e.get('value') == TRUST_PATH for c in containers for e in c.get('env', []))
    if not volumes and not used:
        return None
    try:
        if len(volumes) != 1:
            raise ValueError()
        source = volumes[0]['configMap']
        reference = source['name']
        if (not isinstance(reference, str) or not re.fullmatch(r'[a-z0-9]([-a-z0-9.]*[a-z0-9])?', reference)
                or len(reference) > 253 or source.get('optional') is True
                or source.get('items') != [{'key': 'ca-bundle.crt', 'path': 'ca-bundle.crt'}]):
            raise ValueError()
        if not containers:
            raise ValueError()
        for container in containers:
            mounts = [m for m in container.get('volumeMounts', []) if m.get('name') == 'runtime-trust']
            if mounts != [{'name': 'runtime-trust', 'mountPath': '/etc/okf/trust', 'readOnly': True}]:
                raise ValueError()
            keys = ('NODE_EXTRA_CA_CERTS',) if container.get('name') == 'frontend' else ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE')
            tls_env = [e for e in container.get('env', []) if e.get('name') in TLS_KEYS]
            if len(tls_env) != len(keys) or {e['name']: e.get('value') for e in tls_env} != {k: TRUST_PATH for k in keys}:
                raise ValueError()
        return reference
    except (KeyError, TypeError, ValueError) as exc:
        raise OperationError('Runtime trust env/mount/volume contract is inconsistent') from exc


def validate_trust_configmap(document: dict) -> dict:
    try:
        if document['kind'] != 'ConfigMap' or document.get('immutable') is not True:
            raise ValueError()
        body = document['data']['ca-bundle.crt']
        if not isinstance(body, str) or not body.strip() or 'PRIVATE KEY' in body:
            raise ValueError()
        # Public CA bundles may contain comment lines. Preserve original bytes for identity.
        clean = '\n'.join(line for line in body.splitlines() if not line.lstrip().startswith('#'))
        if not re.fullmatch(r'(?:\s*-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\s*)+', clean):
            raise ValueError()
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=clean)
        return {'configMap': document['metadata']['name'], 'key': 'ca-bundle.crt',
                'sha256': hashlib.sha256(body.encode('utf-8')).hexdigest()}
    except (KeyError, TypeError, ValueError, ssl.SSLError) as exc:
        raise OperationError('Runtime trust requires an immutable ConfigMap with valid public PEM certificates') from exc


def operation_pod_spec(template: dict, backend: dict) -> dict:
    reference = runtime_trust_reference(template)
    container = {key: copy.deepcopy(backend[key]) for key in ('image', 'envFrom', 'env', 'securityContext', 'resources')}
    container.update(name='operation', imagePullPolicy=backend.get('imagePullPolicy', 'IfNotPresent'),
                     volumeMounts=[{'name': 'data', 'mountPath': '/data'}, {'name': 'work', 'mountPath': '/work'}])
    data_volume = next(v for v in template['volumes'] if v['name'] == 'data')
    spec = {'restartPolicy': 'Never', 'automountServiceAccountToken': False,
            'securityContext': copy.deepcopy(template['securityContext']), 'containers': [container],
            'volumes': [copy.deepcopy(data_volume), {'name': 'work', 'emptyDir': {}}]}
    if reference:
        container['volumeMounts'].append({'name': 'runtime-trust', 'mountPath': '/etc/okf/trust', 'readOnly': True})
        spec['volumes'].append(copy.deepcopy(next(v for v in template['volumes'] if v['name'] == 'runtime-trust')))
        runtime_trust_reference(spec)
    for key in ('imagePullSecrets', 'nodeSelector', 'tolerations'):
        if key in template:
            spec[key] = copy.deepcopy(template[key])
    return spec


def validate_operator_budgets(desired: dict, current: dict | None) -> None:
    """Product reserves within existing 600s start / 180s stop waits, not CSI guarantees."""
    def duration(probe, field):
        defaults = {'initialDelaySeconds': 0, 'periodSeconds': 10, 'timeoutSeconds': 1, 'failureThreshold': 3}
        value = probe.get(field, defaults[field])
        if type(value) is not int or value < (0 if field == 'initialDelaySeconds' else 1):
            raise OperationError('Invalid probe timing field: ' + field)
        return value

    for label, document in (('desired', desired), ('current', current)):
        if document is None:
            continue
        spec = document['spec']['template']['spec']
        grace = spec.get('terminationGracePeriodSeconds', 30)
        if type(grace) is not int or grace < 0 or grace + 30 > 180:
            raise OperationError(label + '.terminationGracePeriodSeconds + 30 exceeds stop budget 180s')
        if label != 'desired':
            continue
        for container in spec.get('containers', []):
            total = 0
            for kind in ('startup', 'readiness'):
                probe = container.get(kind + 'Probe')
                if probe is None:
                    continue  # Absent probe has no timer; omitted fields use Kubernetes defaults.
                span = duration(probe, 'initialDelaySeconds') + duration(probe, 'failureThreshold') * max(
                    duration(probe, 'periodSeconds'), duration(probe, 'timeoutSeconds'))
                total += span
            if total + 60 > 600:
                raise OperationError('desired startup/readiness timings + 60 exceed application-ready budget 600s')


def validate_runtime_secret(secret):
    try:
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) for key in secret["data"]):
            raise ValueError()
        controlled = {"ENVIRONMENT", "DEPLOYMENT_TIER", "MODEL_MODE", "UVICORN_WORKERS", "STORAGE_MODE", "DATA_DIR", "QDRANT_URL", "EMBEDDING_PROVIDER"}
        if controlled.intersection(secret["data"]):
            raise ValueError()
        fields = {key: base64.b64decode(secret["data"][key], validate=True).decode()
                  for key in ("DATABASE_URL", "POSTGRES_PASSWORD", "APP_SECRET_KEY")}
        url = urlsplit(fields["DATABASE_URL"])
        if (url.scheme != "postgresql+psycopg" or url.hostname != "postgres" or url.port != 5432
                or url.username != "okf" or url.path != "/okf_knowledge"
                or unquote(url.password or "") != fields["POSTGRES_PASSWORD"] or url.query
                or len(fields["APP_SECRET_KEY"]) < 32):
            raise ValueError()
    except (KeyError, ValueError, UnicodeError) as exc:
        raise OperationError("Runtime Secret must use matching bundled PostgreSQL credentials and a strong session key") from exc


def validate_claim_bindings(current, desired):
    def claims(document):
        volumes = document["spec"]["template"]["spec"].get("volumes", [])
        return {v["name"]: v["persistentVolumeClaim"]["claimName"] for v in volumes if "persistentVolumeClaim" in v}
    if current and claims(current) != claims(desired):
        raise OperationError("Persistent claim bindings changed; use a separate verified storage migration")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_restore_trust(saved: dict | None, target: dict | None) -> None:
    for identity in (saved, target):
        if identity is not None:
            if (not isinstance(identity, dict) or set(identity) != {'configMap', 'key', 'sha256'}
                    or not isinstance(identity.get('configMap'), str) or not identity['configMap']
                    or identity.get('key') != 'ca-bundle.crt'
                    or not isinstance(identity.get('sha256'), str)
                    or not re.fullmatch(r'[a-f0-9]{64}', identity['sha256'])):
                raise OperationError('Invalid saved/target trust identity')
    if ((saved is None) != (target is None)
            or saved is not None and (saved['key'], saved['sha256']) != (target['key'], target['sha256'])):
        raise OperationError('Restore trust bundle must match the saved public bundle (target name may differ)')


def verify_backup(path):
    try:
        if (path / 'manifest.json').is_symlink():
            raise ValueError()
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        if not isinstance(manifest, dict):
            raise ValueError()
        if type(manifest.get('format')) is not int or manifest['format'] != 2:
            raise OperationError('Unsupported backup format; legacy format 1 requires the original kit-v1')
        files = manifest['files']
        if manifest.get('complete') is not True or not isinstance(files, dict) or not ARTIFACTS <= files.keys():
            raise ValueError()
        for name, checksum in files.items():
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError()
            artifact = path / name
            if artifact.is_symlink() or sha256(artifact) != checksum:
                raise ValueError()
        identity = json.loads((path / 'release.json').read_text(encoding='utf-8'))
        if not isinstance(identity, dict) or 'trust' not in identity:
            raise ValueError()
        trust = identity['trust']
        validate_restore_trust(trust, trust)
        bundle = path / 'trust-bundle.pem'
        if trust is None:
            if 'trust-bundle.pem' in files or bundle.exists() or bundle.is_symlink():
                raise ValueError()
        else:
            if files.get('trust-bundle.pem') != trust['sha256']:
                raise ValueError()
            validated = validate_trust_configmap({'kind': 'ConfigMap', 'immutable': True,
                'metadata': {'name': trust['configMap']}, 'data': {'ca-bundle.crt': bundle.read_bytes().decode('utf-8')}})
            if validated != trust:
                raise ValueError()
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OperationError('Backup is incomplete or failed trust/integrity verification') from exc


def database_revision_command():
    # Read metadata without resolving this image's Alembic graph: after a
    # compatible binary rollback the database can retain a newer revision.
    return ["python", "-c", "from sqlalchemy import text; from app.db.session import get_engine; "
            "c=get_engine().connect(); rows=c.execute(text('select version_num from alembic_version')).scalars().all(); "
            "assert len(rows)==1 and isinstance(rows[0],str) and rows[0]; print(rows[0]); c.close()"]


class Manager:
    def __init__(self, context, namespace, release, values, chart_path=CHART, expected_tier=None, migration_image=None):
        for value in (namespace, release):
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,48}", value):
                raise OperationError("Invalid namespace/release")
        self.context, self.namespace, self.release = context, namespace, release
        self.expected_tier = expected_tier
        self.migration_image = migration_image
        self._migration_backend = None
        self.values = [str(Path(value).resolve()) for value in values]
        self.chart_path = str(Path(chart_path).resolve())
        self.operation = uuid.uuid4().hex[:12]
        self.lock = "okf-operation-lock"
        self.base = ["kubectl", "--context", context, "--namespace", namespace]
        self.selector = f"app.kubernetes.io/instance={release},okf-role=application"
        self.documents = []
        self.trust_identities = {}

    def run(self, args, *, data=None, output=None, source=None, allow_failure=False):
        # stderr can contain credentials or document content. Do not relay it to CI.
        result = subprocess.run(args, input=data, stdin=source, stdout=output or subprocess.PIPE,
                                stderr=subprocess.PIPE, check=False)
        if result.returncode and not allow_failure:
            raise OperationError(f"{Path(args[0]).name} operation failed (exit {result.returncode}); inspect restricted cluster logs")
        return result

    def kube(self, *args, **kwargs):
        return self.run(self.base + list(args), **kwargs)

    def get(self, kind, name):
        result = self.kube("get", kind, name, "--ignore-not-found", "-o", "json")
        return json.loads(result.stdout) if result.stdout.strip() else None

    def apply(self, document, *, create=False):
        self.kube("create" if create else "apply", "-f", "-", data=yaml.safe_dump(document).encode())

    def preflight(self, *, require_secret=True):
        args = ["helm", "template", self.release, self.chart_path, "--namespace", self.namespace]
        for value in self.values:
            args += ["-f", value]
        self.documents = list(yaml.safe_load_all(self.run(args).stdout))
        if not self.get("namespace", self.namespace):
            raise OperationError("Create the target namespace, external PVCs and runtime Secret first")
        runtime = self.document("ConfigMap", "runtime")["data"]
        if self.expected_tier and runtime["DEPLOYMENT_TIER"] != self.expected_tier:
            raise OperationError("Rendered deployment tier differs from the expected delivery environment")
        application = self.document("Deployment", "application")
        spec = application["spec"]["template"]["spec"]
        backend = next(c for c in spec["containers"] if c["name"] == "backend")
        self.secret_name = backend["envFrom"][1]["secretRef"]["name"]
        secret = self.get("secret", self.secret_name)
        if require_secret and (not secret or not {"DATABASE_URL", "POSTGRES_PASSWORD", "APP_SECRET_KEY"} <= secret.get("data", {}).keys()):
            raise OperationError("Runtime Secret is missing mandatory keys")
        if require_secret:
            validate_runtime_secret(secret)
        for document in self.documents:
            if not document:
                continue
            if document["kind"] in ("Deployment", "StatefulSet"):
                for volume in document["spec"]["template"]["spec"].get("volumes", []):
                    claim = volume.get("persistentVolumeClaim", {}).get("claimName")
                    if claim and not self.get("pvc", claim):
                        raise OperationError("An external persistent claim is missing")
            if document["kind"] in ("ListenerSet", "HTTPRoute", "VaultStaticSecret", "TrafficPolicy"):
                self.preflight_platform(document)
        if runtime["DEPLOYMENT_TIER"] == "prod" and runtime["MODEL_MODE"] != "live":
            raise OperationError("Production requires live models")
        # One release per namespace: fixed Service names are the frontend build contract.
        existing = self.get("configmap", "runtime")
        if existing and existing.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/instance") != self.release:
            raise OperationError("Namespace belongs to another application release")
        for name in ("postgres", "qdrant"):
            current = self.get("statefulset", name)
            desired = self.document("StatefulSet", name)
            validate_claim_bindings(current, desired)
            if current and current["spec"]["template"]["spec"]["containers"][0]["image"] != desired["spec"]["template"]["spec"]["containers"][0]["image"]:
                raise OperationError("Storage image changes require a separate validated storage upgrade")
        current_application = self.get("deployment", "application")
        self._migration_backend = None
        if self.migration_image:
            current_backend = next((c for c in (current_application or {}).get("spec", {}).get("template", {}).get("spec", {}).get("containers", []) if c.get("name") == "backend"), None)
            if not current_backend or current_backend.get("image") != self.migration_image:
                raise OperationError("Requested migration image must match the installed backend image")
            self._migration_backend = copy.deepcopy(backend)
            self._migration_backend["image"] = self.migration_image
        validate_operator_budgets(application, current_application)
        validate_claim_bindings(current_application, application)
        self.preflight_trust(spec, runtime)
        if current_application:
            self.preflight_trust(current_application['spec']['template']['spec'])

    def preflight_trust(self, pod: dict, runtime: dict | None = None) -> dict | None:
        reference = runtime_trust_reference(pod)
        if not reference:
            return None
        document = self.get('configmap', reference)
        if not document:
            raise OperationError('Runtime trust ConfigMap is missing')
        identity = validate_trust_configmap(document)
        previous = self.trust_identities.get(reference)
        if previous and previous != identity:
            raise OperationError('Runtime trust ConfigMap identity changed during operation')
        self.trust_identities[reference] = identity
        if runtime is not None and TLS_KEYS.intersection(runtime):
            raise OperationError('Runtime config conflicts with controlled TLS keys')
        for container in pod.get('containers', []):
            for source in container.get('envFrom', []):
                kind = 'secret' if 'secretRef' in source else 'configmap'
                name = source.get('secretRef', source.get('configMapRef', {})).get('name')
                data = self.get(kind, name)
                if data and TLS_KEYS.intersection(data.get('data', {})):
                    raise OperationError('Runtime envFrom conflicts with controlled TLS keys')
        return identity

    def preflight_platform(self, document: dict) -> None:
        api_version = document['apiVersion']
        discovery = json.loads(self.kube('get', '--raw', '/apis/' + api_version).stdout)
        matches = [r for r in discovery.get('resources', [])
                   if r.get('kind') == document['kind'] and r.get('namespaced') is True and '/' not in r.get('name', '')]
        if len(matches) != 1:
            raise OperationError('Required namespaced platform kind is not available: ' + document['kind'])
        resource = matches[0]['name'] + '.' + api_version.split('/')[0]
        def permission(verb):
            response = self.kube('auth', 'can-i', verb, resource, allow_failure=True)
            if response.returncode or response.stdout.strip() != b'yes':
                raise OperationError('Platform permission denied: ' + verb + ' ' + document['kind'])
        permission('get')
        existing = self.get(resource, document['metadata']['name'])
        permission('patch' if existing else 'create')
        if document['kind'] != 'TrafficPolicy':
            return
        if existing:
            meta = existing.get('metadata', {})
            annotations = meta.get('annotations', {})
            if (annotations.get('meta.helm.sh/release-name') != self.release
                    or annotations.get('meta.helm.sh/release-namespace') != self.namespace
                    or meta.get('labels', {}).get('app.kubernetes.io/managed-by') != 'Helm'):
                raise OperationError('TrafficPolicy belongs to another owner')
            self.kube('patch', resource, document['metadata']['name'], '--type=merge', '--patch', json.dumps(document),
                      '--dry-run=server', '-o', 'json')
        else:
            self.kube('create', '-f', '-', '--dry-run=server', '-o', 'json', data=yaml.safe_dump(document).encode())

    def document(self, kind, name):
        return next(d for d in self.documents if d and d["kind"] == kind and d["metadata"]["name"] == name)

    def acquire(self):
        if self.get("configmap", self.lock):
            raise OperationError("Maintenance lock exists; inspect its operation and Jobs before recovery")
        self.apply({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": self.lock},
                    "data": {"operation": self.operation, "release": self.release}}, create=True)
        print(f"Maintenance operation: {self.operation}", flush=True)

    def release_lock(self):
        lock = self.get("configmap", self.lock)
        if not lock or lock["data"]["operation"] != self.operation:
            raise OperationError("Operation lock ownership changed")
        self.kube("delete", "configmap", self.lock)

    def application_pods(self):
        return json.loads(self.kube("get", "pods", "-l", self.selector, "-o", "json").stdout)["items"]

    def stop(self):
        old = self.get("deployment", "application")
        if old:
            self.kube("scale", "deployment/application", "--replicas=0")
        deadline = time.monotonic() + 180
        while self.application_pods():
            if time.monotonic() > deadline:
                raise OperationError("Old application has not exited; no writer Job will start (never force-delete)")
            time.sleep(2)
        return old

    def chart(self, enabled):
        args = ["helm", "upgrade", "--install", self.release, self.chart_path, "--kube-context", self.context,
                "--namespace", self.namespace, "--timeout", "10m", "--set", f"application.enabled={str(enabled).lower()}"]
        for value in self.values:
            args += ["-f", value]
        # No --atomic: Helm rollback cannot roll back SQL or safely restart an old writer.
        self.run(args)

    def storage_ready(self):
        for service in ("postgres", "qdrant"):
            self.kube("rollout", "status", f"statefulset/{service}", "--timeout=300s")

    def job(self, command, *, backend=None, template=None):
        template = template or self.document("Deployment", "application")["spec"]["template"]["spec"]
        backend = backend or next(c for c in template["containers"] if c["name"] == "backend")
        name = f"okf-operation-{self.operation}"
        labels = {"app.kubernetes.io/instance": self.release, "okf-role": "operation"}
        labels["app.kubernetes.io/part-of"] = "ai-knowledge"
        self.preflight_trust(template)
        spec = operation_pod_spec(template, backend)
        spec['containers'][0]['command'] = command
        self.apply({"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": name, "labels": labels},
                    "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 3600,
                             "template": {"metadata": {"labels": labels}, "spec": spec}}}, create=True)
        return name

    def toolbox(self, *, backend=None, template=None):
        name = self.job(["python", "-c", "import time; time.sleep(3500)"], backend=backend, template=template)
        deadline = time.monotonic() + 300
        while True:
            pods = json.loads(self.kube("get", "pods", "-l", f"job-name={name}", "-o", "json").stdout)["items"]
            if pods:
                pod = pods[0]["metadata"]["name"]
                self.kube("wait", "--for=condition=Ready", f"pod/{pod}", "--timeout=300s")
                return name, pod
            if time.monotonic() > deadline:
                raise OperationError("Operation Pod was not scheduled")
            time.sleep(2)

    def finish_job(self, name):
        self.kube("delete", "job", name, "--wait=true", "--timeout=180s")
        deadline = time.monotonic() + 180
        while json.loads(self.kube("get", "pods", "-l", f"job-name={name}", "-o", "json").stdout)["items"]:
            if time.monotonic() > deadline:
                raise OperationError("Operation Pod has not exited; refusing to start another writer")
            time.sleep(2)

    def exec(self, pod, command, **kwargs):
        return self.kube("exec", "-i", pod, "--", *command, **kwargs)

    def migrate(self):
        migration_backend = getattr(self, "_migration_backend", None)
        if migration_backend:
            name = self.job(["python", "scripts/kubernetes_migrate.py"], backend=migration_backend)
        else:
            name = self.job(["python", "scripts/kubernetes_migrate.py"])
        deadline = time.monotonic() + 600
        while True:
            status = self.get("job", name).get("status", {})
            if any(c.get("type") == "Failed" and c.get("status") == "True" for c in status.get("conditions", [])):
                raise OperationError("Migration Job failed; application remains stopped and lock retained")
            if status.get("succeeded") == 1:
                break
            if time.monotonic() > deadline:
                raise OperationError("Migration Job timed out; inspect it before recovery")
            time.sleep(2)
        self.finish_job(name)

    def start(self):
        self.chart(True)
        self.kube("rollout", "status", "deployment/application", "--timeout=600s")

    def deploy(self, backup_root):
        self.preflight()
        self.acquire()
        old = self.stop()
        if old:
            self.backup_stopped(backup_root, old)
        self.chart(False)
        self.storage_ready()
        self.migrate()
        if getattr(self, "_migration_backend", None):
            if self.document("ConfigMap", "runtime")["data"].get("AUDIT_STDOUT_MODE", "disabled") != "disabled":
                raise OperationError("Binary rollback requires audit transport disabled; application remains stopped")
            job, pod = self.toolbox()
            self.exec(pod, audit_outbox_hold_command("binary_rollback"))
            self.finish_job(job)
        self.start()
        self.release_lock()

    def provision(self):
        # Initial Vault synchronization only; never mutate an existing installation.
        self.preflight(require_secret=False)
        for kind in ("deployment", "statefulset", "job"):
            if json.loads(self.kube("get", kind, "-o", "json").stdout)["items"]:
                raise OperationError("Provision requires a fresh namespace without workloads")
        if not any(d and d["kind"] == "VaultStaticSecret" for d in self.documents):
            raise OperationError("Provision requires an enabled platform VaultStaticSecret")
        self.acquire()
        self.chart(False)
        deadline = time.monotonic() + 180
        while True:
            secret = self.get("secret", self.secret_name)
            if secret and {"DATABASE_URL", "POSTGRES_PASSWORD", "APP_SECRET_KEY"} <= secret.get("data", {}).keys():
                break
            if time.monotonic() > deadline:
                raise OperationError("VSO runtime Secret has not synchronized; application stays disabled")
            time.sleep(2)
        self.storage_ready()
        self.preflight()
        self.release_lock()

    def backup_stopped(self, root, old):
        # Old image/config are retained until this coherent backup is complete.
        target = root / self.operation
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
        old_template = old['spec']['template']['spec']
        trust = self.preflight_trust(old_template)
        if trust:
            body = self.get('configmap', trust['configMap'])['data']['ca-bundle.crt'].encode('utf-8')
            if hashlib.sha256(body).hexdigest() != trust['sha256']:
                raise OperationError('Old public trust bundle changed before backup')
            (target / 'trust-bundle.pem').write_bytes(body)
        backend = next(c for c in old["spec"]["template"]["spec"]["containers"] if c["name"] == "backend")
        job, pod = self.toolbox(backend=backend, template=old["spec"]["template"]["spec"])
        pg_pods = json.loads(self.kube("get", "pods", "-l", f"app.kubernetes.io/instance={self.release},okf-role=postgres", "-o", "json").stdout)["items"]
        pg = pg_pods[0]["metadata"]["name"]
        with (target / "postgres.dump").open("xb") as output:
            self.exec(pg, ["pg_dump", "-U", "okf", "-d", "okf_knowledge", "-Fc", "--no-owner", "--no-acl"], output=output)
        self.exec(pod, ["python", "scripts/check_integrity.py", "--strict", "--verify-content"])
        self.exec(pod, ["python", "scripts/backup_totals.py", "--output", "/work/totals.json"])
        self.exec(pod, ["python", "scripts/create_qdrant_snapshot.py", "--output-dir", "/work/qdrant"])
        for name, command in {"data.tar": ["tar", "-C", "/data", "-cf", "-", "."],
                              "qdrant.tar": ["tar", "-C", "/work/qdrant", "-cf", "-", "."],
                              "totals.json": ["cat", "/work/totals.json"]}.items():
            with (target / name).open("xb") as output:
                self.exec(pod, command, output=output)
        revision = self.exec(pod, database_revision_command()).stdout.decode().strip()
        identity = {"images": [c["image"] for c in old["spec"]["template"]["spec"]["containers"]],
                    "alembic": revision, "trust": trust,
                    "storage_images": [self.get("statefulset", s)["spec"]["template"]["spec"]["containers"][0]["image"] for s in ("postgres", "qdrant")],
                    "model_mode": self.get("configmap", "runtime")["data"]["MODEL_MODE"]}
        (target / "release.json").write_text(json.dumps(identity, indent=2), encoding="utf-8")
        self.finish_job(job)
        artifacts = ARTIFACTS | ({'trust-bundle.pem'} if trust else set())
        manifest = {"format": 2, "complete": True, "namespace": self.namespace, "operation": self.operation,
                    "files": {name: sha256(target / name) for name in sorted(artifacts)}}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        verify_backup(target)
        print(f"Verified backup: {target}", flush=True)
        return target

    def backup(self, root):
        self.preflight()
        self.acquire()
        old = self.stop()
        if not old:
            raise OperationError("No application installation to back up")
        self.backup_stopped(root, old)
        self.kube("scale", "deployment/application", "--replicas=1")
        self.kube("rollout", "status", "deployment/application", "--timeout=600s")
        self.release_lock()

    def restore(self, source):
        raise OperationError('Automatic restore is unsupported in the compact home kit; use the stopped manual restore runbook')

    def recover_lock(self, operation):
        lock = self.get("configmap", self.lock)
        if not lock or lock["data"]["operation"] != operation or lock["data"]["release"] != self.release:
            raise OperationError("Recovery operation does not own the lock")
        if self.application_pods():
            raise OperationError("Application Pods still exist; recovery refused")
        jobs = json.loads(self.kube("get", "jobs", "-l", f"app.kubernetes.io/instance={self.release}", "-o", "json").stdout)["items"]
        terminal = lambda job: any(c.get("type") in ("Complete", "Failed") and c.get("status") == "True"
                                   for c in job.get("status", {}).get("conditions", []))
        if any(not terminal(j) for j in jobs):
            raise OperationError("An operation Job can still start a writer; delete it normally and wait for its Pods before recovery")
        pods = json.loads(self.kube("get", "pods", "-l", f"app.kubernetes.io/instance={self.release},okf-role=operation", "-o", "json").stdout)["items"]
        if any(p["status"].get("phase") not in ("Succeeded", "Failed") for p in pods):
            raise OperationError("An operation Pod may still be running; recovery refused")
        self.operation = operation
        self.release_lock()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("preflight", "provision", "deploy", "backup", "restore", "recover-lock"))
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release", default="ai-knowledge")
    parser.add_argument("--chart", type=Path, default=CHART)
    parser.add_argument("--expected-tier", choices=("home", "ci", "dev", "test", "prod"))
    parser.add_argument("--values", action="append", required=True)
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--migration-image", help="Deploy only: installed backend image for forward migrations during a validated binary rollback")
    args = parser.parse_args()
    if args.operation == 'restore':
        parser.error('Automatic restore is unsupported in the compact home kit; use the stopped manual restore runbook before application startup')
    try:
        if args.migration_image and args.operation != "deploy":
            parser.error("--migration-image is allowed only for deploy")
        manager = Manager(args.context, args.namespace, args.release, args.values, args.chart, args.expected_tier, args.migration_image)
        if args.operation in ("deploy", "backup"):
            if not args.backup_root:
                parser.error("--backup-root is required, outside application PVCs")
            getattr(manager, args.operation)(args.backup_root.resolve())
        elif args.operation == "restore":
            if not args.backup:
                parser.error("--backup is required")
            manager.restore(args.backup.resolve())
        elif args.operation == "recover-lock":
            if not args.operation_id:
                parser.error("--operation-id is required after inspecting the failed operation")
            manager.recover_lock(args.operation_id)
        elif args.operation == "provision":
            manager.provision()
        else:
            manager.preflight()
        return 0
    except (OperationError, OSError, StopIteration) as exc:
        print(f"Operation stopped: {exc}. Inspect the maintenance lock; no automatic SQL rollback.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
