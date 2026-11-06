"""A trust change must preserve data, identities, privileges and held writers."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_ca_rotation.py"
    assert path.is_file(), "Coordinated CA rotation is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("ca_rotation_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_new_certificates_bind_each_owned_service_and_independent_root():
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import padding
    tool = module()
    config = {"namespace": "replica-test-fixture01", "storage_release": "storage"}
    material = tool.tls_material(config, "a" * 32)
    ca = x509.load_pem_x509_certificate(material["ca"].encode())
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert set(material["leaves"]) == {"postgres", "qdrant", "artifacts"}
    for role, leaf in material["leaves"].items():
        certificate = x509.load_pem_x509_certificate(base64.b64decode(leaf["tls.crt"]))
        ca.public_key().verify(certificate.signature, certificate.tbs_certificate_bytes, padding.PKCS1v15(), certificate.signature_hash_algorithm)
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        assert f"storage-{role}.replica-test-fixture01.svc" in san.get_values_for_type(x509.DNSName)
        assert str(san.get_values_for_type(x509.IPAddress)[0]) == "127.0.0.1"
        assert not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        assert certificate.issuer == ca.subject


def storage():
    return {"metadata": {"uid": "owned", "resourceVersion": "14"}, "spec": {"replicas": 0, "template": {"spec": {
        "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": "retained"}},
                    {"name": "tls", "secret": {"secretName": "old", "defaultMode": 288}}],
        "containers": [{"image": "pinned", "env": [{"name": "PASSWORD", "valueFrom": {"secretKeyRef": {"name": "auth", "key": "password"}}}],
                        "volumeMounts": [{"name": "data", "mountPath": "/data"}, {"name": "tls", "mountPath": "/run/tls", "readOnly": True}]}]}}}}


def test_storage_cert_change_never_alters_pvc_image_or_credentials():
    tool = module()
    original = storage()
    changed = tool.stage_storage_tls(original, "old", "new")
    expected = copy.deepcopy(original)
    expected["spec"]["template"]["spec"]["volumes"][1]["secret"]["secretName"] = "new"
    assert changed == expected and original == storage()
    assert tool.stage_storage_tls(changed, "old", "new") == changed


@pytest.mark.parametrize("fault", ["running", "foreign", "writable"])
def test_storage_cert_change_refuses_unstopped_or_unbound_inputs(fault):
    tool = module()
    value = storage()
    if fault == "running":
        value["spec"]["replicas"] = 1
    elif fault == "foreign":
        value["spec"]["template"]["spec"]["volumes"][1]["secret"]["secretName"] = "foreign"
    else:
        value["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][1]["readOnly"] = False
    with pytest.raises(ValueError):
        tool.stage_storage_tls(value, "old", "new")


@pytest.mark.parametrize("selected", ["old", "new"])
def test_resume_accepts_only_the_original_storage_with_old_or_new_certificate(selected):
    tool = module()
    original = storage()
    current = copy.deepcopy(original)
    current["spec"]["replicas"] = 1
    current["spec"]["template"]["spec"]["volumes"][1]["secret"]["secretName"] = selected
    tool.assert_storage_source(current, original, "old", "new")
    current["spec"]["template"]["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "different-data"
    with pytest.raises(ValueError):
        tool.assert_storage_source(current, original, "old", "new")


def test_tls_probe_is_compilable_and_cannot_release_or_use_operator_credentials():
    tool = module()
    source = tool.tls_program("public-root")
    compile(source, "tls-probe", "exec")
    assert "verify_code" in source and "ListCollectionsRequest" in source
    assert "leave_maintenance" not in source and "/run/operator-db" not in source


@pytest.mark.parametrize("configured,expected", [(None, 6334), (7001, 7001)])
def test_tls_probe_resolves_optional_grpc_port_like_runtime(configured, expected):
    import ast
    tool = module()
    tree = ast.parse(tool.tls_program("public-root"))
    assignment = next((node for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "grpc_port" for target in node.targets)), None)
    assert assignment is not None, "Native diagnostic must resolve the optional gRPC port"
    from urllib.parse import urlsplit
    expression = ast.Expression(assignment.value)
    assert eval(compile(expression, "grpc-port", "eval"), {"s": SimpleNamespace(qdrant_grpc_port=configured,
        qdrant_url="https://storage-qdrant:6333"), "urlsplit": urlsplit}) == expected


@pytest.mark.parametrize("fault", [None, "uid", "changed_spec", "active", "running_pod", "unready_node"])
def test_failed_diagnostic_remains_failed_and_only_its_verified_stopped_resources_are_filtered(fault):
    tool = module()
    job = {"kind": "Job", "metadata": {"name": "tls", "uid": "job"}, "spec": {"backoffLimit": 0},
        "status": {"failed": 1, "conditions": [{"type": "Failed", "status": "True"}]}}
    pod = {"kind": "Pod", "metadata": {"name": "tls-1", "uid": "pod", "ownerReferences": [{"kind": "Job", "uid": "job", "controller": True}]},
        "spec": {"nodeName": "node"}, "status": {"phase": "Failed", "containerStatuses": [{"state": {"terminated": {"exitCode": 2}}}]}}
    record = {"name": "tls", "uid": "job", "spec_sha256": tool.digest(job["spec"])}
    ready = {"node": True}
    if fault == "uid": job["metadata"]["uid"] = "other"
    elif fault == "changed_spec": job["spec"]["backoffLimit"] = 1
    elif fault == "active": job["status"]["active"] = 1
    elif fault == "running_pod": pod["status"]["phase"] = "Running"
    elif fault == "unready_node": ready["node"] = False
    unrelated = {"kind": "Job", "metadata": {"name": "unknown", "uid": "other"}}
    original = copy.deepcopy([job, pod, unrelated])
    if fault:
        with pytest.raises(ValueError): tool.filter_retired_diagnostics([job, pod, unrelated], [record], ready)
    else:
        assert tool.filter_retired_diagnostics([job, pod, unrelated], [record], ready) == [unrelated]
    assert [job, pod, unrelated] == original


def test_vector_inventory_includes_vectors_and_ignores_only_record_order():
    tool = module()
    records = [{"id": 1, "payload": {"doc_id": "a"}, "vector": {"dense": [0.1, 0.2]}},
               {"id": 2, "payload": {"doc_id": "b"}, "vector": {"dense": [0.3, 0.4]}}]
    class Client:
        def __init__(self, values): self.values = values
        def post(self, path, *, json):
            assert path == "/collections/owned/points/scroll" and json["with_vector"] is True and json["with_payload"] is True
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"result": {"points": self.values, "next_page_offset": None}})
    baseline = tool.vector_inventory(Client(records), "owned")
    assert baseline == tool.vector_inventory(Client(list(reversed(records))), "owned")
    changed = copy.deepcopy(records); changed[1]["vector"]["dense"][1] = 0.9
    assert baseline != tool.vector_inventory(Client(changed), "owned")


@pytest.mark.parametrize("fault", [None, "anonymous", "wrong_mode", "missing_role"])
def test_retained_session_requires_the_same_authenticated_admin_not_only_http_200(fault):
    tool = module()
    body = {"mode": "simulation", "user": {"username": "replica.admin", "roles": ["admin"]}}
    if fault == "anonymous": body["user"]["username"] = "anonymous"
    elif fault == "wrong_mode": body["mode"] = "disabled"
    elif fault == "missing_role": body["user"]["roles"] = []
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
    if fault:
        with pytest.raises(ValueError): tool.require_retained_session(response, "replica.admin")
    else:
        tool.require_retained_session(response, "replica.admin")


def test_operator_ca_activation_refuses_parent_verification_without_complete_ca_receipt(tmp_path):
    tool = module()
    operation = object.__new__(tool.CaRotation)
    operation.journal = {"phase": "verified"}
    with pytest.raises(ValueError):
        operation.activate(tmp_path / "operator.json")


@pytest.mark.parametrize("fault", [None, "incomplete", "stale", "installation", "fingerprint"])
def test_rotation_requires_matching_verified_recovery_point(fault):
    tool = module()
    now = datetime.now(timezone.utc)
    config = {"installation_id": "5" * 32, "namespace": "replica-test-fixture01"}
    plan = SimpleNamespace(source=SimpleNamespace(installation_id="5" * 32, namespace=config["namespace"], consistent_at=now),
        model_vector_sha256=tool.digest("a" * 64), schema_revision="rpl05_maintenance_epoch")
    manifest = SimpleNamespace(status="complete", plan=plan)
    if fault == "incomplete":
        manifest.status = "incomplete"
    elif fault == "stale":
        plan.source.consistent_at -= timedelta(days=2)
    elif fault == "installation":
        plan.source.installation_id = "4" * 32
    elif fault == "fingerprint":
        plan.model_vector_sha256 = "0" * 64
    if fault:
        with pytest.raises(ValueError):
            tool.require_recovery_point(manifest, config, "a" * 64, now=now)
    else:
        tool.require_recovery_point(manifest, config, "a" * 64, now=now)
