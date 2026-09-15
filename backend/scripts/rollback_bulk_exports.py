"""Guarded operator rollback for the isolated raw-export queue."""
from __future__ import annotations

import argparse
import json

from app.services.audit import SystemUser
from app.services.export_queue import get_export_queue

_CONFIRMATION = "DELETE_EXPORT_ARTIFACTS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-ready", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)
    if (args.apply and args.confirm != _CONFIRMATION) or (not args.apply and args.confirm):
        print(json.dumps({"error": "invalid_confirmation"}))
        return 2
    plan = get_export_queue().plan_rollback(include_ready=args.include_ready)
    payload = {
        "dry_run": not args.apply,
        "include_ready": plan.include_ready,
        "job_ids": list(plan.job_ids),
        "artifact_names": list(plan.artifact_names),
        "artifact_bytes": plan.artifact_bytes,
        "active_lease_job_ids": list(plan.active_lease_job_ids),
    }
    if not args.apply:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    result = get_export_queue().apply_rollback(plan, actor=SystemUser())
    payload.update({"dry_run": False, **result.__dict__})
    print(json.dumps(payload, ensure_ascii=False))
    if result.skipped_leases:
        return 3
    return 4 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
