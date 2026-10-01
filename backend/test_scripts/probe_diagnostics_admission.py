"""Bounded CT admission observation; results are diagnostic, not SLA evidence."""
import argparse
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

if __package__:
    from .probe_diagnostics_http_matrix import measure, _containers, _image_ids
else:
    from probe_diagnostics_http_matrix import measure, _containers, _image_ids


CONTROL_FIELDS = {"active", "revision", "lease_until", "deadline", "session_id"}
STATUS_FIELDS = {"running", "storage_degraded", "written", "dropped", "invalid",
                 "repeats", "expired_queue", "intentional_aggregated", "intentional_sampled",
                 "sampled_out_slow", "aggregate_overflow", "queued"}


def snapshot(root):
    result = {}
    for name, path, allowed in (
        ("control", root / "backend/control/capture.json", CONTROL_FIELDS),
        ("frontend", root / "frontend/status.json", STATUS_FIELDS),
    ):
        try:
            if path.stat().st_size > 4096:
                raise ValueError("Oversized safe metadata")
            value = json.loads(path.read_text())
            result[name] = {key: value[key] for key in allowed if key in value}
        except (OSError, ValueError, TypeError) as exc:
            result[name] = {"read_error_type": type(exc).__name__}
    return result


def observe_measure(output, *, measure, read, metadata=None):
    """Keep observations even when the measurement raises; never serialize its text."""
    if output.exists():
        raise FileExistsError(output.name)
    observations = deque(maxlen=6000)
    done = threading.Event()
    count = [0]
    data = {"schema_version": 1, "scope": "diagnostic observation only; not SLA acceptance",
            **(metadata or {})}

    def sample():
        observations.append({"at_utc": datetime.now(timezone.utc).isoformat(),
                             "at_monotonic": time.monotonic(), **read()})
        count[0] += 1

    def observe():
        while not done.wait(.05):
            sample()

    sample()
    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    try:
        result = measure()
        data["result"] = result
        return result
    except Exception as exc:
        status = getattr(exc, "code", None)
        if type(status) is not int:
            status = getattr(exc.__cause__, "code", None)
        data["failure"] = {"error_type": type(exc).__name__,
                           "http_status": status if type(status) is int and 100 <= status <= 599 else None}
        raise
    finally:
        done.set()
        observer.join(timeout=2)
        sample()
        data["observations"] = list(observations)
        data["evicted_observations"] = max(0, count[0] - len(observations))
        temporary = output.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        temporary.replace(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path("/opt/okf-diag-levels-20260929"))
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--measurement-seconds", type=int, default=60)
    parser.add_argument("--actor-username", default="diag.bench04")
    args = parser.parse_args()
    if not 0 <= args.warmup_seconds <= 30 or not 1 <= args.measurement_seconds <= 60:
        parser.error("Observation is bounded to warmup <=30 and measurement <=60 seconds")
    containers = _containers("okf-diag-levels-20260929")
    images = _image_ids(containers)
    spool = args.root / "diagnostics-1doc"
    def collect():
        if _image_ids(containers) != images:
            raise RuntimeError("Images changed during observation")
        return measure("http://127.0.0.1:18084", concurrency=8,
                       warmup_seconds=args.warmup_seconds,
                       measurement_seconds=args.measurement_seconds, min_requests=1000,
                       mode="standard", repeat=1, corpus_size=1, spool_root=spool,
                       build_bundle=True, actor_username=args.actor_username, containers=containers)
    result = observe_measure(args.output, measure=collect, read=lambda: snapshot(spool),
                             metadata={"image_ids": images})
    print(json.dumps({"counts": result["capture_search_counts"],
                      "expected": result["expected_capture_search_calls"]}), flush=True)


if __name__ == "__main__":
    main()
