"""Every independent runtime identity must be checked inside the fault budget."""
import threading
from types import SimpleNamespace

import pytest

from test_scripts.replica_runtime_acceptance import verify_runtime


def probe(fault=None, *, replicas=2):
    pods = [dict(kind="Pod", metadata=dict(name=f"{role}-{i}", labels={"okf-role": role}))
        for role in ("api", "worker") for i in range(replicas)]
    barrier = threading.Barrier(4, timeout=2)
    lock = threading.Lock()
    calls, active, peak = [], [0], [0]
    def python(pod, source):
        assert "enforce_runtime_identity" in source
        with lock:
            calls.append(pod)
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            barrier.wait()
            if fault == "exception" and pod == "worker-1":
                raise RuntimeError("Unreachable own runtime")
            result = dict(fingerprint="same", schema_revision="same", model_owners=0)
            if fault in {"fingerprint", "schema_revision"} and pod == "worker-1":
                result[fault] = "different"
            return result
        finally:
            with lock:
                active[0] -= 1
    return pods, SimpleNamespace(python=python), calls, peak


@pytest.mark.parametrize("replicas", [2, 4])
def test_independent_runtime_checks_overlap_with_bounded_concurrency(replicas):
    pods, kube, calls, peak = probe(replicas=replicas)
    result = verify_runtime(kube, pods, dict(namespace="test", run_id="test", installation_id="test"))
    assert result == {"fingerprint": "same", "schema_revision": "same"}
    assert set(calls) == {p["metadata"]["name"] for p in pods} and len(calls) == len(pods)
    assert peak[0] == 4


@pytest.mark.parametrize("fault", ["fingerprint", "schema_revision", "exception"])
def test_concurrent_identity_checks_never_drop_a_failed_or_different_pod(fault):
    pods, kube, calls, _ = probe(fault)
    error = RuntimeError if fault == "exception" else ValueError
    with pytest.raises(error):
        verify_runtime(kube, pods, dict(namespace="test", run_id="test", installation_id="test"))
    assert set(calls) == {p["metadata"]["name"] for p in pods}
