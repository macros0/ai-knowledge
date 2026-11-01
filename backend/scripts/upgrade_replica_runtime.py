"""Explicit held same-schema image/config identity update; never start application roles."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env-file", type=Path, required=True)
    parser.add_argument("--expected-identity", type=Path, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        from app.deployment.bootstrap import OperatorSettings, write_identity
        from app.deployment.runtime_upgrade import rebind_runtime
        from app.db.session import configure_database, get_engine
        from app.services.work.maintenance_control import MaintenanceOperator
        if (not args.runtime_env_file.is_file() or not args.expected_identity.is_file()
                or args.expected_identity.stat().st_size > 8192):
            raise ValueError("Explicit bounded offline inputs required")
        settings = OperatorSettings(_env_file=str(args.runtime_env_file))
        if settings.runtime_identity_file is None:
            raise ValueError("Explicit identity output required")
        expected = json.loads(args.expected_identity.read_bytes())
        if not args.apply:
            print(json.dumps({"status": "planned", "mutations": 0}))
            return 0
        configure_database(settings.db_url)
        with MaintenanceOperator(get_engine(), operation_id=args.operation_id, owner_id=args.owner_id, settings=settings) as op:
            with op.transaction() as session:
                document = rebind_runtime(session, settings, expected, operation_id=args.operation_id)
            write_identity(settings.runtime_identity_file, document)
        print("REPLICA_UPGRADE_IDENTITY:" + json.dumps({"identity": document, "operation_id": args.operation_id,
            "jobs_held": True}, sort_keys=True), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 -- raw offline Settings/provider exceptions may contain secrets.
        print(json.dumps({"status": "upgrade-refused", "type": type(exc).__name__, "jobs_held": True}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
