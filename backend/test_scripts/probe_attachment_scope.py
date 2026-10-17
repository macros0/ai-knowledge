"""Compare bounded preflight against the unchanged parse baseline, without LLM."""

import argparse
import json
from pathlib import Path
import statistics
import sys
from tempfile import TemporaryDirectory
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def measure(path, repeats=10):
    from inspect_attachment_scope import inspect_file
    from app.services.parser_supervisor import parse_document_supervised

    baseline = []
    after = []
    inspection = None
    # One warmup for both paths, then ten paired passes in the same environment.
    for run in range(repeats + 1):
        with TemporaryDirectory(prefix="scope-baseline-") as folder:
            started = time.monotonic()
            parse_document_supervised(
                path, path.name, attachments_dir=folder, timeout_seconds=60, max_memory_mb=1024
            )
            raw = time.monotonic() - started
        started = time.monotonic()
        inspection = inspect_file(path)
        added = time.monotonic() - started
        if run:
            baseline.append(raw)
            after.append(raw + added)
    deltas = [a - b for a, b in zip(after, baseline)]
    p95 = statistics.quantiles(deltas, n=20, method="inclusive")[18]
    return {
        "baseline_seconds": baseline,
        "after_seconds": after,
        "added_p95_seconds": p95,
        "light_time_gate_passed": p95 <= 1.0,
        "inventory": inspection["inventory"],
        "recommendation": inspection["recommendation"],
        "memory_gate": "unmeasured",
        "llm_calls": 0,
        "database_writes": 0,
        "vector_writes": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = {p.name: measure(p) for p in sorted(args.fixtures.iterdir()) if p.is_file()}
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
