"""The destructive lab recipe must bind observed identities before its stop."""
import importlib.util
from pathlib import Path
import sys

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_worker_loss.py"
    assert path.is_file(), "Owned active worker loss coordinator is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("worker_loss_test", path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_live_stop_guard_fetches_again_and_refuses_an_orphan():
    tool = module()
    from tests.test_replica_provider_stop import fixture
    value, binding, items, nodes = fixture()
    calls = []
    def fetch():
        calls.append(True)
        containers = [] if len(calls) == 1 else [{"state": "CONTAINER_RUNNING", "labels": {"io.kubernetes.pod.uid": "old-provider"}}]
        return items, nodes, containers
    guard = tool.StopObservation(fetch, value, binding)
    first = guard.digest()
    assert len(first) == 64
    with pytest.raises(ValueError):
        guard.digest()
    assert len(calls) == 2


def test_unknown_or_changed_slot_is_not_accepted_as_original():
    tool = module()
    lease = {"id": "slot", "resource": "llm", "owner_id": "original", "fence": 8, "release_required": True}
    assert tool.same_reservation([lease], lease)
    assert not tool.same_reservation([{**lease, "owner_id": "later", "fence": 9}], lease)
    assert not tool.same_reservation([lease, {**lease, "id": "second"}], lease)


def test_cri_failure_command_is_exact_and_never_stops_all():
    tool = module()
    selected = [{"container_id": "a" * 64, "pod_uid": "p", "namespace": "replica-test-fixture01", "node": "okf-policy-control-plane"}]
    source = tool.crash_source(selected)
    assert "--all" not in source and "--timeout=0" in source
    with pytest.raises(ValueError):
        tool.crash_source([{**selected[0], "container_id": "--all"}])


@pytest.mark.parametrize("fault", ["old_fence", "duplicate_active", "base_changed", "work_missing"])
def test_recovery_requires_new_owner_publication_and_unchanged_existing_corpus(fault):
    tool = module()
    old = {"id": "work", "fence": 1}
    work = [{"id": "work", "fence": 2, "status": "succeeded"}]
    baseline = [{"id": "doc", "sha256": "original", "active_generation_id": "base"}]
    observed = [dict(baseline[0])]
    count = 1
    if fault == "old_fence":
        work[0]["fence"] = 1
    elif fault == "duplicate_active":
        count = 2
    elif fault == "base_changed":
        observed[0]["active_generation_id"] = "late"
    else:
        work = []
    with pytest.raises(ValueError):
        tool.assert_recovered(old, work, count, baseline, observed)


@pytest.mark.parametrize("change", [{"epoch": 6}, {"enabled": True}, {"operation_id": "other"}])
def test_verification_cannot_adopt_a_later_maintenance_operation(change):
    tool = module()
    expected = {"epoch": 5, "operation_id": "operation"}
    state = {"epoch": 5, "operation_id": "operation", "enabled": False, "audit_count": 1}
    with pytest.raises(ValueError):
        tool.assert_released_state({**state, **change}, expected)
