"""Live fault probes need real owned scope and evidence of fenced publication."""
import copy
import json

import pytest

from tests.test_replica_runtime_acceptance import live_inputs


def module():
    import importlib.util
    assert importlib.util.find_spec("test_scripts.replica_runtime_faults") is not None, "Active fault probes are missing"
    return importlib.import_module("test_scripts.replica_runtime_faults")


def test_recreated_namespace_blocks_active_fault_before_any_mutation():
    tool = module()
    inputs = {**live_inputs(), "namespace_uid": "11111111-1111-1111-1111-111111111111"}

    class Foreign:
        def scope(self):
            pass

        def run(self, *args):
            assert args[:2] == ("get", "namespace"), "No mutation before matching the recorded UID"
            return json.dumps({"metadata": {"uid": "22222222-2222-2222-2222-222222222222"}})

    with pytest.raises(ValueError, match="identity"):
        tool.assert_fault_scope(Foreign(), inputs)


@pytest.mark.parametrize("fault", ["late_active", "changed_content", "duplicate_active", "cancel_lost", "delete_reverted"])
def test_interrupted_publication_cannot_be_reported_as_success(fault):
    tool = module()
    before = {"active_generation": "a" * 32, "concept_sha256": "b" * 64, "active_count": 1,
        "work": [{"id": "c" * 32, "status": "running", "cancel_requested": False}]}
    after = {**copy.deepcopy(before), "deleted": fault == "delete_reverted", "status": "done"}
    after["work"][0]["cancel_requested"] = True
    operation = "delete" if fault == "delete_reverted" else "cancel"
    if fault == "late_active":
        after["active_generation"] = "d" * 32
    elif fault == "changed_content":
        after["concept_sha256"] = "d" * 64
    elif fault == "duplicate_active":
        after["active_count"] = 2
    elif fault == "cancel_lost":
        after["work"][0]["cancel_requested"] = False
    else:
        after["deleted"] = False
    with pytest.raises(ValueError):
        tool.assert_interrupted(before, after, operation=operation, work_ids={"c" * 32})


def test_busy_provider_forbids_fixture_rollout():
    tool = module()

    class Busy:
        def state(self, *_args):
            return {"model_owners": 1, "running_documents": 0, "running_chats": 0,
                "provider": {"active": 1}}

        def scope(self):
            pass

        def run(self, *_args):
            pytest.fail("Never restart a busy synthetic provider")

    with pytest.raises(ValueError, match="active"):
        tool.require_quiescent(Busy())
