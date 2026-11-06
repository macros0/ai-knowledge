"""A scoped current-profile fault must have its recovery authority before injection."""
import base64
import copy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.test_replica_backup import KEY, bound_writer, fixture_plan
from tests.test_replica_image_upgrade import api


NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
FP = "d" * 64
UID = "11111111-1111-1111-1111-111111111111"


def module():
    path = Path(__file__).resolve().parents[2] / "scripts/kubernetes/replica_fault_authority.py"
    assert path.is_file(), "Scoped current-profile fault authority is missing"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("fault_authority_test", path)
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def fixture(tmp_path, monkeypatch, tool, *, age=60):
    from replica_backup import digest
    source = {"phase": "verified", "operation_kind": "same-schema-image-upgrade", "image_upgrade_verified": True,
        "namespace_uid": UID, "fingerprint": "c" * 64,
        "new_identity": {"installation_id": "a" * 32, "runtime_fingerprint": FP},
        "next_runtime": "runtime", "next_identity": "old-identity", "runtime_secret": "private-runtime",
        "operator_secret": "offline-operator", "image": "old-image"}
    path = tmp_path / "source.json"; path.write_text(json.dumps(source))
    plan, payloads = fixture_plan(consistent_at=NOW - timedelta(seconds=age))
    values = plan.model_dump(mode="json"); values["model_vector_sha256"] = digest(FP)
    from app.deployment.replica_backup import BackupPlan
    plan = BackupPlan.model_validate_json(json.dumps(values))
    archive = tmp_path / "backup"
    with bound_writer(archive, plan, KEY, source_guard=lambda: plan.source) as writer:
        for key, data in payloads.items():
            writer.add(key, BytesIO(data))
        writer.finish(now=NOW)
    key_file = tmp_path / "key.bin"; key_file.write_bytes(KEY)
    events = []
    class Source:
        journal = source
        lab = "verified-source-lab"
        def check_inputs(self): events.append("inputs")
        def check_roles(self, **kwargs):
            assert kwargs == {"staged": True, "allow_running": True}
            events.append("roles")
        def submit_job(self, job): events.append(("submit", job))
    monkeypatch.setattr(tool, "load_source_operation", lambda *args: Source())
    owner = SimpleNamespace(config={"installation_id": "a" * 32, "namespace": "replica-test-fixture1",
        "ca_file": str(tmp_path / "ca.pem")}, value={"namespace_uid": UID}, fingerprint=FP,
        path=tmp_path / "fault.json", journal=None)
    return owner, path, archive, key_file, events


def authority(tool, args, **kwargs):
    owner, source, archive, key, _ = args
    return tool.ScopedFaultAuthority(owner, source, archive, key, now=NOW, **kwargs)


@pytest.mark.parametrize("fault", ["unverified", "flag", "namespace", "installation", "fingerprint", "kind"])
def test_unverified_or_foreign_source_refuses_before_factory_or_mutation(tmp_path, monkeypatch, fault):
    tool = module(); args = fixture(tmp_path, monkeypatch, tool)
    source = json.loads(args[1].read_bytes())
    if fault == "unverified": source["phase"] = "running"
    elif fault == "flag": source["image_upgrade_verified"] = False
    elif fault == "namespace": source["namespace_uid"] = "foreign"
    elif fault == "installation": source["new_identity"]["installation_id"] = "e" * 32
    elif fault == "fingerprint": source["new_identity"]["runtime_fingerprint"] = "e" * 64
    else: source["operation_kind"] = "unexpected"
    args[1].write_text(json.dumps(source))
    with pytest.raises(ValueError): authority(tool, args)
    assert args[-1] == []


