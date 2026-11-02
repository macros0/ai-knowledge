"""Image rollout retains scoped runtime credentials and offline-only authority."""
import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tests.test_replica_upgrade_coordinator import deployment


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_image_upgrade.py"
    assert path.is_file(), "Scoped image upgrade coordinator is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("image_upgrade_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def api():
    value = deployment()
    pod = value["spec"]["template"]["spec"]
    value["spec"]["template"]["metadata"] = {"labels": {"okf-role": "api"}}
    pod["containers"][0]["env"] = []
    pod["volumes"].append({"name": "runtime-trust", "configMap": {"name": "current-roots"}})
    pod["containers"][0]["volumeMounts"].append({"name": "runtime-trust", "mountPath": "/run/trust", "readOnly": True})
    return value


def stage(tool, value, baseline):
    return tool.stage_image_role(value, baseline=baseline, runtime="runtime-new", identity="identity-new",
        image="new-image", runtime_secret="private-runtime", operator_secret="operator-only")


def test_stopped_image_stage_changes_only_image_config_and_identity():
    tool = module()
    baseline = api()
    result = stage(tool, copy.deepcopy(baseline), baseline)
    expected = copy.deepcopy(baseline)
    container = expected["spec"]["template"]["spec"]["containers"][0]
    container["image"] = "new-image"
    container["envFrom"][1]["configMapRef"]["name"] = "runtime-new"
    expected["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] = "identity-new"
    assert result == expected
    assert stage(tool, result, baseline) == result
    assert baseline == api()
    assert "operator-only" not in json.dumps(result)


@pytest.mark.parametrize("fault", ["running", "uid", "secret", "trust", "mixed", "extra_env"])
def test_stage_refuses_changed_source_instead_of_overwriting_it(fault):
    tool = module()
    baseline, value = api(), api()
    pod = value["spec"]["template"]["spec"]
    if fault == "running":
        value["spec"]["replicas"] = 1
    elif fault == "uid":
        value["metadata"]["uid"] = "foreign"
    elif fault == "secret":
        pod["containers"][0]["envFrom"][0]["secretRef"]["name"] = "operator-only"
    elif fault == "trust":
        pod["volumes"][1]["configMap"]["name"] = "other-roots"
    elif fault == "mixed":
        pod["containers"][0]["image"] = "new-image"
    else:
        pod["containers"][0]["env"].append({"name": "DATABASE_URL", "value": "unexpected"})
    with pytest.raises(ValueError):
        stage(tool, value, baseline)


def test_candidate_job_keeps_runtime_secret_and_mounts_operator_url_only_as_file():
    tool = module()
    baseline = api()
    result = tool.image_operator_job(baseline, name="compute", image="new-image", runtime="runtime-new",
        inputs="inputs", runtime_secret="private-runtime", operator_secret="operator-only", command=["python"])
    pod = result["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["image"] == "new-image"
    assert container["envFrom"] == [{"secretRef": {"name": "private-runtime"}}, {"configMapRef": {"name": "runtime-new"}}]
    assert "operator-only" not in json.dumps(container.get("env", [])) + json.dumps(container["envFrom"])
    assert {"name": "operator-database", "mountPath": "/run/operator-db", "readOnly": True} in container["volumeMounts"]
    assert {"name": "operator-database", "secret": {"secretName": "operator-only", "defaultMode": 0o440,
            "items": [{"key": "url", "path": "url"}]}} in pod["volumes"]
    assert {"name": "runtime-trust", "configMap": {"name": "current-roots"}} in pod["volumes"]
    assert baseline == api()


@pytest.mark.parametrize("fault", ["shared_secret", "wrong_runtime_secret", "existing_operator"])
def test_candidate_job_refuses_operator_secret_alias_or_preexisting_mount(fault):
    tool = module()
    source = api()
    selected = "private-runtime"
    operator = "operator-only"
    if fault == "shared_secret":
        operator = selected
    elif fault == "wrong_runtime_secret":
        selected = "other"
    else:
        source["spec"]["template"]["spec"]["volumes"].append({"name": "operator-database", "secret": {"secretName": operator}})
    with pytest.raises(ValueError):
        tool.image_operator_job(source, name="compute", image="new-image", runtime="runtime-new", inputs="inputs",
            runtime_secret=selected, operator_secret=operator, command=["python"])


@pytest.mark.parametrize("fault", ["uid", "data", "mutable", "foreign_run"])
def test_pinned_input_rejects_recreated_or_changed_immutable_resource(fault):
    tool = module()
    baseline = {"metadata": {"name": "owned", "uid": "first", "labels": {"okf-run-id": "case"}},
                "immutable": True, "data": {"key": "public-value"}}
    value = copy.deepcopy(baseline)
    if fault == "uid":
        value["metadata"]["uid"] = "second"
    elif fault == "data":
        value["data"]["key"] = "changed"
    elif fault == "mutable":
        value["immutable"] = False
    else:
        value["metadata"]["labels"]["okf-run-id"] = "other"
    with pytest.raises(ValueError):
        tool.assert_pinned_input(value, baseline)
    tool.assert_pinned_input(copy.deepcopy(baseline), baseline)


def test_legacy_upgrade_refuses_scoped_sql_principal_before_fence():
    from tests.test_replica_upgrade_coordinator import module as old_module
    tool = old_module()
    scoped = {"data": {"DATABASE_URL": base64.b64encode(b"postgresql+psycopg://scoped:secret@owned/db").decode()}}
    with pytest.raises(ValueError, match="scoped"):
        tool.require_legacy_operator_principal(scoped, "replica")
    scoped["data"]["DATABASE_URL"] = base64.b64encode(b"postgresql+psycopg://replica:secret@owned/db").decode()
    tool.require_legacy_operator_principal(scoped, "replica")


def operation_with_roles(tool):
    operation = tool.ImageUpgrade.__new__(tool.ImageUpgrade)
    roles = {name: api() for name in ("api", "worker", "frontend", "model-stub")}
    for name, value in roles.items():
        value["metadata"].update(name=name, uid=name)
        value["spec"]["replicas"] = 1 if name == "model-stub" else 2
    operation.journal = {"old_deployments": roles, "next_runtime": "runtime-new", "next_identity": "identity-new",
        "image": "new-image", "runtime_secret": "private-runtime", "operator_secret": "operator-only"}
    current = copy.deepcopy(roles)
    for name, value in current.items():
        value["spec"]["replicas"] = 0
        if name in {"api", "worker"}:
            current[name] = stage(tool, value, roles[name])
    operation.resource = lambda kind, name: copy.deepcopy(current[name])
    return operation, current


@pytest.mark.parametrize("fault", ["old_image", "frontend_image", "provider_env", "running"])
def test_release_role_guard_refuses_unstaged_or_changed_workloads(fault):
    tool = module()
    operation, current = operation_with_roles(tool)
    if fault == "old_image":
        current["api"] = copy.deepcopy(operation.journal["old_deployments"]["api"])
        current["api"]["spec"]["replicas"] = 0
    elif fault == "frontend_image":
        current["frontend"]["spec"]["template"]["spec"]["containers"][0]["image"] = "other"
    elif fault == "provider_env":
        current["model-stub"]["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "EXTRA", "value": "changed"}]
    else:
        current["worker"]["spec"]["replicas"] = 2
    with pytest.raises(ValueError):
        operation.check_roles(staged=True)


def test_start_resume_accepts_only_same_staged_roles_and_intended_counts():
    tool = module()
    operation, current = operation_with_roles(tool)
    operation.check_roles(staged=True)
    current["api"]["spec"]["replicas"] = 2
    operation.check_roles(staged=True, allow_running=True)
    current["worker"]["spec"]["replicas"] = 3
    with pytest.raises(ValueError):
        operation.check_roles(staged=True, allow_running=True)


def test_release_observes_same_existing_job_after_lost_success_response(monkeypatch):
    tool = module()
    operation = tool.ImageUpgrade.__new__(tool.ImageUpgrade)
    operation.journal = {"phase": "staged", "inputs": "operation-inputs"}
    operation.check_inputs = lambda: None
    operation.check_roles = lambda **kwargs: None
    operation.check_held = lambda: pytest.fail("A released existing Job must not require re-entry into maintenance")
    class Kube:
        def run(self, *args):
            assert args == ("get", "job", "operation-release", "--ignore-not-found", "-o", "json")
            return '{"metadata":{"uid":"same-job"}}'
    operation.kube = Kube()
    observed = []
    monkeypatch.setattr(tool.ReplicaCredentials, "release", lambda self: observed.append("observe-existing"))
    operation.release()
    assert observed == ["observe-existing"]


@pytest.mark.parametrize("fault", ["uid", "installation", "phase", "unverified_ca"])
def test_backup_diagnostic_provenance_refused_before_lab_access(tmp_path, fault):
    module()
    import replica_backup
    journal = {"phase": "verified", "namespace_uid": "owned", "ca_verified": True,
        "new_identity": {"installation_id": "5" * 32, "runtime_fingerprint": "a" * 64},
        "retired_diagnostics": [{"name": "recorded-failure"}]}
    if fault == "uid":
        journal["namespace_uid"] = "foreign"
    elif fault == "installation":
        journal["new_identity"]["installation_id"] = "6" * 32
    elif fault == "phase":
        journal["phase"] = "running"
    else:
        journal["ca_verified"] = False
    path = tmp_path / "source.json"
    path.write_text(json.dumps(journal))
    with pytest.raises(ValueError):
        replica_backup.source_journal_lab({"installation_id": "5" * 32, "ca_file": str(tmp_path / "ca.pem")}, "owned", path)


def test_backup_uses_exact_verified_diagnostic_guard_and_binds_current_fingerprint(tmp_path, monkeypatch):
    tool = module()
    import replica_backup
    journal = {"phase": "verified", "namespace_uid": "owned", "ca_verified": True,
        "new_identity": {"installation_id": "5" * 32, "runtime_fingerprint": "a" * 64},
        "retired_diagnostics": [{"name": "recorded-failure", "uid": "exact-uid"}]}
    path = tmp_path / "source.json"
    path.write_text(json.dumps(journal))
    received = []
    class Guarded:
        def __init__(self, config, uid, operation):
            received.append((uid, operation.journal))
    monkeypatch.setattr(sys.modules[tool.CaLab.__module__], "CaLab", Guarded)
    result = replica_backup.source_journal_lab({"installation_id": "5" * 32, "ca_file": str(tmp_path / "ca.pem")}, "owned", path)
    assert result.expected_source_fingerprint == "a" * 64
    assert received == [("owned", journal)]
