"""Fixed-cadence polling without adding read duration to every period."""
import math
import time


def poll_loop(observe, stop, *, interval_seconds=.5, monotonic=time.monotonic):
    if (type(interval_seconds) not in (int, float) or not math.isfinite(interval_seconds)
            or not 0 < interval_seconds <= 60):
        raise ValueError('Invalid observation period')
    next_at = monotonic() + interval_seconds
    while not stop.wait(max(0, next_at - monotonic())):
        observe()
        next_at += interval_seconds
