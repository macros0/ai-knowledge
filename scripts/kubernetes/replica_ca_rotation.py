"""Explicit same-installation CA replacement for the owned synthetic kind recipe.

Every phase is journaled. Unknown outcomes keep maintenance held; no data volume,
application credential, schema, operator installation or production cron changes.
"""
from __future__ import annotations

import argparse
import base64
import copy
import ipaddress
import json
import re
import socket
import ssl
import sys
import textwrap
import traceback
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import connections, digest, sql_source, validate_drained
from replica_credentials import (
    ReplicaCredentials,
    assert_credential_stopped,
    bind_trust_configmap,
    credential_secret_data,
    scoped_s3_probe,
)
from replica_upgrade import UpgradeLab, corpus, operator_job
from replica_worker_loss import BASELINE_SOURCE, emit
from test_scripts.replica_runtime_acceptance import validate_inventory
from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent


def tls_material(config, operation):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    if (not re.fullmatch(r"[0-9a-f]{32}", operation) or not re.fullmatch(r"replica-test-[a-z0-9]{8,32}", config["namespace"])
            or not re.fullmatch(r"[a-z0-9-]{1,48}", config["storage_release"])):
        raise ValueError("Owned certificate scope required")
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Owned replica CA " + operation)])
    root = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=7))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True).sign(key, hashes.SHA256()))
    pem = root.public_bytes(serialization.Encoding.PEM).decode()
    private = lambda value: value.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    result = {"ca": pem, "ca_key": private(key), "leaves": {}}
    for role in ("postgres", "qdrant", "artifacts"):
        service = config["storage_release"] + "-" + role
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        san = [x509.DNSName(value) for value in (service, f"{service}.{config['namespace']}.svc", f"{service}.{config['namespace']}.svc.cluster.local")]
        san.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service)]))
            .issuer_name(root.subject).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False).sign(key, hashes.SHA256()))
        result["leaves"][role] = {name: base64.b64encode(value.encode()).decode() for name, value in {
            "tls.crt": leaf.public_bytes(serialization.Encoding.PEM).decode(), "tls.key": private(leaf_key), "ca.crt": pem}.items()}
    return result


def stage_storage_tls(value, previous, selected):
    result = copy.deepcopy(value)
    if result["spec"].get("replicas") != 0:
        raise ValueError("Storage must be stopped before changing certificate binding")
    pod = result["spec"]["template"]["spec"]
    volumes = [v for v in pod.get("volumes", []) if v["name"] == "tls"]
    mounts = [v for c in pod["containers"] for v in c.get("volumeMounts", []) if v["name"] == "tls"]
    if (len(pod["containers"]) != 1 or len(volumes) != 1 or volumes[0].get("secret", {}).get("secretName") not in {previous, selected}
            or len(mounts) != 1 or mounts[0].get("readOnly") is not True or mounts[0].get("mountPath") != "/run/tls"):
        raise ValueError("Expected storage certificate projection differs")
    volumes[0]["secret"]["secretName"] = selected
    result.pop("status", None)
    result.get("metadata", {}).pop("managedFields", None)
    return result


def assert_storage_source(current, original, previous, selected):
    if current["metadata"]["uid"] != original["metadata"]["uid"] or current["spec"].get("replicas") not in {0, 1}:
        raise ValueError("Storage controller identity/count changed")
    observed, expected = copy.deepcopy(current), copy.deepcopy(original)
    observed["spec"]["replicas"] = expected["spec"]["replicas"] = 0
    if stage_storage_tls(observed, previous, selected)["spec"] != stage_storage_tls(expected, previous, selected)["spec"]:
        raise ValueError("Storage controller inputs changed")


def require_recovery_point(manifest, config, fingerprint, *, now):
    source = manifest.plan.source
    if (manifest.status != "complete" or source.installation_id != config["installation_id"] or source.namespace != config["namespace"]
            or manifest.plan.schema_revision != "rpl05_maintenance_epoch" or manifest.plan.model_vector_sha256 != digest(fingerprint)
            or not 0 <= (now - source.consistent_at).total_seconds() <= 86400):
        raise ValueError("A matching authenticated independent recovery point younger than24h is required")


