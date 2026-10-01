"""Derive a private external-topology env for a disposable CT test project.

The existing synthetic database password is retained and never printed.
"""

import argparse
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _db_url(value):
    parsed = urlsplit(value)
    auth = parsed.netloc.rsplit("@", 1)[0]
    return urlunsplit((parsed.scheme, f"{auth}@okf-diag-levels-20260929-postgres-1:5432",
                       "/okf_diag_external", parsed.query, parsed.fragment))


def prepare(source: Path, output: Path, root: Path):
    if output.exists():
        raise FileExistsError(output)
    source_values = dict(line.split("=", 1) for line in source.read_text(encoding="utf-8").splitlines()
                         if line and not line.startswith("#") and "=" in line)
    if "DATABASE_URL" not in source_values or "QDRANT_URL" not in source_values:
        raise ValueError("Missing synthetic storage endpoints")
    updates = {
        "STORAGE_MODE": "external",
        "OKF_RUNTIME_ENV_FILE": str(output.resolve()),
        "DATABASE_URL": _db_url(source_values["DATABASE_URL"]),
        "QDRANT_URL": "http://okf-diag-levels-20260929-qdrant-1:6333",
        "QDRANT_COLLECTION": "okf_diag_external",
        "OKF_DATA_DIR": str(root / "data-external"),
        "OKF_DIAGNOSTICS_DIR": str(root / "diagnostics-external"),
        "FRONTEND_PORT": "127.0.0.1:18085",
    }
    result = []
    for line in source.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0]
        if key in updates:
            result.append(f"{key}={updates.pop(key)}")
        else:
            result.append(line)
    result.extend(f"{key}={value}" for key, value in updates.items())
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(result) + "\n")
    output.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.input, args.output, args.root)
    print("private external test env prepared")
