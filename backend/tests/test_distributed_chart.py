"""Render the actual split API/worker topology, including the legacy guards."""
import os
import shutil

from tests.test_kubernetes_chart import render


def distributed(*extra, check=True):
    assert shutil.which(os.environ.get("HELM_BIN", "helm")), "Helm is required for distributed chart acceptance"
    return render("deployment.mode=distributed", "frontend.replicas=2", "api.replicas=2", "worker.replicas=2",
        "replicaStorage.artifacts.backend=s3", "replicaStorage.artifacts.provider=external",
        "replicaStorage.artifacts.storeId=acceptance-artifacts", "runtime.identityConfigMap=runtime-identity",
        *extra, check=check)


def test_api_service_excludes_workers():
    documents = distributed()
    deployments = {item["metadata"]["name"]: item for item in documents if item["kind"] == "Deployment"}
    assert {"frontend", "api", "worker"} <= deployments.keys()
    assert "application" not in deployments
    assert all(deployments[name]["spec"]["replicas"] == 2 for name in ("frontend", "api", "worker"))
    services = {item["metadata"]["name"]: item for item in documents if item["kind"] == "Service"}
    assert services["backend"]["spec"]["selector"]["okf-role"] == "api"
    assert services["frontend"]["spec"]["selector"]["okf-role"] == "frontend"
    assert not any(item["kind"] == "HorizontalPodAutoscaler" for item in documents)


def test_distributed_requires_shared_storage():
    result = distributed("replicaStorage.artifacts.backend=local", check=False)
    assert result.returncode != 0
    assert "shared" in result.stderr.lower() or "local" in result.stderr.lower()


def test_role_probes_and_identity():
    documents = distributed()
    deployments = {item["metadata"]["name"]: item for item in documents if item["kind"] == "Deployment"}
    pods = {name: deployments[name]["spec"]["template"]["spec"] for name in ("api", "worker")}
    for name, pod in pods.items():
        container = pod["containers"][0]
        environment = {entry["name"]: entry.get("value") for entry in container["env"]}
        assert environment["APP_ROLE"] == name
        assert environment["OKF_DEPLOYMENT_SCHEMA"] == "replicas-v1"
        assert environment["UVICORN_WORKERS"] == "1"
        assert container["envFrom"] == [{"secretRef": {"name": "test-runtime"}}, {"configMapRef": {"name": "runtime"}}]
        assert environment["LLM_BASE_URL"] == "http://model-stub:8000/v1"
        assert pod["automountServiceAccountToken"] is False
        assert any(volume.get("configMap", {}).get("name") == "runtime-identity" for volume in pod["volumes"])
        assert not any("persistentVolumeClaim" in volume for volume in pod["volumes"])
        assert pod["topologySpreadConstraints"][0]["topologyKey"] == "kubernetes.io/hostname"
        assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
        assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    api, worker = (pods[name]["containers"][0] for name in ("api", "worker"))
    assert api["readinessProbe"]["httpGet"]["port"] == 8000
    assert worker["readinessProbe"]["httpGet"]["port"] == 8001
    assert worker["command"] == ["python", "-m", "app.worker"]
    assert all(port["containerPort"] != 8000 for port in worker["ports"])
    for container in (api, worker):
        namespace = next(item for item in container["env"] if item["name"] == "DEPLOYMENT_NAMESPACE")
        assert namespace["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.namespace"


def test_distributed_network_keeps_worker_and_storage_private():
    documents = distributed()
    policies = {item["metadata"]["name"]: item["spec"] for item in documents if item["kind"] == "NetworkPolicy"}
    assert policies["worker-boundaries"]["ingress"] == []
    frontend = policies["frontend-boundaries"]
    assert frontend["ingress"][0]["ports"] == [{"protocol": "TCP", "port": 3000}]
    assert frontend["egress"][0]["to"][0]["podSelector"]["matchLabels"]["okf-role"] == "api"
    assert policies["distributed-default-deny"]["ingress"] == []
    for name in ("postgres", "qdrant"):
        rule = policies[name + "-boundaries"]["ingress"][0]
        roles = rule["from"][0]["podSelector"]["matchExpressions"][0]["values"]
        assert "frontend" not in roles and {"api", "worker"} <= set(roles)


def test_legacy_single_process_guard_retained():
    for override in ("application.replicas=2", "runtime.workers=2", "worker.replicas=1"):
        assert render(override, check=False).returncode != 0
    documents = render()
    assert [item["metadata"]["name"] for item in documents if item["kind"] == "Deployment" and item["metadata"]["name"] != "model-stub"] == ["application"]


def test_distributed_trust_is_a_bound_per_pod_snapshot():
    documents = distributed("trust.existingConfigMap=runtime-roots")
    for item in documents:
        if item["kind"] != "Deployment" or item["metadata"]["name"] not in {"api", "worker"}:
            continue
        container = item["spec"]["template"]["spec"]["containers"][0]
        environment = {entry["name"]: entry.get("value") for entry in container["env"]}
        assert environment["RUNTIME_TRUST_FILE"] == environment["ARTIFACT_S3_CA_BUNDLE"] == "/etc/okf/trust/ca-bundle.crt"
        assert environment["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] == environment["RUNTIME_TRUST_FILE"]
        mount = next(entry for entry in container["volumeMounts"] if entry["name"] == "runtime-trust")
        assert mount["subPath"] == "ca-bundle.crt" and mount["readOnly"] is True
