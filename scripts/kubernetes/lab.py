"""Bootstrap isolated, synthetic home/CI prerequisites. Never accepts PROD secrets."""
import argparse
from pathlib import Path
import secrets
import subprocess

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--values-output", type=Path, required=True)
    args = parser.parse_args()
    base = ["kubectl", "--context", args.context, "--namespace", args.namespace]
    if not args.context.startswith("kind-"):
        parser.error("Lab bootstrap only supports explicit kind contexts")
    if args.values_output.exists():
        parser.error("Refusing to overwrite an existing lab profile")
    subprocess.run(base + ["create", "namespace", args.namespace], check=True)
    password = secrets.token_hex(24)
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "runtime-secret"}, "type": "Opaque",
              "stringData": {"POSTGRES_PASSWORD": password,
                             "DATABASE_URL": f"postgresql+psycopg://okf:{password}@postgres:5432/okf_knowledge",
                             "APP_SECRET_KEY": secrets.token_hex(32), "LLM_API_KEY": "synthetic-stub-only"}}
    # Never put secret values in process arguments, generated values or stdout.
    subprocess.run(base + ["create", "-f", "-"], input=yaml.safe_dump(secret).encode(), check=True, stdout=subprocess.DEVNULL)
    for name in ("data", "diagnostics", "postgres", "qdrant"):
        pvc = {"apiVersion": "v1", "kind": "PersistentVolumeClaim", "metadata": {"name": f"okf-{name}"},
               "spec": {"accessModes": ["ReadWriteOnce"], "resources": {"requests": {"storage": "2Gi"}}}}
        subprocess.run(base + ["create", "-f", "-"], input=yaml.safe_dump(pvc).encode(), check=True, stdout=subprocess.DEVNULL)
    values = {"deployment": {"environment": "ci"}, "runtime": {"environment": "development", "config": {"AUTH_PROVIDER": "disabled"}},
              "secrets": {"existingSecret": "runtime-secret"},
              "persistence": {name: {"existingClaim": f"okf-{name}"} for name in ("data", "diagnostics", "postgres", "qdrant")},
              "images": {name: {"repository": repository, "tag": "kube-smoke", "pullPolicy": "IfNotPresent"}
                         for name, repository in (("backend", "okf-backend"), ("frontend", "okf-frontend"), ("stub", "okf-model-stub"))},
              "models": {"mode": "stub"}, "gateway": {"enabled": False}}
    args.values_output.parent.mkdir(parents=True, exist_ok=True)
    with args.values_output.open("x", encoding="utf-8") as target:
        yaml.safe_dump(values, target)
    print("Synthetic lab prerequisites created; authentication and Gateway acceptance remain separate.")


if __name__ == "__main__":
    main()
