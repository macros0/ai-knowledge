"""Explicit stopped-installation audit operation; payload is never printed."""
import argparse
from pathlib import Path
import sys
from audit_operator_common import configure


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env-file",type=Path)
    parser.add_argument("--confirm-hold-restored-events",action="store_true",required=True)
    args=parser.parse_args()
    owner=None
    try:
        settings=configure(args.runtime_env_file)
        from app.services.audit_outbox import AuditDispatcher
        from app.db.session import get_engine
        from app.services.audit_hold import hold_pending
        owner=AuditDispatcher(settings)
        owner._lock()
        count=hold_pending(get_engine())
        print(f"Audit hold verified: {count} rows")
    except Exception:
        print("Audit operation unavailable; keep application stopped",file=sys.stderr)
        return 1
    finally:
        if owner is not None and owner._handle is not None:owner._handle.close()
    return 0

if __name__=="__main__":raise SystemExit(main())
