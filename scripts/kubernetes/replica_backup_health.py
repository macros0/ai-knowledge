"""Read-only format3 backup health: exit0 healthy copy,1 pending attempt,2 alert.

    No scheduler, source connections, maintenance transitions or notification
    delivery. Key material is read from a separate file and never emitted.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "key-file"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("installation-id", "destination-store-id"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--attempt-journal", type=Path)
    parser.add_argument("--now", help="Explicit aware observation time for offline drills")
    parser.add_argument("--attempt-timeout-seconds", type=int, default=3600)
    args = parser.parse_args(argv)
    from app.deployment.replica_backup_health import assess_backup
    try:
        with args.key_file.open("rb") as stream:
            key = stream.read(33)
        if len(key) != 32 or args.key_file.resolve().is_relative_to(args.archive.resolve()):
            raise ValueError("Separate 32-byte key required")
        now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
        result = assess_backup(args.archive, key, installation_id=args.installation_id,
            destination_store_id=args.destination_store_id, attempt_path=args.attempt_journal,
            attempt_timeout_seconds=args.attempt_timeout_seconds, now=now)
    except Exception:  # noqa: BLE001 -- public health must never expose key/path/decoding errors.
        result = {"schema_version": 1, "state": "alert", "alerts": ["backup_health_unavailable"],
            "archive_verified": False, "restore_verified": False, "rpo_assured": False}
    print(json.dumps(result))
    return {"healthy": 0, "pending": 1, "alert": 2}[result["state"]]


if __name__ == "__main__":
    raise SystemExit(main())
