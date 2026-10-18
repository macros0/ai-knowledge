"""Preview by default. Does not change configuration or migrate schema."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true",
                        help="Operator confirms all HTTP/background/offline writers are stopped")
    args = parser.parse_args()
    url = os.environ.get("REFERENCE_MAINTENANCE_DATABASE_URL")
    if not url:
        parser.error("Set REFERENCE_MAINTENANCE_DATABASE_URL explicitly; no default database is used")
    if args.apply and not args.writers_stopped:
        parser.error("Apply requires --writers-stopped after closing all writers")
    # Prevent configuration fallback to the working database, including on import.
    os.environ["DATABASE_URL"] = url
    from app.db.session import configure_for_tests, session_scope
    from app.services.reference_operations import invalidate_all_editor_generations
    configure_for_tests(url)
    with session_scope() as session:
        result = invalidate_all_editor_generations(session, apply=args.apply)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
