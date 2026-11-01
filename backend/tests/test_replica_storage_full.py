"""Native ENOSPC faults must be confined to a bounded private memory filesystem."""
import copy
import importlib.util
from pathlib import Path
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_storage_full.py"
    assert path.is_file(), "Bounded native storage-full acceptance is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("storage_full_test", path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize("fault", [None, "disk", "unbounded", "wrong_path", "duplicate_mount", "read_only"])
def test_filler_requires_exact_bounded_writable_memory_mount(fault):
    tool = module()
    mount = "42 10 0:30 / /full rw,nosuid,nodev - tmpfs tmpfs rw,size=131072k"
    capacity = 128 * 1024 * 1024
    if fault == "disk": mount = mount.replace("tmpfs tmpfs", "ext4 /dev/sda")
    elif fault == "unbounded": capacity *= 2
    elif fault == "wrong_path": mount = mount.replace("/full", "/data")
    elif fault == "duplicate_mount": mount += "\n" + mount
    elif fault == "read_only": mount = mount.replace("/full rw,", "/full ro,")
    if fault:
        with pytest.raises(ValueError): tool.require_bounded_tmpfs(mount, capacity)
    else:
        tool.require_bounded_tmpfs(mount, capacity)


def test_bounded_filler_counts_partial_writes_and_requires_actual_enospc():
    import errno
    tool = module()
    remaining = 7777
    def write(block):
        nonlocal remaining
        if not remaining: raise OSError(errno.ENOSPC, "bounded full")
        size = min(remaining, len(block), 1000); remaining -= size
        return size
    assert tool.write_bounded(write, 8192) == 7777
    with pytest.raises(ValueError): tool.write_bounded(lambda data: len(data), 8192)


def test_bounded_filler_refuses_stalled_io_instead_of_looping():
    tool = module()
    with pytest.raises(ValueError): tool.write_bounded(lambda _data: 0, 8192)


def test_probe_manifest_has_no_persistent_or_host_storage_or_external_network_identity():
    tool = module()
    pod = tool.probe_pod("replica-test-full20261102", "a" * 32)
    spec = pod["spec"]
    assert spec["automountServiceAccountToken"] is False
    assert not spec.get("hostNetwork") and not spec.get("hostPID") and not spec.get("hostIPC")
    for volume in spec["volumes"]:
        assert set(volume) <= {"name", "emptyDir", "secret", "configMap"}
        if "emptyDir" in volume: assert volume["emptyDir"]["medium"] == "Memory"
    data = next(v for v in spec["volumes"] if v["name"] == "data")
    assert data["emptyDir"]["sizeLimit"] == "128Mi"
    probe = next(c for c in spec["containers"] if c["name"] == "probe")
    assert not any(m["name"] == "tls" for m in probe["volumeMounts"])
    for container in spec["containers"]:
        assert container["securityContext"]["readOnlyRootFilesystem"] is True
        assert container["securityContext"]["allowPrivilegeEscalation"] is False
        assert container["resources"]["limits"]["memory"] == "512Mi"


@pytest.mark.parametrize("fault", [None, "uid", "spec", "image", "restart", "unready", "foreign_label"])
def test_fault_requires_the_original_ready_unchanged_pod(fault):
    tool = module()
    pod = tool.probe_pod("replica-test-full20261102", "a" * 32)
    pod["metadata"]["uid"] = "original"
    pod["status"] = {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
        "containerStatuses": [{"name": c["name"], "restartCount": 0, "ready": True, "imageID": "immutable-" + c["name"]} for c in pod["spec"]["containers"]]}
    expected = copy.deepcopy(pod)
    if fault == "uid": pod["metadata"]["uid"] = "other"
    elif fault == "spec": pod["spec"]["volumes"][0] = {"name": "data", "hostPath": {"path": "/"}}
    elif fault == "restart": pod["status"]["containerStatuses"][0]["restartCount"] = 1
    elif fault == "image": pod["status"]["containerStatuses"][0]["imageID"] = "other-build"
    elif fault == "unready": pod["status"]["conditions"][0]["status"] = "False"
    elif fault == "foreign_label": pod["metadata"]["labels"]["okf-operation-id"] = "foreign"
    if fault:
        with pytest.raises(ValueError): tool.require_original_pod(pod, expected)
    else:
        tool.require_original_pod(pod, expected)


