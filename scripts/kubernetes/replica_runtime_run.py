"""Durable staged acceptance of the current owned basic managed-S3 installation.

Only explicit stages execute. Unknown outcomes are never replayed as another
fault; operator transport refresh happens between stages, outside retained SQL.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import traceback
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from filelock import FileLock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_scripts.replica_runtime_plan import SCOPE, STAGES


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def recipe_digest():
    files = [*Path(__file__).parent.glob("replica_*.py"),
        *(ROOT / "backend/test_scripts").glob("replica_*.py"), ROOT / "backend/test_scripts/run_replica_acceptance.py"]
    return digest({str(path.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


def read_json(path, *, limit=16 * 1024 * 1024):
    from app.deployment.replica_backup import _regular_open
    path = Path(path)
    marker = path.with_name(path.name + ".writing")
    from app.services.diagnostics.paths import assert_no_links
    assert_no_links(marker, path.parent)
    if marker.exists(): raise ValueError("Runtime status write is uncertain")
    with _regular_open(path, path.parent) as stream:
        raw = stream.read(limit + 1)
    assert_no_links(marker, path.parent)
    if marker.exists(): raise ValueError("Runtime status changed while reading")
    if len(raw) > limit:
        raise ValueError("Runtime journal exceeds its bound")
    return json.loads(raw)


def write_json(path, value):
    from app.deployment.replica_backup import _sync_directory
    from app.services.diagnostics.paths import assert_no_links
    path = Path(path)
    assert_no_links(path, path.parent)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".pending")
    marker = path.with_name(path.name + ".writing")
    assert_no_links(marker, path.parent)
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(b"runtime-status-write-in-progress\n"); output.flush(); os.fsync(output.fileno())
        _sync_directory(path.parent)
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, sort_keys=True, allow_nan=False)
            output.write("\n"); output.flush(); os.fsync(output.fileno())
        assert_no_links(path, path.parent)
        os.replace(temporary, path)
        _sync_directory(path.parent)
        # Status is durable; a marker resurrected by crash is conservative.
        marker.unlink()
    finally:
        if temporary.exists(): temporary.unlink()


def now():
    return datetime.now(timezone.utc).isoformat()


class RuntimeRun:
    def __init__(self, path, adapter, *, code_sha256=None):
        from app.services.diagnostics.paths import assert_no_links
        self.path, self.adapter = Path(path).absolute(), adapter
        if self.path.parent != Path(adapter.private).resolve() or not self.path.parent.is_dir():
            raise ValueError("Runtime journal must be in the existing protected directory")
        assert_no_links(self.path, self.path.parent)
        self.code = code_sha256 or recipe_digest()
        if not re.fullmatch(r"[0-9a-f]{64}", self.code): raise ValueError("Recipe digest required")
        exists = self.path.exists()
        self.state = read_json(self.path) if exists else {"version": 1, "execution_id": uuid4().hex,
            "code_sha256": self.code, "binding_sha256": None, "admission": None,
            "created_at": now(), "completed": {}, "active": None}
        self.validate()
        binding = adapter.bind(self.state)
        selected = digest(binding)
        if exists and self.state["binding_sha256"] != selected:
            raise ValueError("Runtime source/profile/input binding changed")
        self.state.update(binding_sha256=selected, admission=binding.get("admission"))

    def validate(self):
        state = self.state
        if (type(state.get("version")) is not int or state["version"] != 1 or state.get("code_sha256") != self.code
                or not re.fullmatch(r"[0-9a-f]{32}", state.get("execution_id", ""))
                or not isinstance(state.get("completed"), dict)):
            raise ValueError("Runtime journal version/code/identity differs")
        completed = state["completed"]
        if set(completed) != set(STAGES[:len(completed)]):
            raise ValueError("Runtime stages are missing or out of order")
        if any(not isinstance(value, dict) or value.get("passed") is not True for value in completed.values()):
            raise ValueError("Runtime stage completion is unverified")
        active = state.get("active")
        if active is not None and (not isinstance(active, dict) or len(completed) == len(STAGES) or active.get("stage") != STAGES[len(completed)]):
            raise ValueError("An unknown or foreign runtime stage is active")

    def save(self):
        write_json(self.path, self.state)

    def complete(self, stage, outcome):
        if not isinstance(outcome, dict) or outcome.get("passed") is not True:
            raise ValueError("Runtime stage did not pass")
        active = self.state["active"]
        finished = now()
        duration = (datetime.fromisoformat(finished) - datetime.fromisoformat(active["started_at"])).total_seconds()
        candidate = copy.deepcopy(self.state)
        candidate["completed"][stage] = {"passed": True, "started_at": active["started_at"],
            "finished_at": finished, "seconds": max(0, duration), "result": outcome}
        candidate["active"] = None
        write_json(self.path, candidate)
        self.state = candidate

    def execute(self, stage, *, apply=False, resume=False):
        from app.services.diagnostics.paths import assert_no_links
        if stage not in STAGES: raise ValueError("Unknown runtime stage")
        if stage not in {"preflight", "final"} and not apply:
            raise ValueError("Explicit apply required for a mutating runtime stage")
        lock = self.path.with_name(self.path.name + ".lock")
        assert_no_links(lock, self.path.parent)
        with FileLock(lock, timeout=5):
            if self.path.exists() and read_json(self.path) != self.state:
                raise ValueError("Runtime journal changed; observe the same run")
            self.validate()
            if stage in self.state["completed"]: return self.report()
            if stage != STAGES[len(self.state["completed"])]:
                raise ValueError("Complete the preceding runtime stage first")
            if self.state["active"] is not None:
                observed = self.adapter.observe(stage, self.state)
                if observed is not None:
                    self.complete(stage, observed)
                    return self.report()
                if not resume or not self.adapter.can_resume(stage, self.state):
                    raise ValueError("Runtime outcome uncertain; inspect the same child operation")
            # Pure transport checks happen before publishing mutation intent.
            self.adapter.before(stage, self.state)
            if self.state["active"] is None:
                self.state["active"] = {"stage": stage, "started_at": now()}
                self.save()
            try:
                outcome = self.adapter.execute(stage, self.state)
                if not isinstance(outcome, dict) or outcome.get("passed") is not True:
                    self.state["active"]["outcome"] = outcome
                self.complete(stage, outcome)
            except Exception as exc:  # noqa: BLE001 -- preserve intent, never expose raw input/provider exceptions.
                self.state["active"]["error_type"] = type(exc).__name__
                self.state["active"]["locations"] = [Path(f.filename).name + ":" + str(f.lineno)
                    for f in traceback.extract_tb(exc.__traceback__)[-3:]]
                try: self.save()
                except OSError: pass  # Existing write-intent marker still blocks a false ACK.
                raise ValueError("Runtime stage interrupted; inspect the same journal") from None
        return self.report()

    def report(self):
        self.validate()
        completed = self.state["completed"]
        active = self.state["active"]
        checks = [{"name": "runtime." + stage, "status": "passed" if stage in completed else "pending",
            **({"evidence_sha256": digest(completed[stage]["result"])} if stage in completed else {})} for stage in STAGES]
        failed = int(active is not None and "error_type" in active)
        if failed: checks[len(completed)]["status"] = "failed"
        return {"scope": SCOPE, "execution_id": self.state["execution_id"], "runner_sha256": self.code,
            "binding_sha256": self.state["binding_sha256"], "passed": len(completed) == len(STAGES) and active is None,
            "checks": checks, "failed": failed, "skipped_required": len(STAGES) - len(completed),
            "durations": {stage: row["seconds"] for stage, row in completed.items()},
            "recovery_point_age_seconds": self.state.get("recovery_point_age_seconds"),
            "verified_at": completed.get("final", {}).get("finished_at"), "production_schedule": "disabled"}


class OwnedAdapter:
    def __init__(self, manifest, journal):
        from replica_backup import validate_config
        from test_scripts.run_replica_acceptance import validate_manifest
        validate_manifest(manifest)
        paths = manifest.get("owned_runtime")
        if not isinstance(paths, dict) or set(paths) != {"config", "source_journal", "archive", "key_file"}:
            raise ValueError("Explicit owned runtime file references required")
        if any(not isinstance(value, str) or not value for value in paths.values()):
            raise ValueError("Runtime references must be paths")
        self.paths = {name: Path(value).absolute() for name, value in paths.items()}
        self.config = read_json(self.paths["config"])
        validate_config(self.config)
        self.private = self.paths["config"].resolve().parent
        self.journal = Path(journal).absolute()
        self.manifest = manifest
        if (manifest["profile"] != "basic" or self.journal.parent != self.private
                or self.journal in self.paths.values() or self.paths["source_journal"].parent != self.private
                or any(manifest.get(name) != self.config[name] for name in ("namespace", "run_id", "context", "installation_id", "application_release", "storage_release"))
                or manifest.get("frontend_url") != f"http://127.0.0.1:{self.config['ports']['frontend']}"
                or manifest.get("simulation_username") != "replica.admin"
                or not re.fullmatch(r"[0-9a-f-]{36}", manifest.get("namespace_uid", ""))
                or not re.fullmatch(r"[0-9a-f]{64}", manifest.get("runtime_fingerprint", ""))):
            raise ValueError("Owned runtime manifest/config/scope differs")
        self.value = {**self.config, "namespace_uid": manifest["namespace_uid"], "frontend_url": manifest["frontend_url"],
            "simulation_username": "replica.admin"}

    def bind(self, state):
        from app.deployment.replica_backup import verify_backup
        from replica_fault_authority import ScopedFaultAuthority
        owner = SimpleNamespace(config=self.config, value=self.value, path=self.journal,
            fingerprint=self.manifest["runtime_fingerprint"], journal={"fault_authority": state["admission"]} if state.get("admission") else None)
        self.authority = ScopedFaultAuthority(owner, self.paths["source_journal"], self.paths["archive"], self.paths["key_file"])
        with self.paths["key_file"].open("rb") as stream: key = stream.read(33)
        self.backup = verify_backup(self.paths["archive"], key)
        self.kube = self.authority.source.kube
        return {"manifest_sha256": digest(self.manifest), "config_sha256": digest(self.config),
            "admission": self.authority.record, "images": dict(self.backup.plan.images), "scope": SCOPE}

    def child_path(self, member, state, *, report=False):
        if member not in {"worker", "partition", "load"}: raise ValueError("Unknown runtime member")
        return self.private / ("runtime-" + state["execution_id"] + "-" + member + ("-report.json" if report else ".json"))

    def child(self, member, state):
        from replica_load import ReplicaLoad
        from replica_postgres_partition import PostgresPartition
        from replica_worker_loss import WorkerLoss
        cls = {"worker": WorkerLoss, "partition": PostgresPartition, "load": ReplicaLoad}[member]
        child = cls(self.config, self.value["namespace_uid"], self.child_path(member, state), self.manifest["runtime_fingerprint"],
            source_journal=self.paths["source_journal"], archive=self.paths["archive"], key_file=self.paths["key_file"])
        if child.journal is not None and child.journal.get("runtime_run_id") != state["execution_id"]:
            raise ValueError("Existing member is not owned by this runtime run")
        original_save = child.save
        def save(**updates):
            if child.journal.get("runtime_run_id", state["execution_id"]) != state["execution_id"]:
                raise ValueError("Member runtime ownership changed")
            child.journal["runtime_run_id"] = state["execution_id"]
            original_save(**updates)
        child.save = save
        return child

    def before(self, stage, state):
        import httpx
        self.authority.check()
        if stage in {"worker.inject", "partition.inject", "load.prepare"} and self.child_path(stage.split(".")[0], state).exists():
            raise ValueError("A new member cannot adopt a pre-existing child journal")
        if stage in {"smoke", "after-idle", "active", "worker.inject", "worker.verify", "partition.inject", "partition.verify", "load.single", "load.dual", "load.verify"}:
            with httpx.Client(trust_env=False, timeout=15) as client:
                client.get(self.value["frontend_url"] + "/health").raise_for_status()

    @staticmethod
    def checked(rows, required):
        names = [row.get("name") for row in rows]
        if not required: raise ValueError("A nonempty required runtime check set is mandatory")
        passed = len(set(names)) == len(names) and set(required) <= set(names) and all(row.get("status") == "passed" for row in rows)
        return {"passed": passed, "checks": rows}

    def baseline(self):
        from replica_worker_loss import BASELINE_SOURCE
        from test_scripts.replica_runtime_acceptance import (
            role_pods,
            validate_inventory,
            verify_runtime,
        )
        from test_scripts.replica_runtime_faults import FaultProbe, require_quiescent
        probe = FaultProbe(self.kube, self.value)
        require_quiescent(probe)
        objects = self.kube.inventory(); ready = validate_inventory(objects, self.value)
        if (ready["images"] != dict(self.backup.plan.images)
                or verify_runtime(self.kube, objects, self.value)["fingerprint"] != self.manifest["runtime_fingerprint"]):
            raise ValueError("Actual current images or common identity differ from the recovery point")
        pod = role_pods(objects, "api")[0]["metadata"]["name"]
        documents = self.kube.python(pod, BASELINE_SOURCE)
        if not isinstance(documents, list) or not documents or len(documents) < self.backup.plan.counts.documents:
            raise ValueError("The nonempty recovery corpus is missing before acceptance")
        by_id = {row["id"]: row for row in documents}
        retained = self.authority.source.journal.get("baseline_documents", [])
        if len(by_id) != len(documents) or any(by_id.get(row["id"]) != row for row in retained):
            raise ValueError("The verified source corpus changed before acceptance")
        epoch = self.kube.python(pod, "import json;from app.config import get_settings;from app.deployment.runtime_identity import enforce_runtime_identity;s=get_settings();enforce_runtime_identity(s);print(json.dumps(s._maintenance_epoch))")
        return {"documents": documents, "images": ready["images"], "epoch": epoch}

    def execute(self, stage, state):
        from test_scripts.replica_runtime_acceptance import (
            document_roundtrip,
            idle_rollout,
            probe_runtime,
        )
        from test_scripts.replica_runtime_faults import probe_active_faults
        if stage == "preflight":
            self.authority.check(inject=True)
            state["baseline"] = self.baseline()
            state["recovery_point_age_seconds"] = (datetime.now(timezone.utc) - self.backup.plan.source.consistent_at).total_seconds()
            return {"passed": True, "baseline": state["baseline"]}
        if stage == "smoke":
            return self.checked(probe_runtime(self.value, ssh_host="ubuntu@10.10.1.52", known_hosts=ROOT / "tests/tmp/replica-lab-known_hosts"),
                ["runtime." + name for name in ("scope", "role_inventory", "common_identity", "shared_session", "frontend_storage_boundary", "document_roundtrip", "memory_samples")])
        if stage == "idle": return {"passed": True, "evidence": idle_rollout(self.kube, self.value)}
        if stage == "after-idle": return {"passed": True, "evidence": document_roundtrip(self.value)}
        if stage == "active":
            return self.checked(probe_active_faults(self.kube, self.value), ["runtime." + name for name in
                ("active_fault_scope", "concurrent_cancel_delete", "model_capacity_exhaustion", "active_fixture_restored")])
        if stage == "final":
            return self.final(state)
        member, action = stage.split(".", 1)
        child = self.child(member, state)
        if stage == "partition.finish":
            if child.journal["phase"] == "healing": child.finish()
            if child.journal["phase"] in {"quarantine-proven", "held", "reconciled"}:
                if child.journal["phase"] != "reconciled": child.recover()
                child.release()
            elif child.journal["phase"] == "released": child.start()
        elif stage == "partition.restore" and child.journal["phase"] == "running":
            self.authority.check(inject=True)
            self.baseline()
        elif action == "verify":
            child.verify(self.child_path(member, state, report=True))
        elif member == "load" and action in {"single", "dual"}:
            workers = 1 if action == "single" else 2
            case = (child.journal or {}).get("cases", {}).get("workers-" + str(workers))
            if case is not None and case.get("status") == "running": child.observe(workers)
            else: child.case(workers)
        elif action == "release" and child.journal["phase"] in {"released", "running"}:
            child.start()
        else:
            getattr(child, action)()
        outcome = self.observe(stage, state)
        if outcome is None: raise ValueError("The same child did not reach its required phase")
        return outcome

    def child_record(self, member, state):
        path = self.child_path(member, state)
        if not path.exists(): return None
        child = read_json(path)
        if (child.get("runtime_run_id") != state["execution_id"] or child.get("namespace_uid") != self.value["namespace_uid"]
                or child.get("fingerprint") != self.manifest["runtime_fingerprint"]):
            raise ValueError("Runtime child belongs to another installation")
        binding = child.get("fault_authority", {})
        expected = state["admission"]
        if any(binding.get(name) != expected[name] for name in ("source_journal_sha256", "backup_id", "backup_authentication", "backup_archive")):
            raise ValueError("Runtime child recovery provenance differs")
        if member != "load" and (not re.fullmatch(r"[0-9a-f]{32}", child.get("operation_id", ""))
                or child.get("started_at", state["created_at"]) < state["created_at"]):
            raise ValueError("Old runtime child cannot qualify this run")
        return child

    def observe(self, stage, state):
        if "." not in stage: return None
        member, action = stage.split(".", 1)
        child = self.child_record(member, state)
        if child is None: return None
        expected = {"worker": {"inject": {"waiting-for-quarantine"}, "observe": {"quarantine-proven"}, "recover": {"reconciled"},
            "release": {"running"}, "verify": {"verified"}},
            "partition": {"inject": {"partition-applied", "partition-proven"}, "confirm": {"partition-proven"}, "observe": {"reclaim-proven"},
                "heal": {"confirmed-return", "quarantine-proven"}, "finish": {"confirmed-return", "running"}, "restore": {"normal-running", "running"}, "verify": {"verified"}},
            "load": {"prepare": {"prepared"}}}
        if member == "load" and action in {"single", "dual"}:
            passed = child.get("cases", {}).get("workers-" + ("1" if action == "single" else "2"), {}).get("status") == "passed"
        else:
            passed = child.get("phase") in expected.get(member, {}).get(action, set())
        if action == "verify":
            if child.get("phase") != "verified": return None
            report = read_json(self.child_path(member, state, report=True))
            if (report.get("namespace_uid") != self.value["namespace_uid"]
                    or report.get("runtime_fingerprint", report.get("fingerprint")) != self.manifest["runtime_fingerprint"]):
                raise ValueError("Runtime member report is foreign")
            if member != "load" and report.get("operation_id") != child["operation_id"]:
                raise ValueError("Runtime member report operation differs")
            return {"passed": True, "operation_id": child.get("operation_id"), "child_sha256": digest(child), "report_sha256": digest(report)}
        if not passed: return None
        return {"passed": True, "operation_id": child.get("operation_id"), "phase": child["phase"], "child_sha256": digest(child)}

    def can_resume(self, stage, state):
        if "." not in stage: return False
        member, action = stage.split(".", 1); child = self.child_record(member, state)
        if child is None: return False
        if member == "worker":
            return action in {"observe", "recover", "release", "verify"} and child.get("phase") in {
                "waiting-for-quarantine", "quarantine-proven", "held", "reconciled", "released", "running", "verified"}
        if member == "load" and action in {"single", "dual"}:
            return child.get("cases", {}).get("workers-" + ("1" if action == "single" else "2"), {}).get("status") == "running"
        return member == "partition" and action in {"confirm", "observe", "heal", "finish", "restore", "verify"}

    def final(self, state):
        current = self.baseline()
        original = {row["id"]: row for row in state["baseline"]["documents"]}
        observed = {row["id"]: row for row in current["documents"]}
        if any(observed.get(key) != value for key, value in original.items()):
            raise ValueError("Original runtime corpus changed during acceptance")
        members = {member: self.child_record(member, state) for member in ("worker", "partition", "load")}
        if any(value is None or value.get("phase") != "verified" for value in members.values()):
            raise ValueError("Every current-run member must be verified")
        worker, partition = members["worker"], members["partition"]
        partition_epoch = partition.get("epoch", partition["source_epoch"])
        if (worker["source_epoch"] != state["baseline"]["epoch"] or worker["epoch"] <= worker["source_epoch"]
                or partition["source_epoch"] != worker["epoch"] or current["epoch"] != partition_epoch
                or partition_epoch < partition["source_epoch"] or members["load"]["epoch"] != current["epoch"]
                or worker["operation_id"] == partition["operation_id"]):
            raise ValueError("Runtime maintenance epoch/operation chain differs")
        for member in members:
            if self.observe(member + ".verify", state) is None: raise ValueError("Missing current member receipt")
        return {"passed": True, "preserved_documents": len(original), "images": current["images"],
            "initial_epoch": state["baseline"]["epoch"], "final_epoch": current["epoch"],
            "worker_operation": worker["operation_id"], "partition_operation": partition["operation_id"]}


def execute_owned_runtime(manifest, journal, stage, *, apply=False, resume=False):
    # Native recipes emit progress and member reports; the common entrypoint
    # owns the sole stdout report and cannot expose a member PASS as run PASS.
    with redirect_stdout(sys.stderr):
        adapter = OwnedAdapter(manifest, journal)
        return RuntimeRun(journal, adapter).execute(stage, apply=apply, resume=resume)
