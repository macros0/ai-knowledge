"""One current-version run must execute every required stage before qualification."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace, ModuleType

import pytest


EXPECTED = (
    "preflight", "smoke", "idle", "after-idle", "active",
    "worker.inject", "worker.observe", "worker.recover", "worker.release", "worker.verify",
    "partition.inject", "partition.confirm", "partition.observe", "partition.heal", "partition.finish",
    "partition.restore", "partition.verify", "load.prepare", "load.single", "load.dual", "load.verify", "final",
)


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_runtime_run.py"
    assert path.is_file(), "Integrated current-profile runtime runner is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("runtime_run_test", path)
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    assert tuple(tool.STAGES) == EXPECTED
    return tool


class Adapter:
    def __init__(self, private):
        self.private = private
        self.calls = []
        self.value = "source-a"
        self.observed = None
        self.fail = None
        self.ready = True

    def bind(self, state):
        return {"source": self.value, "admission": {"backup_id": "b" * 32}}

    def before(self, stage, state):
        if not self.ready:
            raise ValueError("PRIVATE_CONNECTION_CANARY")

    def execute(self, stage, state):
        self.calls.append(stage)
        if self.fail == stage:
            raise RuntimeError("PRIVATE_SECRET_CANARY")
        return {"passed": True, "evidence": {"private": "PRIVATE_SECRET_CANARY", "stage": stage}}

    def observe(self, stage, state):
        return self.observed

    def can_resume(self, stage, state):
        return stage == "worker.observe"


def run(tmp_path):
    tool = module(); adapter = Adapter(tmp_path)
    return tool, adapter, tool.RuntimeRun(tmp_path / "run.json", adapter, code_sha256="a" * 64)


def test_complete_current_run_executes_all_required_steps_and_redacts_private_evidence(tmp_path):
    _, adapter, current = run(tmp_path)
    for stage in EXPECTED:
        result = current.execute(stage, apply=True)
        assert result["passed"] is (stage == "final")
    assert adapter.calls == list(EXPECTED)
    assert result["failed"] == result["skipped_required"] == 0
    assert {row["name"] for row in result["checks"]} == {"runtime." + s for s in EXPECTED}
    assert "PRIVATE_SECRET_CANARY" not in json.dumps(result)


@pytest.mark.parametrize("stage", ["smoke", "worker.inject", "partition.inject", "load.single", "final", "../inject"])
def test_out_of_order_or_unknown_stage_never_executes(tmp_path, stage):
    _, adapter, current = run(tmp_path)
    with pytest.raises(ValueError): current.execute(stage, apply=True)
    assert adapter.calls == []


def test_mutating_stages_require_explicit_apply_and_readonly_preflight_does_not(tmp_path):
    _, adapter, current = run(tmp_path)
    current.execute("preflight")
    with pytest.raises(ValueError): current.execute("smoke")
    assert adapter.calls == ["preflight"]


def test_completed_step_is_observed_without_reexecuting_it(tmp_path):
    tool, adapter, current = run(tmp_path)
    current.execute("preflight")
    resumed = tool.RuntimeRun(tmp_path / "run.json", adapter, code_sha256="a" * 64)
    resumed.execute("preflight")
    assert adapter.calls == ["preflight"]
    assert resumed.report()["passed"] is False


def test_uncertain_injection_keeps_same_run_and_cannot_be_replayed_even_with_resume_flag(tmp_path):
    tool, adapter, current = run(tmp_path)
    for stage in EXPECTED[:5]: current.execute(stage, apply=True)
    adapter.fail = "worker.inject"
    with pytest.raises(ValueError): current.execute("worker.inject", apply=True)
    identity = current.state["execution_id"]
    resumed = tool.RuntimeRun(tmp_path / "run.json", adapter, code_sha256="a" * 64)
    with pytest.raises(ValueError): resumed.execute("worker.inject", apply=True, resume=True)
    assert resumed.state["execution_id"] == identity
    assert adapter.calls.count("worker.inject") == 1
    assert resumed.report()["failed"] == 1 and resumed.report()["passed"] is False
    assert "PRIVATE_SECRET_CANARY" not in json.dumps(resumed.report())


def test_lost_response_advances_only_after_same_child_outcome_is_observed(tmp_path):
    tool, adapter, current = run(tmp_path)
    for stage in EXPECTED[:5]: current.execute(stage, apply=True)
    adapter.fail = "worker.inject"
    with pytest.raises(ValueError): current.execute("worker.inject", apply=True)
    adapter.observed = {"passed": True, "evidence": {"operation_id": "c" * 32, "same_child": True}}
    resumed = tool.RuntimeRun(tmp_path / "run.json", adapter, code_sha256="a" * 64)
    resumed.execute("worker.inject", apply=True)
    assert adapter.calls.count("worker.inject") == 1
    assert resumed.state["active"] is None and "worker.inject" in resumed.state["completed"]


@pytest.mark.parametrize("fault", ["code", "binding", "missing_stage", "foreign_active", "unknown_version"])
def test_changed_code_source_or_forged_stage_history_cannot_be_qualified(tmp_path, fault):
    tool, adapter, current = run(tmp_path)
    current.execute("preflight")
    path = tmp_path / "run.json"; state = json.loads(path.read_bytes())
    code = "a" * 64
    if fault == "code": code = "c" * 64
    elif fault == "binding": adapter.value = "different-profile"
    elif fault == "missing_stage": state["completed"]["final"] = state["completed"].pop("preflight")
    elif fault == "foreign_active": state["active"] = {"stage": "worker.inject", "started_at": "ignored"}
    else: state["version"] = 2
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError): tool.RuntimeRun(path, adapter, code_sha256=code)
    assert adapter.calls == ["preflight"]


def test_transport_precheck_failure_leaves_no_uncertain_mutation_marker(tmp_path):
    _, adapter, current = run(tmp_path)
    current.execute("preflight")
    adapter.ready = False
    with pytest.raises(ValueError): current.execute("smoke", apply=True)
    assert current.state["active"] is None and adapter.calls == ["preflight"]
    adapter.ready = True; current.execute("smoke", apply=True)
    assert adapter.calls == ["preflight", "smoke"]


def test_a_failed_or_empty_stage_result_is_not_completion(tmp_path):
    _, adapter, current = run(tmp_path)
    adapter.execute = lambda *args: {"passed": False}
    with pytest.raises(ValueError): current.execute("preflight")
    assert current.report()["passed"] is False and current.report()["skipped_required"] == len(EXPECTED)


def test_no_required_native_checks_cannot_be_treated_as_passed():
    tool = module()
    with pytest.raises(ValueError): tool.OwnedAdapter.checked([], [])


def member_adapter(tmp_path):
    tool = module(); adapter = tool.OwnedAdapter.__new__(tool.OwnedAdapter)
    adapter.private = tmp_path; adapter.value = {"namespace_uid": "owned-namespace"}
    adapter.manifest = {"runtime_fingerprint": "d" * 64}
    state = {"execution_id": "a" * 32, "created_at": "2026-11-01T12:00:00+00:00",
        "admission": {"source_journal_sha256": "b" * 64, "backup_id": "c" * 32,
            "backup_authentication": "e" * 64, "backup_archive": "protected-backup"}}
    return tool, adapter, state


def member_record(adapter, state, member, phase):
    child = {"runtime_run_id": state["execution_id"], "phase": phase, "namespace_uid": adapter.value["namespace_uid"],
        "fingerprint": adapter.manifest["runtime_fingerprint"], "operation_id": "f" * 32,
        "started_at": state["created_at"], "fault_authority": state["admission"],
        "cases": {"workers-1": {"status": "passed"}, "workers-2": {"status": "passed"}}}
    adapter.child_path(member, state).write_text(json.dumps(child))
    return child


def test_old_member_copied_to_current_run_path_cannot_be_adopted(tmp_path):
    _, adapter, state = member_adapter(tmp_path)
    child = member_record(adapter, state, "worker", "waiting-for-quarantine")
    child["runtime_run_id"] = "9" * 32
    adapter.child_path("worker", state).write_text(json.dumps(child))
    with pytest.raises(ValueError): adapter.observe("worker.inject", state)


@pytest.mark.parametrize("stage,phase", [
    ("worker.inject", "waiting-for-quarantine"), ("worker.observe", "quarantine-proven"),
    ("worker.recover", "reconciled"), ("worker.release", "running"),
    ("partition.inject", "partition-applied"), ("partition.confirm", "partition-proven"),
    ("partition.observe", "reclaim-proven"), ("partition.heal", "confirmed-return"),
    ("partition.heal", "quarantine-proven"), ("partition.finish", "running"),
    ("partition.restore", "normal-running"), ("load.prepare", "prepared"),
    ("load.single", "workers-1"), ("load.dual", "workers-2"),
])
def test_observation_uses_actual_recipe_terminal_phase_names(tmp_path, stage, phase):
    _, adapter, state = member_adapter(tmp_path)
    member_record(adapter, state, stage.split(".")[0], phase)
    assert adapter.observe(stage, state)["passed"] is True
    child = json.loads(adapter.child_path(stage.split(".")[0], state).read_bytes())
    if stage not in {"load.single", "load.dual"}:
        child["phase"] = "unknown-in-flight"
        adapter.child_path(stage.split(".")[0], state).write_text(json.dumps(child))
        assert adapter.observe(stage, state) is None


def test_load_report_uses_its_actual_fingerprint_field_and_current_run_child(tmp_path):
    _, adapter, state = member_adapter(tmp_path)
    member_record(adapter, state, "load", "verified")
    report = {"namespace_uid": adapter.value["namespace_uid"], "fingerprint": adapter.manifest["runtime_fingerprint"]}
    adapter.child_path("load", state, report=True).write_text(json.dumps(report))
    assert adapter.observe("load.verify", state)["passed"] is True
    report["fingerprint"] = "9" * 64
    adapter.child_path("load", state, report=True).write_text(json.dumps(report))
    with pytest.raises(ValueError): adapter.observe("load.verify", state)


def test_completion_write_failure_keeps_in_memory_and_durable_state_unqualified(tmp_path, monkeypatch):
    tool, adapter, current = run(tmp_path)
    original = tool.write_json
    def fail_complete(path, value):
        if value["active"] is None: raise OSError("PRIVATE_IO_CANARY")
        return original(path, value)
    monkeypatch.setattr(tool, "write_json", fail_complete)
    with pytest.raises(ValueError): current.execute("preflight")
    assert adapter.calls == ["preflight"]
    assert current.report()["passed"] is False
    resumed = tool.RuntimeRun(tmp_path / "run.json", adapter, code_sha256="a" * 64)
    assert resumed.report()["failed"] == 1 and resumed.report()["passed"] is False


@pytest.mark.parametrize("fault", ["empty", "lost_original"])
def test_preflight_cannot_capture_a_baseline_that_already_lost_source_documents(tmp_path, monkeypatch, fault):
    _, adapter, _ = member_adapter(tmp_path)
    from test_scripts import replica_runtime_acceptance as live, replica_runtime_faults as faults
    images = {role: "sha256:" + "2" * 64 for role in ("frontend", "api", "worker")}
    adapter.backup = SimpleNamespace(plan=SimpleNamespace(images=images, counts=SimpleNamespace(documents=20)))
    adapter.authority = SimpleNamespace(source=SimpleNamespace(journal={"baseline_documents": [{"id": "original", "sha256": "known"}]}))
    class Kube:
        def inventory(self): return []
        def python(self, pod, source):
            if "BASELINE" in source or "documents" in source:
                return [] if fault == "empty" else [{"id": str(i), "sha256": "new"} for i in range(20)]
            return 11
    adapter.kube = Kube()
    monkeypatch.setattr(live, "validate_inventory", lambda *args: {"images": images})
    monkeypatch.setattr(live, "verify_runtime", lambda *args: {"fingerprint": adapter.manifest["runtime_fingerprint"]})
    monkeypatch.setattr(live, "role_pods", lambda *args: [{"metadata": {"name": "api"}}])
    monkeypatch.setattr(faults, "FaultProbe", lambda *args: object())
    monkeypatch.setattr(faults, "require_quiescent", lambda *args: None)
    with pytest.raises(ValueError): adapter.baseline()


def test_member_is_stamped_with_parent_run_before_its_first_save(tmp_path, monkeypatch):
    _, adapter, state = member_adapter(tmp_path)
    adapter.config = {}; adapter.paths = {name: tmp_path / name for name in ("source_journal", "archive", "key_file")}
    seen = []
    class Child:
        def __init__(self, *args, **kwargs): self.journal = None
        def save(self, **updates): seen.append(copy.deepcopy(self.journal)); self.journal.update(updates)
    for name, cls_name in (("replica_load", "ReplicaLoad"), ("replica_worker_loss", "WorkerLoss"), ("replica_postgres_partition", "PostgresPartition")):
        replacement = ModuleType(name); setattr(replacement, cls_name, Child)
        monkeypatch.setitem(sys.modules, name, replacement)
    child = adapter.child("worker", state)
    child.journal = {"phase": "prepared"}; child.save()
    assert seen == [{"phase": "prepared", "runtime_run_id": state["execution_id"]}]


def test_member_progress_is_sent_to_stderr_so_entrypoint_stdout_stays_one_report(tmp_path, monkeypatch, capsys):
    tool = module()
    class Driver:
        def __init__(self, *args, **kwargs): pass
        def execute(self, *args, **kwargs):
            print('{"phase":"member-progress"}')
            return {"passed": False}
    monkeypatch.setattr(tool, "OwnedAdapter", lambda *args: object())
    monkeypatch.setattr(tool, "RuntimeRun", Driver)
    assert tool.execute_owned_runtime({}, tmp_path / "run.json", "worker.inject") == {"passed": False}
    printed = capsys.readouterr()
    assert printed.out == "" and json.loads(printed.err)["phase"] == "member-progress"


def test_failed_probe_outcome_is_retained_privately_for_same_stage_inspection(tmp_path):
    _, adapter, current = run(tmp_path)
    outcome = {"passed": False, "checks": [{"name": "runtime.document_roundtrip", "status": "failed"}],
        "private": "PRIVATE_SECRET_CANARY"}
    adapter.execute = lambda *args: outcome
    with pytest.raises(ValueError): current.execute("preflight")
    assert current.state["active"].get("outcome") == outcome
    assert "PRIVATE_SECRET_CANARY" not in json.dumps(current.report())


def test_entrypoint_preserves_other_suite_gates_even_after_complete_owned_runtime(monkeypatch):
    from tests.test_replica_acceptance_safety import manifest
    from test_scripts import run_replica_acceptance as runner
    assert hasattr(runner, "execute_owned_runtime"), "The common entrypoint must execute the owned runtime driver"
    result = {"passed": True, "execution_id": "c" * 32, "checks": [{"name": "runtime." + stage, "status": "passed"} for stage in EXPECTED],
        "failed": 0, "skipped_required": 0, "durations": {}, "recovery_point_age_seconds": 30}
    calls = []
    def execute(*args, **kwargs): calls.append(True); return copy.deepcopy(result)
    monkeypatch.setattr(runner, "execute_owned_runtime", execute)
    runtime = runner.build_report(manifest(), "runtime", live=True, owned_stage="final", owned_journal=Path("private/run.json"))
    assert runtime["passed"] is True
    all_suites = runner.build_report(manifest(), "all", live=True, owned_stage="final", owned_journal=Path("private/run.json"))
    assert all_suites["passed"] is False and all_suites["skipped_required"] > 0
    assert len(calls) == 2
    result["checks"] = []
    empty = runner.build_report(manifest(), "runtime", live=True, owned_stage="final", owned_journal=Path("private/run.json"))
    assert empty["passed"] is False


@pytest.mark.parametrize("fault", ["namespace", "profile", "frontend", "paths"])
def test_owned_manifest_rejects_unsafe_binding_before_adapter_execution(tmp_path, fault):
    tool = module()
    from tests.test_replica_acceptance_safety import manifest
    value = manifest()
    value.update(namespace_uid="11111111-1111-1111-1111-111111111111", runtime_fingerprint="d" * 64,
        frontend_url="http://127.0.0.1:25459", owned_runtime={"config": str(tmp_path / "config.json"),
        "source_journal": str(tmp_path / "source.json"), "archive": str(tmp_path / "backup"), "key_file": str(tmp_path / "key.bin")})
    from tests.test_replica_backup_coordinator import config
    inputs = config(); inputs.update(run_id="abcdefgh",namespace="replica-test-abcdefgh",context="kind-okf-policy",
        database="replica_test_abcdefgh",collection="replica_test_abcdefgh",bucket="replica-test-abcdefgh",prefix="replica_test/abcdefgh/application/")
    value.update(context=inputs["context"], installation_id=inputs["installation_id"],
        application_release=inputs["application_release"], storage_release=inputs["storage_release"],
        simulation_username="replica.admin", frontend_url=f"http://127.0.0.1:{inputs['ports']['frontend']}")
    if fault == "namespace": inputs["namespace"] = "default"
    elif fault == "profile": value["profile"] = "resilient"
    elif fault == "frontend": value["frontend_url"] = "https://customer.invalid"
    else: value["owned_runtime"]["command"] = "arbitrary-program"
    (tmp_path / "config.json").write_text(json.dumps(inputs))
    with pytest.raises(ValueError): tool.OwnedAdapter(value, tmp_path / "run.json")