def test_completed_native_receipt_is_observed_instead_of_injecting_again(tmp_path):
    tool = module()
    calls = []
    def execute(): calls.append(1); return {"refused": True}
    assert tool.run_cached(tmp_path, "a" * 32, "check-single", "b" * 64, execute) == {"refused": True}
    assert tool.run_cached(tmp_path, "a" * 32, "check-single", "b" * 64, execute) == {"refused": True}
    assert calls == [1]
    with pytest.raises(ValueError): tool.run_cached(tmp_path, "a" * 32, "check-single", "c" * 64, execute)


def test_unknown_native_outcome_never_repeats_the_request(tmp_path):
    tool = module()
    def lost(): raise OSError("lost observation")
    with pytest.raises(OSError): tool.run_cached(tmp_path, "a" * 32, "fill", "b" * 64, lost)
    with pytest.raises(ValueError): tool.run_cached(tmp_path, "a" * 32, "fill", "b" * 64, lambda: pytest.fail("Must inspect the first attempt"))


def test_native_fault_scope_keeps_namespace_test_purpose_run_and_owner():
    import json
    from types import SimpleNamespace
    tool = module()
    operation = object.__new__(tool.StorageFull)
    operation.journal = {"namespace_uid": "owned", "operation_id": "a" * 32}
    value = {"metadata": {"uid": "owned", "labels": {"okf-operation-id": "a" * 32,
        "okf-purpose": "replica-test", "okf-run-id": "full20261102", "app.kubernetes.io/managed-by": "okf-replica-acceptance"}}}
    operation.kube = SimpleNamespace(run=lambda *_args: json.dumps(value))
    operation.namespace()
    for key in ("okf-purpose", "okf-run-id", "app.kubernetes.io/managed-by"):
        previous = value["metadata"]["labels"][key]
        value["metadata"]["labels"][key] = "foreign"
        with pytest.raises(ValueError): operation.namespace()
        value["metadata"]["labels"][key] = previous


@pytest.mark.parametrize("fault", [None, "missing_enospc", "acknowledged", "lost_seed", "pending_uploads", "not_recovered"])
def test_report_requires_actual_refusal_integrity_and_recovery_and_redacts_private_inputs(fault):
    tool = module()
    journal = {"phase": "verified", "operation_id": "a" * 32, "namespace_uid": "owned-namespace", "pod_uid": "owned-pod",
        "images": {"artifacts": "rustfs-immutable", "probe": "backend-immutable"}, "secret": "CANARY_CREDENTIAL", "material": {"key": "CANARY_PRIVATE_KEY"},
        "full_receipt": {"capacity": 128 * 1024 * 1024, "errno": 28, "free_bytes": 0},
        "single_refusal": {"acknowledged": False, "existing_bytes_unchanged": True, "failure": "BlobUnavailable", "protocol": [{"status": 500, "code": "InternalError"}]},
        "multipart_refusal": {"acknowledged": False, "existing_bytes_unchanged": True, "failure": "BlobUnavailable", "protocol": [{"status": 500, "code": "InternalError"}]},
        "recovery_receipt": {"filler_removed": True, "free_bytes": 100 * 1024 * 1024},
        "verification": {"seed_unchanged": True, "same_ids_retried": True, "conditional_replay": True, "verified_range": True, "pending_multipart_uploads": 0}}
    if fault == "missing_enospc": journal["full_receipt"]["errno"] = None
    elif fault == "acknowledged": journal["single_refusal"]["acknowledged"] = True
    elif fault == "lost_seed": journal["verification"]["seed_unchanged"] = False
    elif fault == "pending_uploads": journal["verification"]["pending_multipart_uploads"] = 1
    elif fault == "not_recovered": journal["recovery_receipt"]["filler_removed"] = False
    if fault:
        with pytest.raises(ValueError): tool.full_report(journal)
    else:
        report = tool.full_report(journal)
        import json
        assert "CANARY" not in json.dumps(report)
        assert report["provider_capacity_error_classification"] == "opaque-server-error"
        assert report["physical_disk_or_pvc_quota_certified"] is False
