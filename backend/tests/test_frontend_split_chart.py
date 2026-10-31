"""Render actual split topology and exercise maintenance count restoration."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import json
import pytest

from tests.test_kubernetes_chart import render


def test_frontend_split_has_no_shared_mount():
    docs = render("deployment.mode=frontend-split", "frontend.replicas=2")
    deployments = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}
    frontend = deployments["frontend"]
    assert frontend["spec"]["replicas"] == 2
    pod = frontend["spec"]["template"]["spec"]
    assert not any("persistentVolumeClaim" in volume for volume in pod["volumes"])
    container = pod["containers"][0]
    env = {item["name"]: item for item in container["env"]}
    assert env["DIAGNOSTICS_INGEST_URL"]["value"] == "http://backend:8000"
    assert "secretKeyRef" in env["DIAGNOSTICS_INGEST_TOKEN"]["valueFrom"]
    assert "OKF_DIAGNOSTICS_CONTROL_PATH" not in env
    assert pod["automountServiceAccountToken"] is False
    backend = deployments["application"]
    assert backend["spec"]["replicas"] == 1
    assert [c["name"] for c in backend["spec"]["template"]["spec"]["containers"]] == ["backend"]
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    assert services["frontend"]["spec"]["selector"]["okf-role"] == "frontend"
    assert services["backend"]["spec"]["selector"]["okf-role"] == "application"


@pytest.mark.parametrize("backend_count", [1, 2])
def test_lifecycle_restores_exact_counts(backend_count):
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/lifecycle.py"
    spec = importlib.util.spec_from_file_location("split_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manager = module.Manager.__new__(module.Manager)
    manager.release, manager.operation, manager.lock = "test", "operation", "maintenance"
    manager.selector = "test"
    deployments = {name: {"metadata": {"labels": {"app.kubernetes.io/instance": "test"}},
                          "spec": {"replicas": count}} for name, count in [("application", backend_count), ("frontend", 3)]}
    manager.get = lambda kind, name: deployments.get(name) if kind == "deployment" else {
        "data": {"operation": "operation", "release": "test"}}
    calls = []
    manager.kube = lambda *args, **kwargs: calls.append(args) or SimpleNamespace(stdout=json.dumps({"items": []}))
    if backend_count == 2:
        with pytest.raises(module.OperationError, match="single"):
            manager.stop()
        assert not any(call[0] == "scale" for call in calls)
        manager._stopped_counts = {"application": 2, "frontend": 3}
        with pytest.raises(module.OperationError, match="single"):
            manager.resume_stopped()
        assert not any(call[0] == "scale" for call in calls)
        return
    manager.stop()
    manager.resume_stopped()
    assert ("scale", "deployment/frontend", "--replicas=0") in calls
    assert ("scale", "deployment/frontend", "--replicas=3") in calls
    assert ("scale", "deployment/application", "--replicas=1") in calls


def test_split_frontend_budgets_are_validated_before_maintenance():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/lifecycle.py"
    spec = importlib.util.spec_from_file_location("split_budget_lifecycle", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manager = module.Manager.__new__(module.Manager)
    manager.documents = render("deployment.mode=frontend-split",
                               "probes.frontend.startup.failureThreshold=100",
                               "probes.frontend.startup.periodSeconds=10")
    manager.get = lambda *args: None
    with pytest.raises(module.OperationError, match="budget"):
        manager.validate_workload_budgets()
