"""Profile synchronous producer cost with the synthetic TestClient search stub."""
import argparse
from collections import defaultdict
from pathlib import Path
import statistics
import sys
import time

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.diagnostics.recorder import DiagnosticRecorder
from test_scripts import probe_diagnostics


def percentile(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int(fraction * (len(values) - 1)))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--level", choices=("standard", "detailed"), required=True)
    args = parser.parse_args()
    calls = defaultdict(list)
    phase = [0]
    original_emit = DiagnosticRecorder.emit
    original_series = probe_diagnostics._series

    def timed_emit(self, event_code, **kwargs):
        started = time.perf_counter_ns()
        try:
            return original_emit(self, event_code, **kwargs)
        finally:
            calls[(phase[0], event_code)].append((time.perf_counter_ns() - started) / 1_000_000)

    def timed_series(*items, **options):
        phase[0] += 1
        return original_series(*items, **options)

    DiagnosticRecorder.emit = timed_emit
    probe_diagnostics._series = timed_series
    try:
        result = probe_diagnostics.run(args.root, warmup_seconds=2, requests=200,
                                       measurement_seconds=10, concurrency=1,
                                       scenario="search_stub", capture_level=args.level)
    finally:
        DiagnosticRecorder.emit = original_emit
        probe_diagnostics._series = original_series
    for name, index in (("baseline", 1), ("capture", 2), ("capture_build", 3)):
        rows = sorted(((code, values) for (phase_index, code), values in calls.items()
                       if phase_index == index), key=lambda item: item[0])
        print(f"{name} p95_ms={result[name]['p95_ms']:.3f} requests={result[name]['requests']}")
        for code, values in rows:
            print(f"  {code}: calls={len(values)} total_ms={sum(values):.3f} "
                  f"p50_ms={statistics.median(values):.4f} p95_ms={percentile(values, .95):.4f}")


if __name__ == "__main__":
    main()
