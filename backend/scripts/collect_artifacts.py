"""Explicit GC setup/one-shot command; no mutation without --apply."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv=None, *, settings=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", action="store_true", help="Bind the configured store to this database")
    parser.add_argument("--adopt-existing", action="store_true", help="Explicitly adopt existing unmarked objects; verify the database/backup first")
    parser.add_argument("--apply", action="store_true", help="Apply initialization or one bounded collection cycle")
    args = parser.parse_args(argv)
    if args.adopt_existing and not args.initialize:
        parser.error("--adopt-existing requires --initialize")
    if not args.apply:
        print(json.dumps({"apply": False, "operation": "initialize" if args.initialize else "collect",
                          "adopt_existing": args.adopt_existing}))
        return 0
    from app.config import get_settings
    from app.services.artifacts.gc import initialize_gc, run_gc_cycle
    settings = settings or get_settings()
    try:
        if args.initialize:
            initialize_gc(settings, adopt_existing=args.adopt_existing, explicit=True)
            print('{"initialized": true}')
        else:
            if not settings.artifact_gc_enabled:
                raise ValueError("Artifact GC is disabled")
            print(json.dumps(asdict(run_gc_cycle(settings))))
        return 0
    except Exception as exc:
        # Never print endpoint/DB credentials, provider messages or private paths.
        print(f"Artifact GC refused or deferred ({type(exc).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
