"""Private DB aggregates; this CLI cannot observe an in-process dispatcher."""
import argparse
import json
from pathlib import Path
import sys
from audit_operator_common import configure


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-env-file",type=Path)
    args=parser.parse_args()
    try:
        configure(args.runtime_env_file)
        from app.services.audit_runtime import delivery_status
        result=delivery_status()
    except Exception:
        print("Audit status unavailable",file=sys.stderr)
        return 1
    print(json.dumps(result,sort_keys=True))
    return 0

if __name__=="__main__":raise SystemExit(main())
