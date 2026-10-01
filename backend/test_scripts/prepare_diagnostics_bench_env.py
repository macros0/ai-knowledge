"""Add distinct simulation admins to a private disposable runtime env file.

The generated file contains the same private values as the input and must stay
outside the repository. This script never prints its contents.
"""

import argparse
import json
import os
from pathlib import Path


def prepare(source: Path, output: Path):
    if output.exists():
        raise FileExistsError(output)
    lines = [line for line in source.read_text(encoding="utf-8").splitlines()
             if not line.startswith(("AUTH_SIM_USERS=", "OKF_RUNTIME_ENV_FILE="))]
    users = [
        {"user_id": "sim-user", "username": "demo.user", "email": "user@demo.local", "groups": ["KB_Viewer"]},
        {"user_id": "sim-editor", "username": "demo.editor", "email": "editor@demo.local", "groups": ["KB_Editor"]},
        {"user_id": "sim-admin", "username": "demo.admin", "email": "admin@demo.local", "groups": ["KB_Admin"]},
        {"user_id": "sim-security", "username": "demo.security", "email": "security@demo.local", "groups": ["KB_Security"]},
        {"user_id": "sim-guest", "username": "demo.guest", "email": "guest@demo.local", "groups": []},
    ]
    for prefix, count in (("diag.bench", 18), ("diag.stop", 12)):
        for number in range(1, count + 1):
            username = f"{prefix}{number:02d}"
            users.append({"user_id": username, "username": username,
                          "email": f"{username}@demo.local", "groups": ["KB_Admin"]})
    lines.append("AUTH_SIM_USERS=" + json.dumps(users, separators=(",", ":")))
    lines.append(f"OKF_RUNTIME_ENV_FILE={output.resolve()}")
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    output.chmod(0o600)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.input, args.output)
    print("private test env prepared")
