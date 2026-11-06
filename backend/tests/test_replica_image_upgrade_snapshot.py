"""Live input validation must fit a bounded provider fault window."""
import copy
import json
from types import SimpleNamespace

import pytest

from tests.test_replica_image_upgrade import module, operation_with_roles


def inputs():
    tool = module()
    op = tool.ImageUpgrade.__new__(tool.ImageUpgrade)
    op.value = {"namespace": "replica-test-snapshot"}
    checked = []
    op.lab = SimpleNamespace(assert_namespace=lambda: checked.append("scope"))
    def obj(kind, name, **extra):
        return dict(kind=kind, metadata=dict(name=name, namespace=op.value["namespace"], uid=name), **extra)
    secret = obj("Secret", "runtime", immutable=True, data={"key": "test-only"})
    config = obj("ConfigMap", "identity", immutable=True, data={"identity": "test-only"})
    claim = obj("PersistentVolumeClaim", "data", spec={"volumeName": "owned-pv"})
    storage = obj("StatefulSet", "postgres", spec={"replicas": 1})
    objects = [secret, config, claim, storage]
    op.journal = dict(pinned_inputs={"secret/runtime": copy.deepcopy(secret), "cm/identity": copy.deepcopy(config)},
        claims={"data": dict(uid="data", spec=copy.deepcopy(claim["spec"]))},
        old_storage={"postgres": copy.deepcopy(storage)})
    calls = []
    def run(*args):
        calls.append(args)
        assert args == ("get", "configmaps,secrets,persistentvolumeclaims,statefulsets", "-o", "json")
        return json.dumps({"items": objects})
    op.kube = SimpleNamespace(run=run)
    return op, objects, calls, checked


def test_input_guard_validates_all_inputs_with_one_fresh_inventory_per_call():
    op, objects, calls, checked = inputs()
    op.check_inputs()
    assert len(calls) == 1 and checked == ["scope"]
    objects[0]["data"]["key"] = "changed-after-first-observation"
    with pytest.raises(ValueError):
        op.check_inputs()
    assert len(calls) == 2 and checked == ["scope", "scope"]


@pytest.mark.parametrize("fault", ["secret-uid", "secret-data", "mutable", "missing-config", "claim", "storage", "namespace", "duplicate", "unknown-kind"])
def test_batched_inputs_keep_each_existing_integrity_guard(fault):
    op, objects, _, _ = inputs()
    if fault == "secret-uid":
        objects[0]["metadata"]["uid"] = "replacement"
    elif fault == "secret-data":
        objects[0]["data"]["key"] = "replacement"
    elif fault == "mutable":
        objects[1]["immutable"] = False
    elif fault == "missing-config":
        objects.pop(1)
    elif fault == "claim":
        objects[2]["spec"]["volumeName"] = "foreign"
    elif fault == "storage":
        objects[3]["spec"]["replicas"] = 2
    elif fault == "namespace":
        objects[0]["metadata"]["namespace"] = "production"
    elif fault == "duplicate":
        objects.append(copy.deepcopy(objects[0]))
    else:
        objects.append(dict(kind="Pod", metadata=dict(name="foreign", namespace=op.value["namespace"], uid="foreign")))
    with pytest.raises(ValueError):
        op.check_inputs()


def test_role_guard_observes_one_fresh_deployment_snapshot_and_keeps_uid_checks():
    tool = module()
    op, current = operation_with_roles(tool)
    op.value = {"namespace": "replica-test-snapshot"}
    for value in current.values():
        value.update(kind="Deployment")
        value["metadata"]["namespace"] = op.value["namespace"]
    calls = []
    def run(*args):
        calls.append(args)
        assert args == ("get", "deployments", "-o", "json")
        return json.dumps({"items": list(current.values())})
    op.kube = SimpleNamespace(run=run)
    op.check_roles(staged=True)
    assert len(calls) == 1
    current["frontend"]["metadata"]["uid"] = "replacement"
    with pytest.raises(ValueError):
        op.check_roles(staged=True)
    assert len(calls) == 2
