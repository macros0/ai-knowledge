"""Check rendered workload contracts, not snapshots of template text."""

import os
from pathlib import Path
import subprocess
import shutil

import pytest
import yaml

ROOT = Path(__file__).parents[2]
CHART = ROOT / "deploy/helm/ai-knowledge"
pytestmark = pytest.mark.skipif(not shutil.which(os.environ.get("HELM_BIN", "helm")), reason="Helm required; Kubernetes CI installs it explicitly")


def render(*overrides, check=True):
    assert (CHART / "Chart.yaml").exists(), "Kubernetes chart is missing"
    command = [os.environ.get("HELM_BIN", "helm"), "template", "test", str(CHART),
               "--namespace", "test", "--set", "secrets.existingSecret=test-runtime",
               "--set", "persistence.data.existingClaim=data", "--set", "persistence.diagnostics.existingClaim=diagnostics",
               "--set", "persistence.postgres.existingClaim=postgres", "--set", "persistence.qdrant.existingClaim=qdrant",
               "--set", "images.backend.tag=test", "--set", "images.frontend.tag=test", "--set", "images.stub.tag=test"]
    for override in overrides:
        command.extend(["--set", override])
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    if not check:
        return result
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def test_compact_workload_preserves_single_process_and_mount_boundaries():
    docs = render()
    app = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "application")
    assert app["spec"]["replicas"] == 1
    assert app["spec"]["strategy"]["type"] == "Recreate"
    pod = app["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    containers = {c["name"]: c for c in pod["containers"]}
    assert set(containers) == {"frontend", "backend"}
    backend = containers["backend"]
    assert backend["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert backend["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert containers["frontend"]["readinessProbe"]["tcpSocket"]["port"] == 3000
    assert next(m for m in backend["volumeMounts"] if m["mountPath"] == "/diagnostics/frontend")["readOnly"]
    assert all(d["kind"] not in {"PersistentVolumeClaim", "Secret", "Ingress", "HorizontalPodAutoscaler"} for d in docs)
    assert {d["metadata"]["name"] for d in docs if d["kind"] == "StatefulSet"} == {"postgres", "qdrant"}


@pytest.mark.parametrize("setting", ["application.replicas=2", "deployment.mode=split", "runtime.workers=2",
                                    "deployment.environment=prod", "storage.mode=external"])
def test_unsafe_or_unimplemented_modes_refused(setting):
    assert render(setting, check=False).returncode != 0


def test_application_disabled_keeps_storage_but_no_app_pods():
    docs = render("application.enabled=false")
    assert not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "application" for d in docs)
    assert sum(d["kind"] == "StatefulSet" for d in docs) == 2


def test_prod_live_has_no_stub_and_strong_runtime_guards():
    digest = "sha256:" + "a" * 64
    docs = render("deployment.environment=prod", "models.mode=live",
                  "images.backend.digest=" + digest, "images.frontend.digest=" + digest)
    assert not any(d["metadata"]["name"] == "model-stub" for d in docs)
    cfg = next(d for d in docs if d["kind"] == "ConfigMap")["data"]
    assert cfg["ENVIRONMENT"] == "production"
    assert cfg["MODEL_MODE"] == "live"
    assert cfg["DEPLOYMENT_TIER"] == "prod"
    assert cfg["EMBEDDING_PROVIDER"] == "http"


def test_platform_resources_are_optional_and_tls_is_not_created():
    docs = render("gateway.enabled=true", "gateway.hostname=example.invalid", "gateway.certificateSecret=existing-tls")
    route = next(d for d in docs if d["kind"] == "HTTPRoute" and d["metadata"]["name"] == "frontend")
    assert route["spec"]["rules"][0]["backendRefs"][0]["port"] == 3000
    assert any(d["kind"] == "ListenerSet" for d in docs)
    assert not any(d["kind"] in {"Ingress", "Secret", "Gateway"} for d in docs)


def test_nonroot_qdrant_working_directory_and_snapshots_are_writable():
    docs = render()
    qdrant = next(d for d in docs if d["kind"] == "StatefulSet" and d["metadata"]["name"] == "qdrant")
    container = qdrant["spec"]["template"]["spec"]["containers"][0]
    assert container["workingDir"] == "/qdrant/storage"
    assert container["command"] == ["/qdrant/qdrant"]


def test_stub_endpoint_cannot_be_overridden_by_runtime_secret():
    docs = render()
    app = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "application")
    backend = next(c for c in app["spec"]["template"]["spec"]["containers"] if c["name"] == "backend")
    environment = {e["name"]: e.get("value") for e in backend["env"]}
    assert environment["LLM_BASE_URL"] == "http://model-stub:8000/v1"
    assert environment["LLM_MODEL"] == "openai/test-stub"