def vector_inventory(client, collection):
    values, cursor, seen, size = [], None, set(), 0
    while True:
        body = {"limit": 128, "with_payload": True, "with_vector": True}
        if cursor is not None:
            body["offset"] = cursor
        response = client.post("/collections/" + collection + "/points/scroll", json=body); response.raise_for_status()
        page = response.json()["result"]
        for point in page["points"]:
            value = json.dumps(point, sort_keys=True, separators=(",", ":"))
            size += len(value.encode()); values.append(value)
            if len(values) > 10000 or size > 64 * 1024 * 1024:
                raise ValueError("Owned vector inventory bound exceeded")
        cursor = page.get("next_page_offset")
        if cursor is None:
            break
        key = json.dumps(cursor)
        if key in seen:
            raise ValueError("Vector pagination did not progress")
        seen.add(key)
    return {"count": len(values), "sha256": sha256("\n".join(sorted(values)).encode()).hexdigest()}


def filter_retired_diagnostics(items, records, ready):
    """A recorded failed diagnostic stays failed; only verified stopped inputs leave the drain view."""
    retired = set()
    for record in records:
        matches = [v for v in items if v["kind"] == "Job" and v["metadata"]["name"] == record["name"]]
        if len(matches) != 1:
            raise ValueError("Retained failed diagnostic disappeared")
        job = matches[0]; status = job.get("status", {})
        if (job["metadata"]["uid"] != record["uid"] or job["metadata"].get("deletionTimestamp")
                or digest(job["spec"]) != record["spec_sha256"] or job["spec"].get("backoffLimit") != 0
                or status.get("active", 0) != 0 or not status.get("failed")
                or not any(c["type"] == "Failed" and c["status"] == "True" for c in status.get("conditions", []))):
            raise ValueError("Recorded diagnostic is not the same terminal failed Job")
        for pod in items:
            if pod["kind"] != "Pod" or not any(r.get("kind") == "Job" and r.get("uid") == record["uid"] and r.get("controller") is True
                    for r in pod["metadata"].get("ownerReferences", [])):
                continue
            states = pod.get("status", {})
            containers = states.get("containerStatuses", []) + states.get("initContainerStatuses", [])
            if (states.get("phase") not in {"Failed", "Succeeded"} or ready.get(pod.get("spec", {}).get("nodeName")) is not True
                    or not containers or any("terminated" not in c.get("state", {}) for c in containers)):
                raise ValueError("Recorded failed diagnostic has an uncertain process")
            retired.add(pod["metadata"]["uid"])
        retired.add(record["uid"])
    return [v for v in items if v["metadata"].get("uid") not in retired]


def require_retained_session(response, username):
    response.raise_for_status()
    value = response.json()
    user = value.get("user", {})
    if value.get("mode") != "simulation" or user.get("username") != username or "admin" not in user.get("roles", []):
        raise ValueError("The retained authenticated administrator session was not preserved")


class CaLab(UpgradeLab):
    def __init__(self, config, namespace_uid, operation):
        super().__init__(config, namespace_uid)
        self.operation = operation

    def ready_nodes(self):
        nodes = json.loads(self.kubectl("get", "nodes", "-o", "json", namespaced=False))["items"]
        return {n["metadata"]["name"]: any(c["type"] == "Ready" and c["status"] == "True"
            for c in n.get("status", {}).get("conditions", [])) for n in nodes}

    def filter_stop_inventory(self, items, nodes):
        records = (self.operation.journal or {}).get("retired_diagnostics", [])
        ready = {n['metadata']['name']: any(c.get('type') == 'Ready' and c.get('status') == 'True'
            for c in n.get('status', {}).get('conditions', [])) for n in nodes}
        return filter_retired_diagnostics(items, records, ready) if records else items

    def inventory(self):
        items = super().inventory()
        records = (self.operation.journal or {}).get("retired_diagnostics", [])
        return filter_retired_diagnostics(items, records, self.ready_nodes()) if records else items


