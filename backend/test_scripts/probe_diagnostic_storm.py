"""Attempt 10,000 synthetic error events per second for one minute."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.diagnostics.recorder import DiagnosticRecorder
from app.services.diagnostics.schema import DiagnosticContext, DiagnosticLimits
from app.services.diagnostics.store import DiagnosticStore


def run(root: Path, *, seconds=60, rate=10000):
    root = root.resolve()
    limits = DiagnosticLimits(total_bytes=8 * 1048576, backend_bytes=7 * 1048576,
                              frontend_bytes=1048576, baseline_bytes=1048576,
                              session_bytes=1048576, segment_bytes=65536,
                              bundle_bytes=2 * 1048576, min_free_bytes=0, queue_size=4096)
    session_id = str(uuid4())
    with DiagnosticStore(root, limits) as store:
        recorder = DiagnosticRecorder(store, capture_selector=lambda *_: session_id)
        recorder.start()
        attempted = 0
        status_ms = []
        start = time.perf_counter()
        try:
            for second in range(seconds):
                for _ in range(rate):
                    recorder.emit("operation_failed", context=DiagnosticContext(operation_kind="system"),
                                  fields={"stage": "search"})
                    attempted += 1
                status_start = time.perf_counter()
                store.status()
                status_ms.append((time.perf_counter() - status_start) * 1000)
                remaining = start + second + 1 - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            recorder.stop(timeout_seconds=15)
        result = {"at_utc": datetime.now(timezone.utc).isoformat(),
                  "attempted": attempted, "elapsed_seconds": time.perf_counter() - start,
                  "requested_rate_per_second": rate, "status_p95_ms": sorted(status_ms)[int(.95 * (len(status_ms) - 1))],
                  "used_bytes": store.used_bytes, "quota_bytes": limits.backend_bytes,
                  "recorder": recorder.status()}
        if result["used_bytes"] > limits.backend_bytes:
            raise RuntimeError("Diagnostic quota exceeded")
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--rate", type=int, default=10000)
    args = parser.parse_args()
    if args.seconds < 1 or args.rate < 1:
        parser.error("Seconds and rate must be positive")
    result = run(args.root, seconds=args.seconds, rate=args.rate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("attempted", "elapsed_seconds", "used_bytes", "status_p95_ms")},
                     sort_keys=True))


if __name__ == "__main__":
    main()
