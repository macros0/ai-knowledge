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
from test_scripts.replica_frontend_acceptance import probe_frontend
from test_scripts.replica_frontend_adapter import execute_frontend
from test_scripts.replica_frontend_native import STAGES as FRONTEND_STAGES
from test_scripts.replica_runtime_plan import REQUIRED_CHECKS, STAGES

RUNNER_SHA256 = sha256(b"".join((Path(__file__).parent / name).read_bytes()
    for name in ("run_replica_acceptance.py", "replica_runtime_acceptance.py", "replica_runtime_faults.py",
                 "replica_frontend_acceptance.py", "replica_frontend_adapter.py", "replica_frontend_native.py",
                 "replica_frontend_process.py", "replica_frontend_http.py", "replica_frontend_run.py"))).hexdigest()

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


def execute_owned_runtime(manifest, journal, stage, *, apply=False, resume=False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/kubernetes"))
    from replica_runtime_run import execute_owned_runtime as execute
    return execute(manifest, journal, stage, apply=apply, resume=resume)


def build_report(manifest: dict, suite: str, *, live=False, ssh_host=None, known_hosts=None, idle_faults=False, active_faults=False,
                 owned_stage=None, owned_journal=None, apply_owned=False, resume_owned=False,
                 frontend_stage=None, frontend_journal=None, apply_frontend=False, observe_frontend=False) -> dict:
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
    frontend = None
    selected_frontend = any((frontend_stage,frontend_journal,apply_frontend,observe_frontend))
    if selected_frontend and (not live or suite!='frontend' or frontend_stage is None or frontend_journal is None
            or any((owned_stage,owned_journal,apply_owned,resume_owned,idle_faults,active_faults))
            or (apply_frontend and observe_frontend)):
        raise ValueError('Frontend stages require exclusive explicit live frontend mode and a journal')
    if valid and selected_frontend:
        checks = [check for check in checks if check['name']!='frontend']
        try:
            frontend = execute_frontend(manifest,frontend_journal,frontend_stage,apply=apply_frontend,
                observe=observe_frontend,ssh_host=ssh_host,known_hosts=known_hosts)
            rows = frontend['checks'];names = [row.get('name') for row in rows]
            if (len(names)!=len(FRONTEND_STAGES) or set(names)!={'frontend.'+s for s in FRONTEND_STAGES}
                    or any(row.get('status') not in {'passed','pending','failed'} for row in rows)
                    or type(frontend.get('selected_case_passed')) is not bool
                    or (frontend['selected_case_passed'] and any(row['status']!='passed' for row in rows))):
                raise ValueError('Full native frontend stage set required')
            checks.extend({k:v for k,v in row.items() if k in {'name','status','evidence_sha256'}} for row in rows)
        except Exception:
            frontend = None
            checks.append({'name':'frontend.owned_stage','status':'failed','reason':'owned_frontend_refused_or_interrupted'})
            checks.append({'name':'frontend.current_run','status':'pending','reason':'observe_same_native_intent'})
        checks.extend({'name':'frontend.'+name,'status':'pending','reason':'real_provider_not_observed'}
                      for name in ('production_csrf','gateway_sso'))
    elif (valid and live and "frontend" in requested
            and (suite == "frontend" or manifest.get('deployment_mode') == 'frontend-split')):
        checks = [check for check in checks if check["name"] != "frontend"]
        checks.extend(probe_frontend(manifest, ssh_host=ssh_host, known_hosts=known_hosts))
    owned = None
    if valid and owned_stage is not None:
        if not live or "runtime" not in requested or owned_journal is None:
            raise ValueError("Owned stages require explicit live runtime and a protected journal")
        checks = [check for check in checks if check["name"] != "runtime"]
        try:
            owned = execute_owned_runtime(manifest, owned_journal, owned_stage, apply=apply_owned, resume=resume_owned)
            rows = owned.get("checks", [])
            names = [row.get("name") for row in rows]
            if set(names) != set(REQUIRED_CHECKS) or len(names) != len(REQUIRED_CHECKS):
                raise ValueError("The full required owned runtime check set is missing")
            checks.extend(rows)
        except Exception:  # Provider, path and journal failures must never expose private inputs.
            owned = None
            checks.append({"name": "runtime.owned_stage", "status": "failed", "reason": "owned_stage_refused_or_interrupted"})
            checks.append({"name": "runtime.current_run", "status": "pending", "reason": "same_run_completion_required"})
    elif valid and live and "runtime" in requested:
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
    passed = owned is not None and owned.get("passed") is True and all(check["status"] == "passed" for check in checks)
    return {
        "suite": suite, "run_id": manifest["run_id"] if valid else None,
        "profile": manifest["profile"] if valid else None,
        "revision": revision if re.fullmatch(r"[0-9a-f]{40,64}", revision) else None,
        "runner_sha256": RUNNER_SHA256,
        "checks": checks, "passed": passed, "failed": sum(c["status"] == "failed" for c in checks),
        "skipped_required": sum(c["status"] == "pending" for c in checks), "durations": {},
        "recovery_point_age_seconds": owned.get("recovery_point_age_seconds") if owned else None,
        **({'selected_frontend_case_passed':frontend is not None and frontend['selected_case_passed'],
            **({'owned_frontend_execution_id':frontend['execution_id'],
                'frontend_recipe_sha256':frontend['runner_sha256']} if frontend else {})} if selected_frontend else {}),
        **({"owned_runtime_execution_id": owned["execution_id"], "owned_runtime_recipe_sha256": owned.get("runner_sha256"),
            "durations": owned["durations"]} if owned else {}),
    }


def build_qualification_report(manifest, *, bundle_path, binding_path, trust_path, evidence_root):
    """Offline configured-harness verification; never a probe or deployment action."""
    from test_scripts.replica_acceptance_report import required_checks
    from test_scripts.replica_evidence import read_public_json, verify_evidence

    validate_manifest(manifest)
    if Path(binding_path).absolute().is_relative_to(Path(evidence_root).absolute()):
        raise ValueError('Expected binding must be independently selected')
    binding, _ = read_public_json(binding_path)
    if (manifest['profile'] != binding.get('profile')
            or manifest.get('runtime_fingerprint') != binding.get('runtime', {}).get('identity_fingerprint')):
        raise ValueError('Manifest differs from expected qualification binding')
    scope = {name: manifest[name] for name in ('run_id','namespace_uid','installation_id','acceptance_id')}
    qualification = verify_evidence(binding,scope,bundle_path=bundle_path,trust_path=trust_path,evidence_root=evidence_root)
    missing, failed = set(qualification['missing_required']), set(qualification['failed_required'])
    checks = [{'name':name,'status':'failed' if name in failed else 'pending' if name in missing else 'passed'}
        for name in sorted(set(required_checks(binding)) | failed)]
    return dict(suite='all',mode='offline-trusted-harness-verification',run_id=manifest['run_id'],
        profile=manifest['profile'],runner_sha256=RUNNER_SHA256,checks=checks,
        passed=qualification['qualification_complete'],failed=len(failed),skipped_required=len(missing),
        qualification=qualification,deployment_authorized=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Run scoped synthetic runtime smoke; creates test documents")
    parser.add_argument("--ssh-host", help="Optional pinned user@host for kubectl")
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--idle-faults", action="store_true", help="Also scale API/worker and normally replace idle owned Pods")
    parser.add_argument("--active-faults", action="store_true", help="Also test cancel/delete and model saturation on new synthetic documents")
    parser.add_argument("--owned-runtime-stage", choices=STAGES)
    parser.add_argument("--runtime-journal", type=Path)
    parser.add_argument("--apply-owned-runtime", action="store_true", help="Explicitly authorize this owned mutating stage")
    parser.add_argument("--resume-owned-stage", action="store_true", help="Resume only a recognized existing child phase")
    parser.add_argument('--frontend-stage',choices=FRONTEND_STAGES,help='One owned simulation stage; production qualification stays pending')
    parser.add_argument('--frontend-journal',type=Path)
    parser.add_argument('--apply-frontend',action='store_true',help='Explicitly apply this new native frontend stage')
    parser.add_argument('--observe-frontend',action='store_true',help='Only observe an existing frontend intent; never resubmit it')
    parser.add_argument('--qualification-bundle', type=Path, help='Offline signed harness receipt bundle')
    parser.add_argument('--qualification-binding', type=Path, help='Independently selected expected public binding')
    parser.add_argument('--qualification-trust', type=Path, help='Independently selected trusted public issuers')
    parser.add_argument('--qualification-root', type=Path, help='Read-only root for referenced receipt/artifact files')
    args = parser.parse_args(argv)
    try:
        qualification_inputs = (args.qualification_bundle,args.qualification_binding,
            args.qualification_trust,args.qualification_root)
        offline = any(value is not None for value in qualification_inputs)
        if offline and (not all(value is not None for value in qualification_inputs) or args.suite != 'all'
                or any((args.live,args.ssh_host,args.known_hosts,args.idle_faults,args.active_faults,
                    args.owned_runtime_stage,args.runtime_journal,args.apply_owned_runtime,args.resume_owned_stage,
                    args.frontend_stage,args.frontend_journal,args.apply_frontend,args.observe_frontend))):
            raise ValueError('Complete offline-only qualification inputs required')
        if offline:
            from test_scripts.replica_evidence import read_public_json
            if args.manifest.absolute().is_relative_to(args.qualification_root.absolute()):
                raise ValueError('Manifest must be independently selected')
            value, _ = read_public_json(args.manifest,max_bytes=1024*1024)
        elif args.manifest.stat().st_size > 1024 * 1024:
            raise ValueError("Manifest too large")
        else:
            value = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Object required")
        if (args.idle_faults or args.active_faults) and not args.live:
            raise ValueError("Fault probes require explicit live execution")
        if any((args.frontend_stage,args.frontend_journal,args.apply_frontend,args.observe_frontend)):
            if (not args.live or args.suite!='frontend' or args.frontend_stage is None or args.frontend_journal is None
                    or any((args.owned_runtime_stage,args.runtime_journal,args.apply_owned_runtime,args.resume_owned_stage,
                            args.idle_faults,args.active_faults)) or (args.apply_frontend and args.observe_frontend)
                    or args.manifest.absolute()==args.frontend_journal.absolute()):
                raise ValueError('Exclusive live frontend stage and separate protected journal required')
        if offline:
            report = build_qualification_report(value,bundle_path=args.qualification_bundle,
                binding_path=args.qualification_binding,trust_path=args.qualification_trust,evidence_root=args.qualification_root)
        else:
            report = build_report(value, args.suite, live=args.live, ssh_host=args.ssh_host,
                known_hosts=args.known_hosts, idle_faults=args.idle_faults, active_faults=args.active_faults,
                owned_stage=args.owned_runtime_stage, owned_journal=args.runtime_journal,
                apply_owned=args.apply_owned_runtime, resume_owned=args.resume_owned_stage,
                frontend_stage=args.frontend_stage,frontend_journal=args.frontend_journal,
                apply_frontend=args.apply_frontend,observe_frontend=args.observe_frontend)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        report = {"suite": args.suite, "passed": False, "failed": 1,
                  "checks": [{"name": "manifest", "status": "failed"}], "skipped_required": 1}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] and not report["skipped_required"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
