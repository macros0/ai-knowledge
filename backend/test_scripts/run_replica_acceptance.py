"""Fail-closed isolated acceptance; live runtime smoke never certifies missing faults."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_scripts.replica_runtime_acceptance import probe_runtime

RUNNER_SHA256 = sha256(b"".join((Path(__file__).parent / name).read_bytes()
    for name in ("run_replica_acceptance.py", "replica_runtime_acceptance.py", "replica_runtime_faults.py"))).hexdigest()

SUITES = ("contracts", "frontend", "storage", "runtime", "operations", "all")


def validate_manifest(value: dict) -> None:
    """Validate target identity before any future network access or mutation."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported manifest schema")
    run_id = value.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[a-z0-9]{8,32}", run_id):
        raise ValueError("Invalid test run identity")
    if not isinstance(value.get("profile"), str) or value["profile"] not in {"basic", "resilient"}:
        raise ValueError("Invalid test profile")
    if not isinstance(value.get("context"), str) or not value["context"].strip():
        raise ValueError("Explicit Kubernetes context required")
    if value.get("namespace") != f"replica-test-{run_id}":
        raise ValueError("Isolated namespace required")
    try:
        if not isinstance(value.get("database_url"), str):
            raise ValueError("Database URL must be a string")
        database = urlsplit(value.get("database_url", ""))
        valid_database = (
            database.scheme in {"postgresql", "postgresql+psycopg"}
            and database.hostname and database.path == f"/replica_test_{run_id}"
            and not database.query and not database.fragment
        )
        _ = database.port
    except (ValueError, TypeError):
        valid_database = False
    if not valid_database:
        raise ValueError("Disposable PostgreSQL database required")
    if value.get("qdrant_collection") != f"replica_test_{run_id}":
        raise ValueError("Run-scoped Qdrant collection required")
    if value.get("bucket_prefix") != f"replica_test/{run_id}/":
        raise ValueError("Run-scoped artifact prefix required")


def build_report(manifest: dict, suite: str, *, live=False, ssh_host=None, known_hosts=None, idle_faults=False, active_faults=False) -> dict:
    """Allowlist output; never include raw inputs or arbitrary exceptions."""
    valid = True
    try:
        validate_manifest(manifest)
    except (ValueError, TypeError):
        valid = False
    requested = SUITES[:-1] if suite == "all" else (suite,)
    checks = [{"name": "target_identity", "status": "passed" if valid else "failed"}]
    # Syntactic validation is not a live namespace/PVC/provider probe.
    checks.extend({"name": name, "status": "pending", "reason": "live_probe_not_implemented"}
                  for name in requested)
    if valid and live and "runtime" in requested:
        checks = [c for c in checks if c["name"] != "runtime"]
        checks.extend(probe_runtime(manifest, ssh_host=ssh_host, known_hosts=known_hosts, idle_faults=idle_faults,
            active_faults=active_faults))
        # A successful synthetic smoke cannot replace destructive failure,
        # stale-writer, model-capacity or independent recovery qualification.
        observed = {c["name"] for c in checks}
        checks.extend({"name": "runtime." + name, "status": "pending", "reason": "required_fault_probe_pending"}
            for name in ("active_worker_loss", "postgres_partition", "stale_worker_return",
                "concurrent_cancel_delete", "model_capacity_exhaustion") if "runtime." + name not in observed)
        if not idle_faults:
            checks.append({"name": "runtime.idle_rollout", "status": "pending", "reason": "explicit_idle_faults_required"})
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    return {
        "suite": suite, "run_id": manifest["run_id"] if valid else None,
        "profile": manifest["profile"] if valid else None,
        "revision": revision if re.fullmatch(r"[0-9a-f]{40,64}", revision) else None,
        "runner_sha256": RUNNER_SHA256,
        "checks": checks, "passed": False, "failed": sum(c["status"] == "failed" for c in checks),
        "skipped_required": sum(c["status"] == "pending" for c in checks), "durations": {},
        "recovery_point_age_seconds": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Run scoped synthetic runtime smoke; creates test documents")
    parser.add_argument("--ssh-host", help="Optional pinned user@host for kubectl")
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--idle-faults", action="store_true", help="Also scale API/worker and normally replace idle owned Pods")
    parser.add_argument("--active-faults", action="store_true", help="Also test cancel/delete and model saturation on new synthetic documents")
    args = parser.parse_args(argv)
    try:
        if args.manifest.stat().st_size > 1024 * 1024:
            raise ValueError("Manifest too large")
        value = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Object required")
        if (args.idle_faults or args.active_faults) and not args.live:
            raise ValueError("Fault probes require explicit live execution")
        report = build_report(value, args.suite, live=args.live, ssh_host=args.ssh_host,
            known_hosts=args.known_hosts, idle_faults=args.idle_faults, active_faults=args.active_faults)
    except (OSError, ValueError, TypeError):
        report = {"suite": args.suite, "passed": False, "failed": 1,
                  "checks": [{"name": "manifest", "status": "failed"}], "skipped_required": 1}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] and not report["skipped_required"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