@pytest.mark.parametrize("fault", ["old", "wrong_key", "missing", "foreign_path"])
def test_recovery_point_required_before_initial_fault_admission(tmp_path, monkeypatch, fault):
    tool = module(); args = list(fixture(tmp_path, monkeypatch, tool, age=86401 if fault == "old" else 60))
    if fault == "wrong_key": args[3].write_bytes(b"x" * 32)
    elif fault == "missing": args[2] = tmp_path / "absent"
    elif fault == "foreign_path":
        other = tmp_path / "outside"; other.mkdir()
        selected = other / "source.json"; selected.write_bytes(args[1].read_bytes()); args[1] = selected
    with pytest.raises(ValueError): authority(tool, args)
    assert not any(isinstance(item, tuple) for item in args[-1])


def test_authority_reuses_source_pin_guards_and_records_exact_provenance(tmp_path, monkeypatch):
    tool = module(); args = fixture(tmp_path, monkeypatch, tool)
    current = authority(tool, args)
    assert current.lab == "verified-source-lab"
    current.check(inject=True)
    assert "inputs" in args[-1] and "roles" in args[-1]
    assert current.record["backup_id"] == "b" * 32
    assert len(current.record["source_journal_sha256"]) == 64
    assert "PRIVATE_SOURCE_CONTENT" not in json.dumps(current.record)
    args[1].write_text(args[1].read_text() + " ")
    with pytest.raises(ValueError): current.check()


def test_resume_refuses_changed_provenance_but_does_not_reapply_admission_age_limit(tmp_path, monkeypatch):
    tool = module(); args = fixture(tmp_path, monkeypatch, tool)
    current = authority(tool, args)
    args[0].journal = {"fault_authority": copy.deepcopy(current.record)}
    resumed = tool.ScopedFaultAuthority(args[0], args[1], args[2], args[3], now=NOW + timedelta(days=2))
    assert resumed.record == current.record
    args[0].journal["fault_authority"]["source_journal_sha256"] = "e" * 64
    with pytest.raises(ValueError): authority(tool, args)


def test_release_uses_current_runtime_with_operator_only_in_readonly_file(tmp_path, monkeypatch):
    tool = module(); args = fixture(tmp_path, monkeypatch, tool)
    current = authority(tool, args)
    value = api()
    source, job = current.release_job(value, name="release", inputs="inputs", operation="f" * 32, owner="owned")
    assert "configure_database(Path('/run/operator-db/url').read_text().strip())" in source
    pod = job["spec"]["template"]["spec"]; container = pod["containers"][0]
    assert container["image"] == "old-image"
    assert container["envFrom"] == [{"secretRef": {"name": "private-runtime"}}, {"configMapRef": {"name": "runtime"}}]
    assert "offline-operator" not in json.dumps(container["envFrom"]) + json.dumps(container["env"])
    assert {"name": "operator-database", "mountPath": "/run/operator-db", "readOnly": True} in container["volumeMounts"]
    assert {"name": "operator-database", "secret": {"secretName": "offline-operator", "defaultMode": 0o440,
        "items": [{"key": "url", "path": "url"}]}} in pod["volumes"]
    current.submit_job(job)
    assert args[-1][-1] == ("submit", job)


@pytest.mark.parametrize("fault", ["image", "runtime", "identity", "secret", "inline"])
def test_release_refuses_runtime_inputs_that_differ_from_verified_current_installation(tmp_path, monkeypatch, fault):
    tool = module(); args = fixture(tmp_path, monkeypatch, tool); current = authority(tool, args)
    value = api(); pod = value["spec"]["template"]["spec"]; container = pod["containers"][0]
    if fault == "image": container["image"] = "other-image"
    elif fault == "runtime": container["envFrom"][1]["configMapRef"]["name"] = "other-runtime"
    elif fault == "identity": pod["volumes"][0]["configMap"]["name"] = "other-identity"
    elif fault == "secret": container["envFrom"][0]["secretRef"]["name"] = "offline-operator"
    else: container["env"].append({"name": "DATABASE_URL", "value": "PRIVATE_SECRET_CANARY"})
    with pytest.raises(ValueError): current.release_job(value, name="release", inputs="inputs", operation="f" * 32, owner="owned")
    assert not any(isinstance(item, tuple) for item in args[-1])


