"""Validate or apply the versioned glossary seed."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.glossary.seed import (
    DEFAULT_SEED_PATH, DEFAULT_RULES_SEED_PATH, load_seed, load_seed_rules, seed_glossary,
)
from app.services.glossary.registry import GlossaryRegistry


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write missing terms and rules")
    parser.add_argument("--path", default=str(DEFAULT_SEED_PATH), help="seed JSON path")
    parser.add_argument("--rules-file", type=Path, help="rule seed JSON path")
    parser.add_argument("--rules-only", action="store_true", help="seed rules without adding terms")
    args = parser.parse_args()
    entries = [] if args.rules_only else load_seed(args.path)
    rules_path = args.rules_file
    if rules_path is None and (args.rules_only or Path(args.path).resolve() == DEFAULT_SEED_PATH):
        rules_path = DEFAULT_RULES_SEED_PATH
    rules = load_seed_rules(rules_path) if rules_path is not None else ()
    report = seed_glossary(GlossaryRegistry(), entries, apply=args.apply, rules=rules)
    mode = "apply" if args.apply else "dry-run"
    print(f"glossary seed {mode}: {report}")
    return 1 if report["conflict"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
