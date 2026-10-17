"""Run infrastructure readiness; synthetic corpus checks are explicitly opt-in."""
import argparse
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--synthetic-fixture", action="store_true")
    args = parser.parse_args()
    base = ["kubectl", "--context", args.context, "--namespace", args.namespace]
    subprocess.run(base + ["rollout", "status", "deployment/application", "--timeout=300s"], check=True)
    command = ["python", "scripts/kubernetes_smoke.py"] if args.synthetic_fixture else [
        "python", "-c", "import urllib.request; [urllib.request.urlopen('http://127.0.0.1:8000'+p,timeout=15).read() for p in ('/health/live','/health/ready')]; print('PASS infrastructure readiness; functional/auth/live-model gates were not run')"]
    subprocess.run(base + ["exec", "deployment/application", "-c", "backend", "--"] + command, check=True)


if __name__ == "__main__":
    main()