@pytest.mark.parametrize("recipe", ["worker", "partition"])
def test_legacy_fault_refuses_scoped_runtime_before_even_starting_fault_probe(monkeypatch, recipe):
    from tests.test_replica_worker_loss import module as worker_module
    from tests.test_replica_postgres_partition import module as partition_module
    tool = worker_module() if recipe == "worker" else partition_module()
    cls = tool.WorkerLoss if recipe == "worker" else tool.PostgresPartition
    value = cls.__new__(cls); value.journal = None; value.authority = None
    encoded = base64.b64encode(b"postgresql://scoped_role:PRIVATE_SECRET_CANARY@postgres/replica_test_fixture1").decode()
    calls = []
    class Kube:
        def run(self, *args):
            calls.append(args)
            assert args[0] == "get"
            return json.dumps(api() if args[1] == "deploy" else {"data": {"DATABASE_URL": encoded}})
    value.kube = Kube()
    def forbidden(*args, **kwargs): raise AssertionError("Fault probe must not start before authority preflight")
    monkeypatch.setattr(tool, "FaultProbe", forbidden)
    with pytest.raises(ValueError, match="scoped"):
        value.inject()
    assert calls and all(call[0] == "get" for call in calls)


@pytest.mark.parametrize("fault", [None, "uid", "image", "count", "credential", "model_url", "model_delay"])
def test_restart_guard_allows_only_exact_source_or_its_owned_model_delay(fault):
    from tests.test_replica_worker_loss import module as worker_module
    tool = worker_module()
    assert hasattr(tool, "assert_recovery_role"), "A lost restart response needs a guarded resumable start"
    baseline = api(); baseline["spec"]["replicas"] = 2
    current = copy.deepcopy(baseline); current["spec"]["replicas"] = 0
    model = fault in {"model_url", "model_delay"}
    if model:
        for value in (current, baseline): value["spec"]["replicas"] = 0 if value is current else 1
        baseline["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "MODEL_STUB_SCENARIO", "value": "success"}]
        env = [{"name": "MODEL_STUB_SCENARIO", "value": "delay"}, {"name": "MODEL_STUB_DELAY_SECONDS", "value": "30"}]
        current["spec"]["template"]["spec"]["containers"][0]["env"] = env
        if fault == "model_url": env.append({"name": "MODEL_URL", "value": "foreign"})
    elif fault == "uid": current["metadata"]["uid"] = "changed"
    elif fault == "image": current["spec"]["template"]["spec"]["containers"][0]["image"] = "other"
    elif fault == "count": current["spec"]["replicas"] = 1
    elif fault == "credential": current["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "DATABASE_URL", "value": "foreign"})
    if fault in {None, "model_delay"}:
        tool.assert_recovery_role(current, baseline, model=model)
    else:
        with pytest.raises(ValueError): tool.assert_recovery_role(current, baseline, model=model)


@pytest.mark.parametrize("bad_receipt", [False, True])
def test_start_resumes_partial_counts_without_resubmitting_release_or_restarting_ready_roles(monkeypatch, bad_receipt):
    from tests.test_replica_worker_loss import module as worker_module
    tool = worker_module(); value = tool.WorkerLoss.__new__(tool.WorkerLoss)
    assert hasattr(value, "start"), "Restart after a released Job needs its own resumable phase"
    deployments = {}
    for role in ("api", "worker", "frontend", "model-stub"):
        deployments[role] = api(); deployments[role]["metadata"].update(name=role, uid=role)
        deployments[role]["spec"]["replicas"] = 1 if role == "model-stub" else 2
        if role == "model-stub":
            deployments[role]["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "MODEL_STUB_SCENARIO", "value": "success"}]
    live = copy.deepcopy(deployments)
    live["worker"]["spec"]["replicas"] = live["frontend"]["spec"]["replicas"] = 0
    calls = []
    class Kube:
        def run(self, *args):
            if args[0] == "get": return json.dumps(live[args[2]])
            assert args[0] == "rollout", "Ready model/API must not be patched again"
            return ""
        def inventory(self): return []
        def python(self, *args):
            return {"epoch": 11, "operation_id": "operation", "enabled": False, "audit_count": 1}
    class Lab:
        def scale(self, role, before, after):
            assert live[role]["spec"]["replicas"] == before
            live[role]["spec"]["replicas"] = after; calls.append(role)
    value.kube, value.lab, value.authority, value.value = Kube(), Lab(), None, {}
    value.fingerprint = FP
    value.journal = {"phase": "released", "epoch": 11, "operation_id": "operation", "proof_digest": "e" * 64,
        "old_deployments": deployments, "release_receipt": {"epoch": 12 if bad_receipt else 11, "corpus_unchanged": True, "released": True}}
    value.preflight = lambda phase: None
    value.save = lambda **kwargs: value.journal.update(kwargs)
    monkeypatch.setattr(tool, "validate_inventory", lambda *args: {"replicas": {"api": 2, "worker": 2, "frontend": 2}})
    monkeypatch.setattr(tool, "verify_runtime", lambda *args: {"fingerprint": FP})
    monkeypatch.setattr(tool, "role_pods", lambda *args: [{"metadata": {"name": "api"}}])
    if bad_receipt:
        with pytest.raises(ValueError): value.start()
        assert calls == [] and value.journal["phase"] == "released"
    else:
        value.start()
        assert calls == ["worker", "frontend"] and value.journal["phase"] == "running"
        value.start()
        assert calls == ["worker", "frontend"]


@pytest.mark.parametrize("recipe", ["worker", "partition"])
def test_explicit_preflight_cli_never_dispatches_injection(tmp_path, monkeypatch, recipe):
    from tests.test_replica_worker_loss import module as worker_module
    from tests.test_replica_postgres_partition import module as partition_module
    tool = worker_module() if recipe == "worker" else partition_module()
    calls = []
    class Recipe:
        authority = object()
        def __init__(self, *args, **kwargs): pass
        def preflight(self, phase): calls.append(phase)
        def inject(self): raise AssertionError("Read-only preflight cannot inject a fault")
    monkeypatch.setattr(tool, "WorkerLoss" if recipe == "worker" else "PostgresPartition", Recipe)
    config = tmp_path / "config.json"; config.write_text("{}")
    journal = tmp_path / "fault.json"
    monkeypatch.setattr(sys, "argv", ["fault", "preflight", "--apply", "--config", str(config),
        "--namespace-uid", UID, "--expected-fingerprint", FP, "--journal", str(journal)])
    assert tool.main() == 0 and calls == ["inject"]
    assert not journal.exists()


@pytest.mark.parametrize("fault", [None, "fingerprint", "owners", "provider", "documents"])
def test_scoped_preflight_requires_the_actual_current_quiescent_runtime(monkeypatch, fault):
    from tests.test_replica_worker_loss import module as worker_module
    tool = worker_module(); value = tool.WorkerLoss.__new__(tool.WorkerLoss)
    value.authority = SimpleNamespace(check=lambda **kwargs: None)
    value.kube, value.value, value.fingerprint = object(), {}, FP
    class Probe:
        identity = {"fingerprint": "e" * 64 if fault == "fingerprint" else FP}
        def state(self):
            return {"model_owners": int(fault == "owners"), "running_documents": int(fault == "documents"),
                "running_chats": 0, "provider": {"active": int(fault == "provider")}}
    monkeypatch.setattr(tool, "FaultProbe", lambda *args: Probe())
    if fault is None:
        assert isinstance(value.preflight("inject"), Probe)
    else:
        with pytest.raises(ValueError): value.preflight("inject")