class CaRotation(ReplicaCredentials):
    def __init__(self, config, namespace_uid, journal, expected_fingerprint):
        super().__init__(config, namespace_uid, journal, expected_fingerprint)
        self.lab = CaLab(config, namespace_uid, self)

    def new_config(self):
        return {**self.journal["old_config"], "ca_file": self.journal["new_ca_file"]}

    def fence(self):
        if self.journal["phase"] != "held":
            super().fence()
        if self.journal.get("storage_updated"):
            raise ValueError("CA storage already updated; resume the recorded later phase")
        self.lab.drained()
        engine, s3, q = connections(self.config)
        try:
            vectors = vector_inventory(q, self.config["collection"])
            if not vectors["count"]:
                raise ValueError("The owned nonempty corpus requires a nonempty vector inventory")
            self.save(vector_inventory=vectors)
        finally:
            engine.dispose(); s3.close(); q.close()
        emit("ca-held-vector-inventory", points=vectors["count"])

    def prepare(self, archive, key_file, credential_journal):
        import httpx
        from app.deployment.replica_backup import verify_backup
        if self.journal is None:
            verified = verify_backup(archive, Path(key_file).read_bytes())
            require_recovery_point(verified, self.config, self.fingerprint, now=datetime.now(timezone.utc))
            if verified.plan.source.failure_domain == verified.plan.destination.failure_domain:
                raise ValueError("Independent backup required")
            probe = FaultProbe(self.kube, self.value); require_quiescent(probe)
            if probe.identity["fingerprint"] != self.fingerprint:
                raise ValueError("Source runtime changed")
            objects = self.lab.inventory()
            deployments = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Deployment"}
            ready = validate_inventory(self.kube.inventory(), self.value)
            if dict(verified.plan.images) != ready["images"]:
                raise ValueError("Recovery point images differ")
            prior = json.loads(Path(credential_journal).read_bytes())
            api = deployments["api"]; pod = api["spec"]["template"]["spec"]
            runtime_secret = next(v["secretRef"]["name"] for v in pod["containers"][0]["envFrom"] if "secretRef" in v)
            old_secret = json.loads(self.kube.run("get", "secret", runtime_secret, "-o", "json"))
            operator = json.loads(self.kube.run("get", "secret", prior["operator_secret"], "-o", "json"))
            if (prior["phase"] != "verified" or prior["namespace_uid"] != self.value["namespace_uid"]
                    or prior["new_identity"]["installation_id"] != self.config["installation_id"]
                    or old_secret["metadata"]["uid"] != prior["runtime_secret_uid"]
                    or old_secret["data"] != credential_secret_data(prior["old_secret"]["data"], prior["new_db_runtime_url"], prior["s3_access"], prior["s3_secret"])[0]
                    or operator["metadata"]["uid"] != prior["operator_secret_uid"]
                    or operator["data"] != {"url": prior["old_secret"]["data"]["DATABASE_URL"]}):
                raise ValueError("Scoped runtime/operator credentials differ")
            identity_name = next(v["configMap"]["name"] for v in pod["volumes"] if v["name"] == "runtime-identity")
            old_identity = json.loads(json.loads(self.kube.run("get", "cm", identity_name, "-o", "json"))["data"]["identity.json"])
            old_roots = next(v["configMap"]["name"] for v in pod["volumes"] if v["name"] == "runtime-trust")
            roots = json.loads(self.kube.run("get", "cm", old_roots, "-o", "json"))
            if roots["data"] != {"ca-bundle.crt": Path(self.config["ca_file"]).read_bytes().decode()}:
                raise ValueError("Source trust material differs")
            documents = self.kube.python(probe.api_pods()[0]["metadata"]["name"], BASELINE_SOURCE)
            if len(documents) != verified.plan.counts.documents:
                raise ValueError("Recovery point document count differs")
            operation = uuid4().hex; stem = "replica-ca-" + operation[:12]
            storage = {o["metadata"]["name"]: o for o in objects if o["kind"] == "StatefulSet"}
            if set(storage) != {self.config["storage_release"] + "-" + r for r in ("postgres", "qdrant", "artifacts")}:
                raise ValueError("Selected storage controllers differ")
            claims = json.loads(self.kube.run("get", "pvc", "-o", "json"))["items"]
            if len(claims) != 3:
                raise ValueError("Selected three retained claims required")
            private = self.path.parent
            self.journal = {"version": 1, "phase": "material-prepared", "fingerprint": self.fingerprint, "namespace_uid": self.value["namespace_uid"],
                "operation_id": operation, "owner_id": "owned-lab-ca-rotation", "old_config": self.config,
                "old_identity": old_identity, "old_identity_name": identity_name, "old_deployments": deployments, "old_secret": old_secret,
                "runtime_secret": runtime_secret, "operator_secret": prior["operator_secret"], "next_identity": stem + "-identity", "inputs": stem + "-inputs",
                "trust_transition": {"previous": old_roots, "selected": stem + "-roots"}, "old_roots": roots,
                "old_storage": storage, "claims": {c["metadata"]["name"]: {"uid": c["metadata"]["uid"], "spec": c["spec"]} for c in claims},
                "material": tls_material(self.config, operation), "new_ca_file": str(private / (stem + ".pem")),
                "new_key_file": str(private / (stem + "-key.pem")), "baseline_documents": documents,
                "backup_id": verified.plan.backup_id, "backup_archive": str(Path(archive).resolve()),
                "prior_rights_operation": prior["operation_id"],
                **{k: prior[k] for k in ("pg_role", "s3_access", "s3_secret", "pg_rights", "s3_rights")}}
            with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=20) as client:
                client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
                self.journal["session_cookie"] = dict(client.cookies)
            self.save()
        if self.journal["phase"] != "material-prepared":
            raise ValueError("Resume the recorded CA phase instead of preparing again")
        for path, value in ((self.journal["new_ca_file"], self.journal["material"]["ca"]), (self.journal["new_key_file"], self.journal["material"]["ca_key"])):
            target = Path(path)
            if target.parent != self.path.parent or target.exists() and target.read_bytes() != value.encode():
                raise ValueError("Refusing to replace CA material")
            if not target.exists():
                with target.open("xb") as stream: stream.write(value.encode())
        self.lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": self.journal["trust_transition"]["selected"]},
            "immutable": True, "data": {"ca-bundle.crt": self.journal["material"]["ca"]}})
        for role, data in self.journal["material"]["leaves"].items():
            self.sealed_secret(self.journal["inputs"].removesuffix("-inputs") + "-" + role, data)
        for role in ("api", "worker"):
            preview = copy.deepcopy(self.journal["old_deployments"][role]); preview["spec"]["replicas"] = 0
            bind_trust_configmap(preview, **self.journal["trust_transition"])
        self.save(phase="secrets-ready")
        emit("ca-material-prepared", operation_id=self.journal["operation_id"], independent_backup_verified=True, runtime_changed=False)

    def claims_unchanged(self):
        self.lab.assert_namespace()
        current = json.loads(self.kube.run("get", "pvc", "-o", "json"))["items"]
        if {c["metadata"]["name"]: {"uid": c["metadata"]["uid"], "spec": c["spec"]} for c in current} != self.journal["claims"]:
            raise ValueError("Retained storage claims changed")

    def storage(self):
        if self.journal["phase"] != "held" or not self.journal.get("vector_inventory"):
            raise ValueError("Held CA operation required")
        self.lab.drained(); self.claims_unchanged()
        for role in ("postgres", "qdrant", "artifacts"):
            name = self.config["storage_release"] + "-" + role
            old = self.journal["old_storage"][name]
            previous = next(v["secret"]["secretName"] for v in old["spec"]["template"]["spec"]["volumes"] if v["name"] == "tls")
            selected = self.journal["inputs"].removesuffix("-inputs") + "-" + role
            current = json.loads(self.kube.run("get", "sts", name, "-o", "json"))
            assert_storage_source(current, old, previous, selected)
            actual = next(v["secret"]["secretName"] for v in current["spec"]["template"]["spec"]["volumes"] if v["name"] == "tls")
            if actual != selected:
                if current["spec"]["replicas"]:
                    self.kube.run("scale", "sts/" + name, "--current-replicas=1", "--replicas=0")
                if self.kube.run("get", "pod", name + "-0", "--ignore-not-found", "-o", "json").strip():
                    self.kube.run("wait", "--for=delete", "pod/" + name + "-0", "--timeout=50s")
                current = json.loads(self.kube.run("get", "sts", name, "-o", "json"))
                staged = stage_storage_tls(current, previous, selected)
                self.claims_unchanged()
                self.kube.run("replace", "-f", "-", source=json.dumps(staged))
            current = json.loads(self.kube.run("get", "sts", name, "-o", "json"))
            if current["spec"]["replicas"] == 0:
                self.kube.run("scale", "sts/" + name, "--current-replicas=0", "--replicas=1")
            self.kube.run("rollout", "status", "sts/" + name, "--timeout=50s")
            self.save(storage_components=sorted(set(self.journal.get("storage_components", [])) | {role}))
        self.claims_unchanged(); self.lab.drained()
        self.save(storage_updated=True)
        emit("ca-storage-updated", application_held=True, next_step="rebind-owned-storage-forwards-then-check-storage")

    def check_storage(self):
        from sqlalchemy import text
        if self.journal["phase"] not in {"held", "computed", "staged"} or self.journal.get("storage_updated") is not True:
            raise ValueError("Updated storage and held application required")
        self.lab.drained(); self.claims_unchanged()
        roots = json.loads(self.kube.run("get", "cm", self.journal["trust_transition"]["selected"], "-o", "json"))
        if roots.get("immutable") is not True or roots.get("data") != {"ca-bundle.crt": self.journal["material"]["ca"]}:
            raise ValueError("The selected root bundle changed")
        for role in ("postgres", "qdrant", "artifacts"):
            name = self.config["storage_release"] + "-" + role
            selected = self.journal["inputs"].removesuffix("-inputs") + "-" + role
            current = json.loads(self.kube.run("get", "sts", name, "-o", "json"))
            old = self.journal["old_storage"][name]
            previous = next(v["secret"]["secretName"] for v in old["spec"]["template"]["spec"]["volumes"] if v["name"] == "tls")
            assert_storage_source(current, old, previous, selected)
            reference = next(v["secret"]["secretName"] for v in current["spec"]["template"]["spec"]["volumes"] if v["name"] == "tls")
            secret = json.loads(self.kube.run("get", "secret", selected, "-o", "json"))
            if (reference != selected or current["spec"]["replicas"] != 1 or secret.get("immutable") is not True
                    or secret.get("data") != self.journal["material"]["leaves"][role]):
                raise ValueError("Storage certificate binding/material changed")
        receipts = {}
        ctx = ssl.create_default_context(cafile=self.journal["new_ca_file"])
        ctx.set_alpn_protocols(["h2", "http/1.1"])
        for role, port in (("qdrant", self.config["ports"]["qdrant"]), ("artifacts", self.config["ports"]["artifacts"]), ("grpc", self.config["ports"]["grpc"])):
            with socket.create_connection(("127.0.0.1", port), timeout=8) as sock, ctx.wrap_socket(sock, server_hostname="127.0.0.1") as channel:
                receipts[role] = sha256(channel.getpeercert(binary_form=True)).hexdigest()
        engine, s3, q = connections(self.new_config())
        try:
            with engine.connect() as connection:
                connection.execute(text("SET TRANSACTION READ ONLY"))
                sql_source(connection, self.new_config())
                state = connection.execute(text("SELECT enabled,epoch,operation_id,owner_id FROM replica_maintenance_state WHERE id=1")).one()
                if tuple(state) != (True, self.journal["epoch"], self.journal["operation_id"], self.journal["owner_id"]) or corpus(connection) != self.journal["corpus"]:
                    raise ValueError("Held corpus/operation changed during certificate replacement")
                artifacts = list(connection.execute(text("SELECT object_id,key,sha256,size FROM artifact_objects WHERE state='ready'")).mappings())
            for artifact in artifacts:
                if artifact["key"] != "objects/" + artifact["object_id"]:
                    raise ValueError("Foreign artifact key")
                response = s3.get_object(Bucket=self.config["bucket"], Key=self.config["prefix"] + artifact["key"])
                checksum, size = sha256(), 0
                try:
                    while block := response["Body"].read(1024 * 1024):
                        checksum.update(block); size += len(block)
                finally:
                    response["Body"].close()
                if checksum.hexdigest() != artifact["sha256"] or size != artifact["size"]:
                    raise ValueError("Artifact bytes changed during certificate replacement")
            if vector_inventory(q, self.config["collection"]) != self.journal["vector_inventory"]:
                raise ValueError("Vector payloads or vectors changed during certificate replacement")
            s3.head_bucket(Bucket=self.config["bucket"])
            q.get("/collections/" + self.config["collection"]).raise_for_status()
        finally:
            engine.dispose(); s3.close(); q.close()
        self.save(storage_receipt={**receipts, "verified_artifacts": len(artifacts), "vectors_unchanged": True})
        emit("ca-new-storage-trust-passed", held_corpus_unchanged=True)

    def compute(self):
        if not self.journal.get("storage_receipt"):
            raise ValueError("Positive new-root storage verification required before identity change")
        self.check_storage()
        super().compute()

    def tls(self):
        if self.journal["phase"] not in {"computed", "staged"}:
            raise ValueError("Computed held CA identity required")
        self.claims_unchanged()
        command = ["python", "-u", "-c", tls_program(self.journal["old_roots"]["data"]["ca-bundle.crt"])]
        name = self.journal.get("tls_job_name", self.journal["inputs"].removesuffix("-inputs") + "-tls")
        self.submit_job(self.tls_job(name, command))
        receipt = self.lab.job_result(name, "REPLICA_CA_TLS:")
        if set(receipt) != {"postgres", "qdrant", "grpc", "artifacts"} or any(r != {"new_ca": True, "old_ca_denied": True, "wrong_hostname_denied": True} for r in receipt.values()):
            raise ValueError("Required native CA/hostname receipt incomplete")
        self.save(tls_receipt=receipt)
        emit("ca-native-tls-checks-passed", components=4, negative_checks=8, app_still_held=True)

    def tls_job(self, name, command):
        api = copy.deepcopy(self.journal["old_deployments"]["api"]); api["spec"]["replicas"] = 0
        api = bind_trust_configmap(api, **self.journal["trust_transition"])
        image = api["spec"]["template"]["spec"]["containers"][0]["image"]
        return operator_job(api, name=name, image=image, runtime=self.journal["runtime_name"], inputs=self.journal["inputs"],
            command=command, new_identity=self.journal["next_identity"])

    def retry_tls(self):
        """Explicit correction of the first diagnostic's absent optional gRPC port, never replay a failed Job."""
        if self.journal["phase"] not in {"computed", "staged"} or self.journal.get("retired_diagnostics"):
            raise ValueError("Only the diagnosed first TLS failure may be retired")
        name = self.journal["inputs"].removesuffix("-inputs") + "-tls"
        previous = json.loads(self.kube.run("get", "job", name, "-o", "json"))
        command = previous["spec"]["template"]["spec"]["containers"][0]["command"]
        self.submit_job(self.tls_job(name, command))  # Exact owned credentials/trust/template; observation only.
        runtime = json.loads(self.kube.run("get", "cm", self.journal["runtime_name"], "-o", "json"))
        logs = self.kube.run("logs", "job/" + name, "--tail=12")
        refused = [json.loads(line) for line in logs.splitlines() if line.startswith('{"tls_refused":')]
        if runtime["data"].get("QDRANT_GRPC_PORT") or len(refused) != 1 or refused[0].get("tls_refused") != "_InactiveRpcError":
            raise ValueError("The diagnosed optional-port failure does not match")
        record = {"name": name, "uid": previous["metadata"]["uid"], "spec_sha256": digest(previous["spec"]),
            "reason": "diagnostic-optional-grpc-port-correction"}
        items = self.lab.inventory(); ready = self.lab.ready_nodes()
        assert_credential_stopped(items, self.journal["old_deployments"])
        self.claims_unchanged()
        validate_drained(filter_retired_diagnostics(items, [record], ready), self.config, ready)
        self.save(retired_diagnostics=[record], tls_job_name=name + "-2")
        emit("ca-failed-diagnostic-retained", previous_job=name, next_job=name + "-2", maintenance_held=True)

    def release(self):
        if not self.journal.get("tls_receipt"):
            raise ValueError("Specific retired-root and hostname refusals required before release")
        self.check_storage()
        super().release()

    def verify(self, report):
        import httpx
        with httpx.Client(base_url=self.value["frontend_url"], cookies=self.journal["session_cookie"], trust_env=False, timeout=20) as client:
            require_retained_session(client.get("/api/auth/me"), "replica.admin")
        fresh_s3 = scoped_s3_probe(self.new_config(), self.journal)
        super().verify(report)
        result = json.loads(Path(report).read_bytes())
        result.update(status="owned-coordinated-ca-rotation-passed", tls=self.journal["tls_receipt"], existing_session_preserved=True,
            pvc_identities_preserved=True, storage_certificate_refs={r: self.journal["inputs"].removesuffix("-inputs") + "-" + r for r in ("postgres", "qdrant", "artifacts")},
            old_ca_sha256=sha256(self.journal["old_roots"]["data"]["ca-bundle.crt"].encode()).hexdigest(), new_ca_sha256=sha256(self.journal["material"]["ca"].encode()).hexdigest(),
            diagnostic_corrections=self.journal.get("retired_diagnostics", []),
            storage_verification=self.journal["storage_receipt"], source_vector_inventory=self.journal["vector_inventory"],
            prior_pg_rights=result.pop("pg_rights"), s3_rights=fresh_s3, prior_rights_operation=self.journal["prior_rights_operation"])
        self.claims_unchanged()
        Path(report).write_text(json.dumps(result, indent=2) + "\n")
        self.save(phase="verified", ca_verified=True, report=str(Path(report).resolve()))
        emit("ca-rotation-functional-pass", preserved_documents=len(self.journal["baseline_documents"]), existing_session_preserved=True)

    def activate(self, config_file):
        if self.journal["phase"] != "verified" or self.journal.get("ca_verified") is not True:
            raise ValueError("Functional CA verification required before operator config activation")
        path = Path(config_file).resolve(strict=True)
        current = json.loads(path.read_bytes())
        if path.parent != self.path.parent or current not in (self.journal["old_config"], self.new_config()):
            raise ValueError("Operator config changed outside this operation")
        pending = path.with_suffix(".ca-pending")
        pending.write_text(json.dumps(self.new_config(), indent=2) + "\n"); pending.replace(path)
        self.save(operator_config_activated=True)
        emit("ca-operator-config-activated", old_ca_retained=True)


