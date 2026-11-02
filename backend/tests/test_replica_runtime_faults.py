"""Live fault probes need real owned scope and evidence of fenced publication."""
import copy
import json
from types import SimpleNamespace

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


def capacity_trial(monkeypatch, *, occupied, admissions=None, final_peak=1):
    """Run the actual trial and state validator across controlled remote replies."""
    tool = module()
    ids = ["a" * 16, "b" * 16]
    def state(active, started, finished, *, done=False, peak=1):
        return {"limit": 1, "model_owners": active, "running_documents": 0 if done else 2,
            "running_chats": 0,
            "provider": {"test_only": True, "active": active, "peak_active": peak,
                "started": started, "finished": finished},
            "documents": {doc_id: {"status": "done" if done else "processing",
                "work": [{"status": "done" if done else "running", "cancel_requested": False}]}
                for doc_id in ids}}
    snapshots = iter([state(0, 0, 0, done=True, peak=0), state(1, 1, 0),
        state(**occupied), state(0, 4, 4, done=True, peak=final_peak)])
    pods = [{"metadata": {"name": name}} for name in ("api-a", "api-b")]
    replies = iter(admissions or [{"busy": True}, {"busy": True}])
    called = []
    def remote(pod, source):
        if "acquire_model_budget" in source:
            called.append(pod)
            return next(replies)
        return next(snapshots)
    probe = tool.FaultProbe.__new__(tool.FaultProbe)
    probe.kube = SimpleNamespace(python=remote)
    probe.api_pods = lambda: pods
    probe.progress = lambda event: None
    def wait(predicate, **kwargs):
        result = probe.state(kwargs.get("doc_ids", ()))
        assert predicate(result), "The actual trial must observe saturation and completion"
        return result
    probe.wait = wait
    receipts = iter({"id": doc_id, "sha256": "c" * 64} for doc_id in ids)
    monkeypatch.setattr(tool, "upload", lambda *args: next(receipts))
    verified = []
    monkeypatch.setattr(tool, "verify_corpus_search_download", lambda client, docs: verified.extend(docs))
    result = tool.capacity(probe, object())
    assert called == ["api-a", "api-b"] and [doc["id"] for doc in verified] == ids
    return result


@pytest.mark.parametrize("occupied", [
    {"active": 1, "started": 2, "finished": 1},
    {"active": 0, "started": 4, "finished": 4, "done": True},
])
def test_capacity_refusals_allow_normal_worker_progress_between_remote_observations(monkeypatch, occupied):
    result = capacity_trial(monkeypatch, occupied=occupied)
    assert result["api_processes_rejected_without_provider_call"] == 2
    assert result["global_limit"] == result["peak_provider_calls"] == 1
    assert result["completed_documents"] == 2


@pytest.mark.parametrize("admissions", [
    [{"busy": False}], [{"busy": True}, {"busy": False}], [{"busy": True, "unknown": True}],
])
def test_capacity_without_two_exact_api_refusals_remains_unqualified(monkeypatch, admissions):
    with pytest.raises(ValueError):
        capacity_trial(monkeypatch, occupied={"active": 1, "started": 1, "finished": 0}, admissions=admissions)


@pytest.mark.parametrize("when", ["between", "final"])
def test_actual_provider_concurrency_violation_still_fails_capacity_trial(monkeypatch, when):
    with pytest.raises(ValueError, match="concurrency"):
        capacity_trial(monkeypatch, occupied={"active": 1, "started": 1, "finished": 0,
            "peak": 2 if when == "between" else 1}, final_peak=2 if when == "final" else 1)


def test_provider_counter_reset_cannot_hide_a_concurrency_peak(monkeypatch):
    with pytest.raises(ValueError):
        capacity_trial(monkeypatch, occupied={"active": 0, "started": 0, "finished": 0, "peak": 0})


def test_actual_remote_admission_source_refuses_owned_slot_without_a_provider_call(tmp_path, monkeypatch):
    from contextlib import redirect_stdout
    from io import StringIO
    from app.config import Settings
    from app.db.session import session_scope
    from app.services.work.coordination import reserve_capacity
    tool = module()
    source = next(value for value in tool.capacity.__code__.co_consts
        if isinstance(value, str) and "acquire_model_budget" in value)
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_global_max_concurrency=1)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    identities = []
    monkeypatch.setattr("app.deployment.runtime_identity.enforce_runtime_identity", lambda value: identities.append(value))
    def forbidden(*args, **kwargs):
        pytest.fail("A capacity-only probe must never invoke the provider")
    monkeypatch.setattr("litellm.completion", forbidden)
    with session_scope() as session:
        lease = reserve_capacity(session, resource="llm", owner_id="actual-provider", limit=1,
            ttl_seconds=120, release_required=True)
    for _ in range(2):
        output = StringIO()
        with redirect_stdout(output):
            exec(source, {})
        assert json.loads(output.getvalue()) == {"busy": True}
    assert identities == [settings, settings]
    from app.db.models import ReplicaCapacitySlot
    with session_scope() as session:
        row = session.get(ReplicaCapacitySlot, lease.id)
        assert row.owner_id == lease.owner_id and row.fence == lease.fence and row.release_required
