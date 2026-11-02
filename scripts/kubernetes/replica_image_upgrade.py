"""Phase-controlled same-schema image rollout after scoped credentials/CA rotation.

Only the explicitly owned synthetic managed-S3 lab is supported. Privileged SQL
authority remains a file in offline bootstrap Jobs, outside runtime Settings.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replica_backup import connections, sql_source
from replica_ca_rotation import (
    CaLab,
    require_recovery_point,
    require_retained_session,
    vector_inventory,
)
from replica_credentials import (
    ReplicaCredentials,
    bind_runtime_secret,
    credential_job,
    credential_secret_data,
)
from replica_upgrade import corpus, stage_role
from replica_worker_loss import BASELINE_SOURCE, emit
from test_scripts.replica_runtime_acceptance import validate_inventory
from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent


def assert_pinned_input(current, expected):
    if (current.get("immutable") is not True or expected.get("immutable") is not True
            or any(current.get(k) != expected.get(k) for k in ("kind", "data", "binaryData", "type"))
            or any(current["metadata"].get(k) != expected["metadata"].get(k)
                   for k in ("name", "namespace", "uid", "labels", "annotations"))):
        raise ValueError("Pinned immutable upgrade input changed")


def _runtime_secret(api, expected, operator):
    pod = api["spec"]["template"]["spec"]
    containers = pod["containers"]
    if (len(containers) != 1 or expected == operator
            or [v["secretRef"]["name"] for v in containers[0].get("envFrom", []) if "secretRef" in v] != [expected]
            or operator in json.dumps(pod)):
        raise ValueError("Scoped runtime and offline operator bindings differ")
    stopped = copy.deepcopy(api)
    stopped["spec"]["replicas"] = 0
    bind_runtime_secret(stopped, expected, expected)  # Existing inline/extra credential guards.


def stage_image_role(value, *, baseline, runtime, identity, image, runtime_secret, operator_secret):
    _runtime_secret(baseline, runtime_secret, operator_secret)
    stopped = copy.deepcopy(baseline)
    stopped["spec"]["replicas"] = 0
    pod = stopped["spec"]["template"]["spec"]
    old_runtime = [v["configMapRef"]["name"] for v in pod["containers"][0]["envFrom"] if "configMapRef" in v]
    old_identity = [v["configMap"]["name"] for v in pod["volumes"] if v["name"] == "runtime-identity"]
    if len(old_runtime) != 1 or len(old_identity) != 1:
        raise ValueError("One bound source runtime and identity required")
    selected = stage_role(stopped, old_runtime=old_runtime[0], new_runtime=runtime,
        old_identity=old_identity[0], new_identity=identity, image=image)
    if (value["metadata"]["uid"] != baseline["metadata"]["uid"]
            or value["spec"] not in (stopped["spec"], selected["spec"])):
        raise ValueError("Image upgrade source template changed or role is running")
    result = copy.deepcopy(value)
    result["spec"] = selected["spec"]
    result.pop("status", None)
    result["metadata"].pop("managedFields", None)
    return result


def image_operator_job(api, *, image, runtime_secret, operator_secret, **kwargs):
    _runtime_secret(api, runtime_secret, operator_secret)
    source = copy.deepcopy(api)
    source["spec"]["template"]["spec"]["containers"][0]["image"] = image
    return credential_job(source, runtime_secret=runtime_secret, operator_secret=operator_secret, **kwargs)


class ImageUpgrade(ReplicaCredentials):
    def __init__(self, config, namespace_uid, journal, expected_fingerprint):
        super().__init__(config, namespace_uid, journal, expected_fingerprint)
        self.lab = CaLab(config, namespace_uid, self)
        if self.journal and self.journal.get("operation_kind") != "same-schema-image-upgrade":
            raise ValueError("An image-upgrade journal is required")

    def resource(self, kind, name):
        return json.loads(self.kube.run("get", kind, name, "-o", "json"))

    def check_inputs(self):
        self.lab.assert_namespace()
        for key, expected in self.journal["pinned_inputs"].items():
            kind, name = key.split("/", 1)
            assert_pinned_input(self.resource(kind, name), expected)
        claims = json.loads(self.kube.run("get", "pvc", "-o", "json"))["items"]
        if {c["metadata"]["name"]: {"uid": c["metadata"]["uid"], "spec": c["spec"]} for c in claims} != self.journal["claims"]:
            raise ValueError("Retained upgrade claims changed")
        for name, expected in self.journal["old_storage"].items():
            current = self.resource("sts", name)
            if current["metadata"]["uid"] != expected["metadata"]["uid"] or current["spec"] != expected["spec"]:
                raise ValueError("Storage controller changed during image-only upgrade")

    def check_roles(self, *, staged, allow_running=False):
        for role, baseline in self.journal["old_deployments"].items():
            current = self.resource("deploy", role)
            count = current["spec"]["replicas"]
            if count not in ({0, 1 if role == "model-stub" else 2} if allow_running else {0}):
                raise ValueError("Expected stopped/staged upgrade role count differs")
            stopped = copy.deepcopy(current)
            stopped["spec"]["replicas"] = 0
            if role in {"api", "worker"}:
                selected = stage_image_role(stopped, baseline=baseline, runtime=self.journal["next_runtime"],
                    identity=self.journal["next_identity"], image=self.journal["image"],
                    runtime_secret=self.journal["runtime_secret"], operator_secret=self.journal["operator_secret"])
                if staged and stopped["spec"] != selected["spec"]:
                    raise ValueError("Candidate image/config/identity must be staged together")
            else:
                expected = copy.deepcopy(baseline["spec"])
                expected["replicas"] = 0
                if current["metadata"]["uid"] != baseline["metadata"]["uid"] or stopped["spec"] != expected:
                    raise ValueError("Frontend/provider changed during backend image upgrade")

    def prepare(self, archive, key_file, credential_journal, source_journal, image):
        import httpx
        from app.deployment.replica_backup import verify_backup
        if self.journal is not None:
            if self.journal.get("image") != image:
                raise ValueError("Recorded candidate image differs")
            self.check_inputs()
            return
        if not re.fullmatch(r"okf-backend:replicas-[0-9a-f]{7}-[0-9a-f]{12}", image):
            raise ValueError("Explicit owned candidate image required")
        verified = verify_backup(archive, Path(key_file).read_bytes())
        require_recovery_point(verified, self.config, self.fingerprint, now=datetime.now(timezone.utc))
        if verified.plan.source.failure_domain == verified.plan.destination.failure_domain:
            raise ValueError("Independent backup required")
        credentials = json.loads(Path(credential_journal).read_bytes())
        prior = json.loads(Path(source_journal).read_bytes())
        if (credentials.get("phase") != "verified" or prior.get("phase") != "verified"
                or credentials["namespace_uid"] != self.value["namespace_uid"] or prior["namespace_uid"] != self.value["namespace_uid"]
                or prior["new_identity"]["runtime_fingerprint"] != self.fingerprint
                or prior["new_identity"]["installation_id"] != self.config["installation_id"]
                or prior["runtime_secret"] != credentials["runtime_secret"] or prior["operator_secret"] != credentials["operator_secret"]):
            raise ValueError("Verified credential/source provenance differs")
        # Preserve the prior exact terminal diagnostic exception, using its
        # existing UID/spec/process/Ready-node verifier; never ignore failures.
        self.journal = {"retired_diagnostics": prior.get("retired_diagnostics", [])}
        probe = FaultProbe(self.kube, self.value)
        require_quiescent(probe)
        if probe.identity["fingerprint"] != self.fingerprint:
            raise ValueError("Source runtime changed")
        objects = self.lab.inventory()
        deployments = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Deployment"}
        ready = validate_inventory(self.kube.inventory(), self.value)
        if set(deployments) != {"api", "worker", "frontend", "model-stub"} or dict(verified.plan.images) != ready["images"]:
            raise ValueError("Source workloads or recovery point images differ")
        api = deployments["api"]
        if api["spec"]["template"]["spec"]["containers"][0]["image"] == image:
            raise ValueError("Candidate image must change")
        _runtime_secret(api, credentials["runtime_secret"], credentials["operator_secret"])
        runtime_secret = self.resource("secret", credentials["runtime_secret"])
        operator = self.resource("secret", credentials["operator_secret"])
        expected_runtime, expected_operator = credential_secret_data(credentials["old_secret"]["data"],
            credentials["new_db_runtime_url"], credentials["s3_access"], credentials["s3_secret"])
        if (runtime_secret["metadata"]["uid"] != credentials["runtime_secret_uid"]
                or operator["metadata"]["uid"] != credentials["operator_secret_uid"]
                or runtime_secret.get("data") != expected_runtime or operator.get("data") != expected_operator):
            raise ValueError("Existing runtime/operator Secret identity changed")
        pod = api["spec"]["template"]["spec"]
        runtime_name = next(v["configMapRef"]["name"] for v in pod["containers"][0]["envFrom"] if "configMapRef" in v)
        identity_name = next(v["configMap"]["name"] for v in pod["volumes"] if v["name"] == "runtime-identity")
        pinned = {"secret/" + obj["metadata"]["name"]: obj for obj in (runtime_secret, operator)}
        for volume in pod["volumes"]:
            if "configMap" in volume:
                name = volume["configMap"]["name"]
                pinned["cm/" + name] = self.resource("cm", name)
        pinned["cm/" + runtime_name] = self.resource("cm", runtime_name)
        old_identity = json.loads(pinned["cm/" + identity_name]["data"]["identity.json"])
        if old_identity != prior["new_identity"]:
            raise ValueError("Installed identity differs from source journal")
        storage = {o["metadata"]["name"]: o for o in objects if o["kind"] == "StatefulSet"}
        if set(storage) != {self.config["storage_release"] + "-" + r for r in ("postgres", "qdrant", "artifacts")}:
            raise ValueError("Selected storage controllers differ")
        for controller in storage.values():
            for volume in controller["spec"]["template"]["spec"]["volumes"]:
                if volume["name"] == "tls":
                    name = volume["secret"]["secretName"]
                    pinned["secret/" + name] = self.resource("secret", name)
        for resource in pinned.values():
            assert_pinned_input(resource, resource)
        documents = self.kube.python(probe.api_pods()[0]["metadata"]["name"], BASELINE_SOURCE)
        if not documents or any(o["spec"]["replicas"] != (1 if r == "model-stub" else 2) for r, o in deployments.items()):
            raise ValueError("Nonempty quiescent corpus and expected role counts required")
        operation = uuid4().hex
        stem = "replica-image-" + operation[:12]
        engine, s3, q = connections(self.config)
        try:
            source_vectors = vector_inventory(q, self.config["collection"])
            if not source_vectors["count"]:
                raise ValueError("Nonempty source vector corpus required")
        finally:
            engine.dispose(); s3.close(); q.close()
        data = {**pinned["cm/" + runtime_name]["data"], "RUNTIME_IMAGE_ID": image}
        self.lab.write({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": stem + "-runtime"}, "immutable": True, "data": data})
        pinned["cm/" + stem + "-runtime"] = self.resource("cm", stem + "-runtime")
        claims = json.loads(self.kube.run("get", "pvc", "-o", "json"))["items"]
        if len(claims) != 3:
            raise ValueError("Three retained claims required")
        self.journal = {"version": 1, "phase": "secrets-ready", "operation_kind": "same-schema-image-upgrade",
            "fingerprint": self.fingerprint, "namespace_uid": self.value["namespace_uid"], "operation_id": operation,
            "owner_id": "owned-lab-image-upgrade", "image": image, "old_identity": old_identity,
            "old_identity_name": identity_name, "old_deployments": deployments, "old_storage": storage,
            "old_secret": runtime_secret, "runtime_secret": credentials["runtime_secret"], "operator_secret": credentials["operator_secret"],
            "next_runtime": stem + "-runtime", "next_identity": stem + "-identity", "inputs": stem + "-inputs",
            "runtime_name": runtime_name, "pinned_inputs": pinned, "baseline_documents": documents,
            "claims": {c["metadata"]["name"]: {"uid": c["metadata"]["uid"], "spec": c["spec"]} for c in claims},
            "retired_diagnostics": prior.get("retired_diagnostics", []), "backup_id": verified.plan.backup_id,
            "backup_archive": str(Path(archive).resolve()), "prior_rights_operation": credentials["operation_id"],
            "vector_inventory": source_vectors,
            **{k: credentials[k] for k in ("pg_role", "s3_access", "s3_secret", "pg_rights", "s3_rights")}}
        with httpx.Client(base_url=self.value["frontend_url"], trust_env=False, timeout=20) as client:
            client.post("/api/auth/simulate", json={"username": "replica.admin"}).raise_for_status()
            self.journal["session_cookie"] = dict(client.cookies)
        self.save()
        self.check_inputs()
        emit("image-upgrade-prepared", image=image, independent_backup_verified=True, runtime_changed=False)

    def fence(self):
        self.check_inputs()
        if self.journal["phase"] != "held":
            super().fence()
        self.check_held()

    def check_held(self):
        from hashlib import sha256

        from app.services.work.maintenance_control import MaintenanceOperator
        from sqlalchemy import text
        self.check_inputs()
        self.lab.drained()
        self.check_roles(staged=False)
        engine, s3, q = connections(self.config)
        try:
            with MaintenanceOperator(engine, operation_id=self.journal["operation_id"], owner_id=self.journal["owner_id"]) as op, op.transaction() as session:
                identity, _ = sql_source(session.connection(), self.config)
                state = session.execute(text("SELECT enabled,operation_id,owner_id,epoch FROM replica_maintenance_state WHERE id=1")).one()
                if (tuple(state) != (True, self.journal["operation_id"], self.journal["owner_id"], self.journal["epoch"])
                        or identity["fingerprint"] not in {self.fingerprint, self.journal.get("new_identity", {}).get("runtime_fingerprint")}
                        or corpus(session.connection()) != self.journal["corpus"]
                        or session.scalar(text("SELECT count(*) FROM replica_capacity_slots WHERE resource='llm' AND owner_id IS NOT NULL"))):
                    raise ValueError("Held image upgrade installation changed")
                artifacts = list(session.execute(text("SELECT object_id,key,sha256,size FROM artifact_objects WHERE state='ready' LIMIT 10001")).mappings())
            if len(artifacts) > 10000 or sum(row["size"] for row in artifacts) > 1024 * 1024 * 1024:
                raise ValueError("Owned artifact verification bound exceeded")
            for artifact in artifacts:
                if artifact["key"] != "objects/" + artifact["object_id"]:
                    raise ValueError("Foreign artifact key")
                response = s3.get_object(Bucket=self.config["bucket"], Key=self.config["prefix"] + artifact["key"])
                checksum, size = sha256(), 0
                try:
                    while block := response["Body"].read(1024 * 1024):
                        checksum.update(block); size += len(block)
                        if size > artifact["size"]:
                            raise ValueError("Artifact exceeds its recorded size")
                finally:
                    response["Body"].close()
                if checksum.hexdigest() != artifact["sha256"] or size != artifact["size"]:
                    raise ValueError("Image-only upgrade artifact integrity changed")
            if vector_inventory(q, self.config["collection"]) != self.journal["vector_inventory"]:
                raise ValueError("Image-only upgrade changed vector payloads or vectors")
            self.save(verified_artifacts=len(artifacts))
        finally:
            engine.dispose(); s3.close(); q.close()

    def job(self, name, command, *, identity=None):
        self.check_inputs()
        desired = image_operator_job(self.journal["old_deployments"]["api"], name=name,
            image=self.journal["image"], runtime=self.journal["next_runtime"], inputs=self.journal["inputs"],
            runtime_secret=self.journal["runtime_secret"], operator_secret=self.journal["operator_secret"], command=command,
            new_identity=identity)
        self.submit_job(desired)

    def compute(self):
        self.check_held()
        super().compute()
        name = self.journal["next_identity"]
        self.journal["pinned_inputs"]["cm/" + name] = self.resource("cm", name)
        self.save()
        self.check_held()

    def stage(self):
        if self.journal["phase"] not in {"computed", "staged"}:
            raise ValueError("Computed image identity required")
        self.check_held()
        for role in ("api", "worker"):
            current = self.resource("deploy", role)
            selected = stage_image_role(current, baseline=self.journal["old_deployments"][role],
                runtime=self.journal["next_runtime"], identity=self.journal["next_identity"], image=self.journal["image"],
                runtime_secret=self.journal["runtime_secret"], operator_secret=self.journal["operator_secret"])
            self.lab.write(selected, verb="replace")
        self.save(phase="staged")

    def release(self):
        self.check_inputs()
        self.check_roles(staged=True)
        name = self.journal["inputs"].removesuffix("-inputs") + "-release"
        # A lost response after successful release must observe the SAME Job,
        # never demand re-entry into maintenance or submit a replacement Job.
        existing = self.kube.run("get", "job", name, "--ignore-not-found", "-o", "json")
        if not existing.strip():
            self.check_held()
        super().release()

    def start(self):
        self.check_inputs()
        self.check_roles(staged=True, allow_running=True)
        super().start()
        self.check_roles(staged=True, allow_running=True)

    def verify(self, report):
        import httpx
        self.check_inputs()
        self.check_roles(staged=True, allow_running=True)
        with httpx.Client(base_url=self.value["frontend_url"], cookies=self.journal["session_cookie"], trust_env=False, timeout=20) as client:
            require_retained_session(client.get("/api/auth/me"), "replica.admin")
        super().verify(report)
        result = json.loads(Path(report).read_bytes())
        result.update(status="owned-scoped-same-schema-image-upgrade-passed", image=self.journal["image"],
            existing_session_preserved=True, runtime_secret_unchanged=True, storage_claims_and_controllers_unchanged=True,
            held_vectors_unchanged=True, independent_backup_id=self.journal["backup_id"],
            rights_denials_reused_from_operation=self.journal["prior_rights_operation"], runtime_configmap=self.journal["next_runtime"])
        Path(report).write_text(json.dumps(result, indent=2) + "\n")
        self.save(image_upgrade_verified=True)
        emit("scoped-image-upgrade-verified", image=self.journal["image"], preserved_existing_documents=len(self.journal["baseline_documents"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--namespace-uid", required=True)
    parser.add_argument("--expected-fingerprint", required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", "fence", "compute", "stage", "release", "start", "verify"), required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--credential-journal", type=Path)
    parser.add_argument("--source-journal", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        config_path = args.config.resolve(strict=True)
        if args.journal.resolve().parent != config_path.parent:
            raise ValueError("Protected operation journal must share the owned config directory")
        operation = ImageUpgrade(json.loads(config_path.read_bytes()), args.namespace_uid, args.journal, args.expected_fingerprint)
        if args.phase == "prepare":
            if not all((args.archive, args.key_file, args.credential_journal, args.source_journal, args.image)):
                raise ValueError("Explicit backup/key/credential/source/image inputs required")
            operation.prepare(args.archive, args.key_file, args.credential_journal, args.source_journal, args.image)
        elif args.phase == "verify":
            operation.verify(args.report)
        else:
            getattr(operation, args.phase)()
    except Exception as exc:  # noqa: BLE001 -- provider/DSN/key errors are private.
        print(json.dumps({"image_upgrade_refused": type(exc).__name__,
            "locations": [Path(f.filename).name + ":" + str(f.lineno) for f in traceback.extract_tb(exc.__traceback__)[-3:]]}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
