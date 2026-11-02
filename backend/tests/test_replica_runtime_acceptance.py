"""Live smoke evidence must stay scoped and cannot certify missing fault tests."""
import copy
import json

import pytest

from test_scripts.replica_runtime_acceptance import validate_live_inputs, validate_inventory


def live_inputs():
    return {"run_id": "abcdefgh", "namespace": "replica-test-abcdefgh", "context": "kind-owned",
        "application_release": "replica-lab", "installation_id": "3" * 32,
        "frontend_url": "http://127.0.0.1:25439", "simulation_username": "replica.admin"}


def inventory():
    objects = []
    for role in ("frontend", "api", "worker"):
        labels = {"app.kubernetes.io/instance": "replica-lab", "okf-run-id": "abcdefgh"}
        objects.append({"kind": "Deployment", "metadata": {"name": role, "labels": labels, "generation": 2},
            "spec": {"replicas": 2}, "status": {"observedGeneration": 2, "availableReplicas": 2,
                "updatedReplicas": 2, "readyReplicas": 2}})
        for index in range(2):
            objects.append({"kind": "Pod", "metadata": {"name": f"{role}-{index}",
                "uid": f"{role}-uid-{index}", "labels": {**labels, "okf-role": role}},
                "status": {"podIP": f"10.0.{len(objects)}.1", "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 0,
                        "imageID": "containerd://sha256:" + "1" * 64}]}})
    objects.append({"kind": "EndpointSlice", "metadata": {"labels": {"kubernetes.io/service-name": "backend"}},
        "endpoints": [{"conditions": {"ready": True}, "targetRef": {"kind": "Pod", "uid": f"api-uid-{i}"}}
            for i in range(2)]})
    return objects


@pytest.mark.parametrize("field,value", [("context", "--production"), ("namespace", "default"),
    ("frontend_url", "http://customer.example"), ("frontend_url", "http://user:secret@127.0.0.1:25439"),
    ("frontend_url", "http://127.0.0.1:25439/other"), ("installation_id", "invalid"),
    ("application_release", "../foreign"), ("simulation_username", "")])
def test_live_input_refuses_ambiguous_or_remote_targets(field, value):
    value_inputs = live_inputs()
    value_inputs[field] = value
    with pytest.raises(ValueError):
        validate_live_inputs(value_inputs)


def test_live_inventory_requires_two_ready_roles_and_api_only_service():
    evidence = validate_inventory(inventory(), live_inputs())
    assert evidence["replicas"] == {"frontend": 2, "api": 2, "worker": 2}
    assert evidence["api_endpoints"] == 2
    assert "10.0." not in json.dumps(evidence)


def test_explicit_single_worker_inventory_preserves_two_api_endpoints():
    objects = inventory()
    objects = [o for o in objects if o.get("metadata", {}).get("name") != "worker-1"]
    worker = next(o for o in objects if o.get("kind") == "Deployment" and o["metadata"]["name"] == "worker")
    worker["spec"]["replicas"] = 1
    for key in ("readyReplicas", "updatedReplicas", "availableReplicas"):
        worker["status"][key] = 1
    result = validate_inventory(objects, live_inputs(), expected_counts={"frontend": 2, "api": 2, "worker": 1})
    assert result["replicas"]["worker"] == 1 and result["api_endpoints"] == 2
    with pytest.raises(ValueError):
        validate_inventory(objects, live_inputs(), expected_counts={"frontend": 2, "api": 2, "worker": 3})


@pytest.mark.parametrize("change", ["worker_endpoint", "stale_rollout", "foreign_release", "not_ready", "retiring_api"])
def test_inventory_refuses_false_ready_or_foreign_membership(change):
    objects = copy.deepcopy(inventory())
    if change == "worker_endpoint":
        objects[-1]["endpoints"][0]["targetRef"]["uid"] = "worker-uid-0"
    elif change == "stale_rollout":
        objects[0]["status"]["observedGeneration"] = 1
    elif change == "foreign_release":
        objects[0]["metadata"]["labels"]["app.kubernetes.io/instance"] = "foreign"
    elif change == "not_ready":
        objects[1]["status"]["containerStatuses"][0]["ready"] = False
    else:
        next(p for p in objects if p.get("metadata", {}).get("uid") == "api-uid-0")["metadata"]["deletionTimestamp"] = "now"
    with pytest.raises(ValueError):
        validate_inventory(objects, live_inputs())


