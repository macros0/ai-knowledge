"""Offline plan/copy/verify/cutover. Input manifests contain no credentials."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None, *, settings=None, stores=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path)
    parser.add_argument("--target-manifest", type=Path)
    parser.add_argument("--plan", type=Path, help="Private persistent transfer inventory/payload directory")
    parser.add_argument("--phase", choices=("plan", "copy", "verify", "cutover"), default="plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true", help="Operator confirmation; stop all application/GC writers first")
    parser.add_argument("--backup-manifest", type=Path)
    parser.add_argument("--private-config", type=Path, help="Private JSON credentials keyed by store ID; never copied into inventory")
    parser.add_argument("--runtime-env-file", type=Path, help="Explicit source runtime env, required for a production plan")
    parser.add_argument("--show-installation", action="store_true")
    args = parser.parse_args(argv)
    from app.services.work.cli import require_single_process_writer
    if args.phase != "plan" and not args.apply:
        print("Mutation requires --apply", file=sys.stderr)
        return 2
    if not args.show_installation and not (args.source_manifest and args.target_manifest and args.plan):
        parser.error("--source-manifest, --target-manifest and --plan are required")
    from app.config import Settings, get_settings
    from app.db.session import configure_database
    from app.services.artifacts.migration import StorageMigration, installation_id, _read
    try:
        configure_target = settings is None
        if configure_target:
            settings = Settings(_env_file=str(args.runtime_env_file)) if args.runtime_env_file else get_settings()
        require_single_process_writer("migrate_artifact_storage", settings=settings)
        if configure_target:
            configure_database(settings.db_url)
        if settings.environment == "production" and not args.runtime_env_file:
            raise ValueError("Explicit production runtime env required")
        if args.show_installation:
            print(json.dumps({"installation_id": installation_id()}))
            return 0
        task = StorageMigration(settings, _read(args.source_manifest), _read(args.target_manifest), args.plan,
                                stores=stores, credentials=_read(args.private_config) if args.private_config else None)
        if args.phase == "plan":
            plan = task.plan()
            result = {"phase": "plan", "migration_id": plan["id"], "source_revision": plan["source_revision"],
                      "plan_hash": plan["plan_hash"], "counts": plan["counts"]}
        else:
            result = getattr(task, args.phase)(apply=args.apply, writers_stopped=args.writers_stopped, backup=args.backup_manifest)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Provider/SQL/OS exceptions may contain credentials or document paths.
        print(f"Storage migration refused or interrupted ({type(exc).__name__}); inspect private receipts/configuration", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