def tls_program(old_ca):
    source = '''import json,socket,ssl,tempfile
from pathlib import Path
from urllib.parse import urlsplit
import boto3,grpc,httpx,psycopg
from botocore.config import Config
from qdrant_client import grpc as qgrpc
from sqlalchemy.engine import make_url
from app.deployment.bootstrap import OperatorSettings
from app.deployment.runtime_identity import validate_identity
s=OperatorSettings(_env_file=None);validate_identity(s)
result={}
with tempfile.TemporaryDirectory(dir='/scratch',prefix='ca-probe-') as directory:
 old=Path(directory)/'old.pem';old.write_bytes(OLD_CA.encode())
 new=str(s.artifact_s3_ca_bundle)
 url=make_url(s.db_url).set(drivername='postgresql')
 with psycopg.connect(url.render_as_string(hide_password=False)) as c:assert c.execute('SELECT 1').fetchone()==(1,)
 for kind,dsn in [('old_ca',url.update_query_dict({'sslrootcert':str(old)})),('wrong_hostname',url.set(host='wrong.invalid').update_query_dict({'hostaddr':socket.gethostbyname(url.host)}))]:
  try:
   with psycopg.connect(dsn.render_as_string(hide_password=False)):pass
  except psycopg.OperationalError as exc:
   message=str(exc).lower()
   assert ('certificate verify failed' in message or 'self-signed certificate' in message) if kind=='old_ca' else ('does not match host name' in message or 'hostname mismatch' in message)
  else:raise AssertionError('Forbidden PostgreSQL trust accepted')
 result['postgres']={'new_ca':True,'old_ca_denied':True,'wrong_hostname_denied':True}
 host=urlsplit(s.qdrant_url).hostname
 grpc_port=s.qdrant_grpc_port if s.qdrant_grpc_port is not None else (urlsplit(s.qdrant_url).port or 6333)+1
 with httpx.Client(verify=ssl.create_default_context(cafile=new),trust_env=False,timeout=8,headers={'api-key':s.qdrant_api_key}) as client:
  client.get(s.qdrant_url+'/collections').raise_for_status()
 channel=grpc.secure_channel(host+':'+str(grpc_port),grpc.ssl_channel_credentials(root_certificates=Path(new).read_bytes()))
 qgrpc.CollectionsStub(channel).List(qgrpc.ListCollectionsRequest(),metadata=[('api-key',s.qdrant_api_key)],timeout=8);channel.close()
 client=boto3.client('s3',endpoint_url=s.artifact_s3_endpoint,aws_access_key_id=s.artifact_s3_access_key,aws_secret_access_key=s.artifact_s3_secret_key,
  region_name='us-east-1',verify=new,config=Config(signature_version='s3v4',s3={'addressing_style':'path'},proxies={},connect_timeout=8,read_timeout=8))
 client.head_bucket(Bucket=s.artifact_s3_bucket);client.close()
 for role,hostname,port in [('qdrant',host,urlsplit(s.qdrant_url).port or 443),('grpc',host,grpc_port),('artifacts',urlsplit(s.artifact_s3_endpoint).hostname,urlsplit(s.artifact_s3_endpoint).port or 443)]:
  for kind,root,name in [('old_ca',str(old),hostname),('wrong_hostname',new,'wrong.invalid')]:
   ctx=ssl.create_default_context(cafile=root);ctx.set_alpn_protocols(['h2','http/1.1'])
   try:
    with socket.create_connection((hostname,port),timeout=8) as sock:
     with ctx.wrap_socket(sock,server_hostname=name):pass
   except ssl.SSLCertVerificationError as exc:
    assert exc.verify_code in ({18,19,20,21} if kind=='old_ca' else {62,64})
   else:raise AssertionError('Forbidden storage trust accepted')
  result[role]={'new_ca':True,'old_ca_denied':True,'wrong_hostname_denied':True}
print('REPLICA_CA_TLS:'+json.dumps(result),flush=True)
'''.replace("OLD_CA", repr(old_ca), 1)
    return "import json,traceback\ntry:\n" + textwrap.indent(source, " ") + '''
except Exception as exc:
 print(json.dumps({'tls_refused':type(exc).__name__,'line':traceback.extract_tb(exc.__traceback__)[-1].lineno}),flush=True)
 raise SystemExit(2)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "fence", "storage", "check-storage", "compute", "tls", "retry-tls", "stage", "release", "start", "verify", "activate"])
    for name in ("config", "journal"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--namespace-uid", required=True); parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--archive", type=Path); parser.add_argument("--key-file", type=Path); parser.add_argument("--credential-journal", type=Path)
    parser.add_argument("--report", type=Path); parser.add_argument("--apply", action="store_true", required=True)
    args = parser.parse_args()
    try:
        config = args.config.resolve(strict=True); journal = args.journal.resolve()
        if config.parent != journal.parent or config == journal:
            raise ValueError("Separate protected CA journal required")
        operation = CaRotation(json.loads(config.read_bytes()), args.namespace_uid, journal, args.expected_fingerprint)
        if args.phase == "prepare":
            if not all((args.archive, args.key_file, args.credential_journal)) or args.credential_journal.resolve().parent != config.parent:
                raise ValueError("Explicit backup/key/prior credential journal required")
            operation.prepare(args.archive, args.key_file, args.credential_journal)
        elif args.phase == "verify":
            operation.verify(args.report)
        elif args.phase == "activate":
            operation.activate(config)
        else:
            getattr(operation, args.phase.replace("-", "_"))()
    except Exception as exc:  # noqa: BLE001 -- provider/DSN/key exceptions stay private.
        print(json.dumps({"ca_refused": type(exc).__name__, "locations": [Path(f.filename).name + ":" + str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
