"""Provider import may only reach a new, stopped, explicitly bound lab target."""
import importlib.util
from pathlib import Path
import sys

import pytest

from tests.test_replica_backup import fixture_plan
from tests.test_replica_backup_coordinator import config


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_restore.py"
    assert path.is_file(), "Live restore coordinator is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("restore_coordinator", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_restore_refuses_same_installation_or_namespace():
    tool = module()
    plan, _ = fixture_plan()
    target = config()
    assert tool.assert_new_target(target, plan) is None
    target["installation_id"] = plan.source.installation_id
    with pytest.raises(ValueError):
        tool.assert_new_target(target, plan)
    target = config()
    plan = plan.model_copy(update={"source": plan.source.model_copy(update={"namespace": target["namespace"]})})
    with pytest.raises(ValueError):
        tool.assert_new_target(target, plan)


@pytest.mark.parametrize("fault", ["deployment", "pod", "job"])
def test_restore_never_imports_into_an_executing_target(fault):
    tool = module()
    items = [{"kind": "Deployment", "metadata": {"name": "worker"}, "spec": {"replicas": 0}}]
    assert tool.assert_jobs_held(items) is None
    if fault == "deployment":
        items[0]["spec"]["replicas"] = 1
    elif fault == "pod":
        items.append({"kind": "Pod", "metadata": {"labels": {"okf-role": "worker"}}, "status": {"phase": "Pending"}})
    else:
        items.append({"kind": "Job", "status": {"active": 1}})
    with pytest.raises(ValueError):
        tool.assert_jobs_held(items)
