"""Validate or apply the versioned glossary seed."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.glossary.seed import DEFAULT_SEED_PATH, load_seed, seed_glossary
from app.services.glossary.registry import GlossaryRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write missing terms")
    parser.add_argument("--path", default=str(DEFAULT_SEED_PATH), help="seed JSON path")
    args = parser.parse_args()
    report = seed_glossary(GlossaryRegistry(), load_seed(args.path), apply=args.apply)
    mode = "apply" if args.apply else "dry-run"
    print(f"glossary seed {mode}: {report}")
    return 1 if report["conflict"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