def test_live_smoke_cannot_hide_remaining_runtime_faults(monkeypatch):
    from test_scripts import run_replica_acceptance as runner
    monkeypatch.setattr(runner, "probe_runtime", lambda *_a, **_k: [
        {"name": "runtime.document_roundtrip", "status": "passed"}])
    value = {**live_inputs(), "schema_version": 1, "profile": "basic",
        "database_url": "postgresql+psycopg://replica@db/replica_test_abcdefgh",
        "qdrant_collection": "replica_test_abcdefgh", "bucket_prefix": "replica_test/abcdefgh/"}
    result = runner.build_report(value, "runtime", live=True)
    assert result["passed"] is False
    assert result["skipped_required"] > 0
    assert any(c["name"] == "runtime.document_roundtrip" and c["status"] == "passed" for c in result["checks"])


def test_completed_active_faults_remove_only_their_own_pending_entries(monkeypatch):
    from test_scripts import run_replica_acceptance as runner
    requested = []

    def actual_probes(*_args, **kwargs):
        requested.append(kwargs["active_faults"])
        return [{"name": "runtime.concurrent_cancel_delete", "status": "passed"},
            {"name": "runtime.model_capacity_exhaustion", "status": "passed"}]

    monkeypatch.setattr(runner, "probe_runtime", actual_probes)
    value = {**live_inputs(), "schema_version": 1, "profile": "basic",
        "database_url": "postgresql+psycopg://replica@db/replica_test_abcdefgh",
        "qdrant_collection": "replica_test_abcdefgh", "bucket_prefix": "replica_test/abcdefgh/"}
    report = runner.build_report(value, "runtime", live=True, active_faults=True, idle_faults=True)
    assert requested == [True]
    assert report["passed"] is False
    assert {c["name"] for c in report["checks"] if c["status"] == "pending"} == {
        "runtime.active_worker_loss", "runtime.postgres_partition", "runtime.stale_worker_return"}
    assert len([c for c in report["checks"] if c["name"] == "runtime.model_capacity_exhaustion"]) == 1


def test_foreign_namespace_stops_before_inventory_or_http(monkeypatch):
    from test_scripts import replica_runtime_acceptance as live

    class Foreign:
        def __init__(self, *_args, **_kwargs):
            pass

        def scope(self):
            raise ValueError("CANARY_SECRET")

        def inventory(self):
            pytest.fail("Must not continue after ownership refusal")

    monkeypatch.setattr(live, "Kubernetes", Foreign)
    monkeypatch.setattr(live, "document_roundtrip", lambda *_: pytest.fail("Foreign target must not receive uploads"))
    result = live.probe_runtime(live_inputs())
    assert result == [{"name": "runtime.scope", "status": "failed", "reason": "live_probe_failed"}]
    assert "CANARY_SECRET" not in json.dumps(result)


def test_uncertain_provider_slot_prevents_idle_scale_or_deletion():
    from test_scripts.replica_runtime_acceptance import idle_rollout

    class Busy:
        def scope(self):
            pass

        def inventory(self):
            return inventory()

        def python(self, *_args):
            return {"documents": 0, "providers": 1}

        def run(self, *_args):
            pytest.fail("An uncertain provider must prevent mutation")

    with pytest.raises(ValueError, match="provider"):
        idle_rollout(Busy(), live_inputs())


def test_idle_remote_sql_binds_its_process_identity_before_opening_a_session(monkeypatch):
    from contextlib import contextmanager, redirect_stdout
    from io import StringIO
    import json
    from app import config
    from app.db import session as db_session
    from app.deployment import runtime_identity
    from test_scripts.replica_runtime_acceptance import idle_rollout
    settings, bound = object(), []
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    def bind(value):
        assert value is settings
        bound.append(True)
    monkeypatch.setattr(runtime_identity, "enforce_runtime_identity", bind)
    @contextmanager
    def guarded_session():
        if not bound: raise ValueError("Remote process identity was not bound")
        class Session:
            answers = iter((0, 1))
            def scalar(self, _statement): return next(self.answers)
        yield Session()
    monkeypatch.setattr(db_session, "session_scope", guarded_session)
    class Busy:
        def scope(self): pass
        def inventory(self): return inventory()
        def python(self, _pod, source):
            output = StringIO()
            with redirect_stdout(output): exec(source, {})
            return json.loads(output.getvalue())
        def run(self, *_args): pytest.fail("No mutation is allowed with an uncertain provider slot")
    with pytest.raises(ValueError, match="provider"):
        idle_rollout(Busy(), live_inputs())
    assert bound == [True]


def test_missing_owned_storage_cannot_be_reported_as_network_denial():
    from test_scripts.replica_runtime_acceptance import storage_network_boundary

    class NoNetwork:
        def python(self, *_args):
            pytest.fail("No probes before storage ownership validation")

        run = python

    with pytest.raises(ValueError, match="storage"):
        storage_network_boundary(NoNetwork(), inventory(), live_inputs())
