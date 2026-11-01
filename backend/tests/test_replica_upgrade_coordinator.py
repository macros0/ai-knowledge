"""The lab rollout must bind stopped roles to the new config/image/identity together."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_upgrade.py"
    assert path.is_file(), "Owned upgrade coordinator is missing"
    spec = importlib.util.spec_from_file_location("upgrade_coordinator", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def deployment():
    return {"metadata": {"name": "api", "uid": "owned", "resourceVersion": "12"}, "spec": {"replicas": 0,
        "template": {"spec": {"containers": [{"name": "api", "image": "old-image", "envFrom": [
            {"secretRef": {"name": "private-runtime"}}, {"configMapRef": {"name": "runtime"}}],
            "volumeMounts": [{"name": "runtime-identity", "mountPath": "/run/identity"}]}],
            "volumes": [{"name": "runtime-identity", "configMap": {"name": "old-identity"}}]}}}}


def test_upgrade_binds_new_image_config_identity_without_starting_role():
    tool = module()
    original = deployment()
    result = tool.stage_role(original, old_runtime="runtime", new_runtime="runtime-new",
        old_identity="old-identity", new_identity="identity-new", image="new-image")
    assert original == deployment()
    assert result["metadata"]["resourceVersion"] == "12"
    assert result["spec"]["replicas"] == 0
    pod = result["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"] == "new-image"
    assert pod["containers"][0]["envFrom"] == [{"secretRef": {"name": "private-runtime"}}, {"configMapRef": {"name": "runtime-new"}}]
    assert pod["volumes"][0]["configMap"]["name"] == "identity-new"
    assert tool.stage_role(result, old_runtime="runtime", new_runtime="runtime-new",
        old_identity="old-identity", new_identity="identity-new", image="new-image") == result


@pytest.mark.parametrize("fault", ["running", "foreign_config", "foreign_identity"])
def test_upgrade_cannot_stage_running_or_unbound_role(fault):
    tool = module()
    value = copy.deepcopy(deployment())
    if fault == "running":
        value["spec"]["replicas"] = 1
    elif fault == "foreign_config":
        value["spec"]["template"]["spec"]["containers"][0]["envFrom"][1]["configMapRef"]["name"] = "foreign"
    else:
        value["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] = "foreign"
    with pytest.raises(ValueError):
        tool.stage_role(value, old_runtime="runtime", new_runtime="runtime-new", old_identity="old-identity",
            new_identity="identity-new", image="new-image")


def test_operator_job_uses_the_existing_bootstrap_network_policy_role():
    tool = module()
    api = deployment()
    api["spec"]["template"]["metadata"] = {"labels": {"okf-role": "api", "app.kubernetes.io/instance": "owned"}}
    api["spec"]["template"]["spec"]["containers"][0]["env"] = []
    result = tool.operator_job(api, name="upgrade", image="new-image", runtime="runtime-new", inputs="inputs", command=["python"])
    assert result["spec"]["template"]["metadata"]["labels"]["okf-role"] == "bootstrap"
    assert api["spec"]["template"]["metadata"]["labels"]["okf-role"] == "api"


def test_resume_refuses_foreign_journal_before_any_kubernetes_or_database_action(tmp_path):
    tool = module()
    journal = {"namespace_uid": "foreign", "image": "new-image", "old_identity": {"installation_id": "5" * 32}}
    path = tmp_path / "journal.json"
    path.write_text(json.dumps(journal))

    class Lab:
        namespace_uid = "owned"

        def kubectl(self, *_args):
            pytest.fail("Foreign journal must not reach Kubernetes")

    with pytest.raises(ValueError, match="journal"):
        tool.resume_upgrade(Lab(), {"installation_id": "5" * 32}, image="new-image", report=tmp_path / "report.json", journal_path=path)
