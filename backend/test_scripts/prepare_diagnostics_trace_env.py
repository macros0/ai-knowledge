"""Derive a private synthetic env with one changed diagnostic trace budget."""

import argparse
import os
from pathlib import Path


def prepare(source: Path, output: Path, limit: int):
    if not 1 <= limit <= 100 or output.exists():
        raise ValueError("Invalid isolated trace tuning request")
    key = "DIAGNOSTICS_TRACE_LIMIT_PER_SECOND"
    lines = [line for line in source.read_text(encoding="utf-8").splitlines()
             if not line.startswith((key + "=", "OKF_RUNTIME_ENV_FILE="))]
    lines.extend((f"{key}={limit}", f"OKF_RUNTIME_ENV_FILE={output.resolve()}"))
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    output.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, required=True)
    args = parser.parse_args()
    prepare(args.input, args.output, args.limit)
    print("private trace test env prepared")
