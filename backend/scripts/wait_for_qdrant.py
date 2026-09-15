"""Bounded, read-only readiness wait for a Qdrant HTTP endpoint."""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx


class QdrantReadinessError(RuntimeError):
    """Qdrant did not answer its read-only collections endpoint in time."""


def wait_for_qdrant(
    url: str,
    *,
    timeout_seconds: float = 60,
    interval_seconds: float = 1,
    api_key: str | None = None,
    ca_bundle: str | None = None,
    request: Callable = httpx.get,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Wait for ``GET /collections`` without creating or changing anything."""
    endpoint = f"{url.rstrip('/')}/collections"
    safe_parts = urlsplit(endpoint)
    safe_endpoint = urlunsplit((safe_parts.scheme, safe_parts.hostname or "", safe_parts.path, "", ""))
    headers = {"api-key": api_key} if api_key else {}
    verify: bool | str = ca_bundle or True
    deadline = monotonic() + timeout_seconds
    last_error = "not ready"

    while True:
        try:
            response = request(endpoint, headers=headers, timeout=2, verify=verify)
            if 200 <= response.status_code < 300:
                return
            last_error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last_error = str(exc) or exc.__class__.__name__

        if monotonic() >= deadline:
            raise QdrantReadinessError(
                f"Qdrant at {safe_endpoint} was not ready within {timeout_seconds:g}s: {last_error}"
            )
        sleep(interval_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Qdrant base HTTP URL")
    parser.add_argument("--timeout-seconds", type=float, default=60)
    parser.add_argument("--interval-seconds", type=float, default=1)
    args = parser.parse_args()
    if args.timeout_seconds < 0 or args.interval_seconds < 0:
        parser.error("--timeout-seconds and --interval-seconds must be non-negative")

    try:
        wait_for_qdrant(
            args.url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
            api_key=os.environ.get("QDRANT_API_KEY"),
            ca_bundle=os.environ.get("QDRANT_CA_BUNDLE"),
        )
    except QdrantReadinessError as exc:
        print(str(exc))
        return 1
    print(f"Qdrant ready: {args.url.rstrip('/')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
