"""Explicit held identity/schema adoption; never migrate or start application roles."""
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
    parser.add_argument("--operator-db-file", type=Path,
        help="Separate read-only operator credential for the same verified PostgreSQL target")
    parser.add_argument("--adopt-schema-from",
        help="Explicit predecessor for an already migrated installation (rpl04_runtime_identity only)")
    parser.add_argument("--writer-receipts", type=Path, help="Verified stop/drain receipts for every writer")
    parser.add_argument("--intended-counts", type=Path, help="Explicit frontend/API/worker counts")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        from app.deployment.bootstrap import OperatorSettings, write_identity
        from app.deployment.runtime_upgrade import adopt_schema_upgrade, rebind_runtime
        from app.deployment.operator_database import operator_database_url, read_regular_input
        from app.db.session import configure_database, get_engine
        from app.services.work.maintenance_control import MaintenanceOperator
        if not args.runtime_env_file.is_file():
            raise ValueError("Explicit bounded offline inputs required")
        schema_adoption = args.adopt_schema_from is not None
        if (schema_adoption and args.adopt_schema_from != "rpl04_runtime_identity"
                or schema_adoption != (args.writer_receipts is not None)
                or schema_adoption != (args.intended_counts is not None)):
            raise ValueError("Explicit supported schema transition and complete adoption inputs required")
        def read_object(path):
            value = json.loads(read_regular_input(path, max_bytes=8192))
            if type(value) is not dict:
                raise ValueError("An offline JSON object is required")
            return value
        expected = read_object(args.expected_identity)
        receipts = read_object(args.writer_receipts) if schema_adoption else None
        counts = read_object(args.intended_counts) if schema_adoption else None
        settings = OperatorSettings(_env_file=str(args.runtime_env_file))
        if settings.runtime_identity_file is None:
            raise ValueError("Explicit identity output required")
        database_url = operator_database_url(settings, args.operator_db_file)
        if not args.apply:
            print(json.dumps({"status": "planned", "mutations": 0}))
            return 0
        configure_database(database_url)
        with MaintenanceOperator(get_engine(), operation_id=args.operation_id, owner_id=args.owner_id, settings=settings) as op:
            with op.transaction() as session:
                if schema_adoption:
                    document = adopt_schema_upgrade(session, settings, expected, operation_id=args.operation_id,
                        previous_revision=args.adopt_schema_from, writer_receipts=receipts, intended_counts=counts)
                else:
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
