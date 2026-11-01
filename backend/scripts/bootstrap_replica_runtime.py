"""Explicit fresh-install migration/bootstrap; never adopt existing application data."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env-file", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--phase", choices=("migrate", "bootstrap"), default="bootstrap")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        from hashlib import sha256
        from app.deployment.bootstrap import (OperatorSettings, canonical, probe_artifacts, install_identity,
            write_identity, assert_fresh_installation, initialize_empty_vector_store, _receipt)
        from app.deployment.storage_selection import StorageSelection
        from app.db.session import configure_database, get_engine, session_scope
        from app.services.artifacts.control import migration_access
        if args.selection.stat().st_size > 65536 or not args.runtime_env_file.is_file():
            raise ValueError("Explicit bounded installation inputs required")
        selection = StorageSelection.model_validate_json(args.selection.read_bytes())
        settings = OperatorSettings(_env_file=str(args.runtime_env_file))
        selection.validate_runtime(settings)
        if settings.runtime_identity_file is None:
            raise ValueError("Explicit identity output reference required")
        if not args.apply:
            print(json.dumps({"status": "planned", "phase": args.phase, "mutations": 0}))
            return 0
        configure_database(settings.db_url)
        if get_engine().dialect.name != "postgresql":
            raise ValueError("Replica bootstrap requires PostgreSQL")
        with migration_access():
            if args.phase == "migrate":
                from alembic import command
                from alembic.config import Config
                root = Path(__file__).resolve().parents[1]
                config = Config(str(root / "alembic.ini"))
                config.set_main_option("script_location", str(root / "alembic"))
                with get_engine().begin() as connection:
                    assert_fresh_installation(connection)
                    config.attributes["connection"] = connection
                    command.upgrade(config, "head")
                print(json.dumps({"status": "migrated"}))
                return 0
            digest = "sha256:" + sha256(canonical(selection.model_dump())).hexdigest()
            from app.db.models import ReplicaRuntimeIdentity
            # Reject nonempty/mismatched SQL before touching external storage.
            # A committed installation can resume file publication directly.
            with session_scope() as session:
                installed = session.get(ReplicaRuntimeIdentity, 1) is not None
                if installed:
                    document = install_identity(session, settings, _receipt(settings, selection.installation_id, digest))
                else:
                    assert_fresh_installation(session.connection())
            if installed:
                write_identity(settings.runtime_identity_file, document)
                print(json.dumps({"status": "installed", "installation_id": selection.installation_id,
                    "runtime_fingerprint": document["runtime_fingerprint"], "qualification": "basic-bootstrap-only"}))
                return 0
            receipt = probe_artifacts(settings, installation_id=selection.installation_id, selection_digest=digest)
            initialize_empty_vector_store(settings)
            with session_scope() as session:
                document = install_identity(session, settings, receipt)
            write_identity(settings.runtime_identity_file, document)
        print(json.dumps({"status": "installed", "installation_id": selection.installation_id,
            "runtime_fingerprint": document["runtime_fingerprint"], "qualification": "basic-bootstrap-only"}))
        return 0
    except Exception as exc:
        # Pydantic/provider exceptions may contain raw input DSNs/credentials.
        print(f"Replica bootstrap refused ({type(exc).__name__}); inspect private inputs", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
