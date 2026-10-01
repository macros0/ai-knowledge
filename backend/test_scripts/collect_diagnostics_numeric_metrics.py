"""Numeric Linux VM/cgroup sampler; no application data or process arguments."""
import argparse
import json
from pathlib import Path
import time

from run_diagnostics_aba_pve import system_sample


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-pid", type=int, required=True)
    parser.add_argument("--frontend-pid", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    groups = {role: Path("/sys/fs/cgroup") / next(line.split("::", 1)[1].lstrip("/")
              for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines() if line.startswith("0::"))
              for role, pid in (("backend", args.backend_pid), ("frontend", args.frontend_pid))}
    with (args.root / "linux-vm-metrics.jsonl").open("x") as handle:
        while not (args.root / "metrics.stop").exists():
            value = system_sample()
            value["containers"] = {}
            for role, group in groups.items():
                metrics = {key: int(count) for key, count in
                           (line.split() for line in (group / "cpu.stat").read_text().splitlines())}
                metrics["memory_current_bytes"] = int((group / "memory.current").read_text())
                metrics["io_counters"] = {}
                for line in (group / "io.stat").read_text().splitlines():
                    device, *fields = line.split()
                    metrics["io_counters"][device] = {key: int(count) for key, count in
                                                    (field.split("=") for field in fields)}
                value["containers"][role] = metrics
            handle.write(json.dumps(value, separators=(",", ":")) + "\n")
            handle.flush()
            time.sleep(.25)


if __name__ == "__main__":
    main()
