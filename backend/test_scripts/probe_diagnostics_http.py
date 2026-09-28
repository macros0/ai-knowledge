"""Measure isolated Compose search latency with a synthetic indexed document.

Run only against a disposable simulation-auth stack. No response text is saved.
"""

import argparse
from datetime import datetime
from http.cookiejar import CookieJar
import json
import math
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.request


class Client:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, payload=None):
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method != "GET":
            token = next((cookie.value for cookie in self.jar if cookie.name == "csrf_token"), None)
            if token:
                headers["X-CSRF-Token"] = token
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with self.opener.open(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if response.headers.get_content_type() == "application/json" else raw

    def login(self):
        self.call("GET", "/api/auth/me")
        self.call("POST", "/api/auth/simulate", {"username": "demo.admin"})


def series(client, warmup_seconds, requests, *, on_samples_start=None):
    query = {"query": "Synthetic diagnostic restore drill", "locale": "en",
             "use_glossary": False, "top_k": 5}

    def once():
        start = time.perf_counter()
        response = client.call("POST", "/api/search", query)
        elapsed = (time.perf_counter() - start) * 1000
        if not response.get("hits"):
            raise RuntimeError("Synthetic search returned no indexed hit")
        return elapsed

    deadline = time.monotonic() + warmup_seconds
    while time.monotonic() < deadline:
        once()
    started = on_samples_start() if on_samples_start else None
    starts = []
    samples = []
    for _ in range(requests):
        starts.append(time.time())
        samples.append(once())
    ordered = sorted(samples)
    return ({"requests": requests, "p50_ms": statistics.median(samples),
             "p95_ms": ordered[math.ceil(0.95 * requests) - 1],
             "max_ms": ordered[-1]}, starts, started)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.warmup_seconds < 0:
        parser.error("requests must be positive and warmup nonnegative")
    client = Client(args.base_url)
    client.login()
    result = {"scenario": "isolated Linux Compose, synthetic indexed document, HTTP /api/search",
              "warmup_seconds": args.warmup_seconds, "requests_per_mode": args.requests}
    result["baseline"] = series(client, args.warmup_seconds, args.requests)[0]
    session = client.call("POST", "/api/admin/diagnostics/sessions",
                          {"scope": "system", "minutes": 15})
    try:
        result["capture"] = series(client, args.warmup_seconds, args.requests)[0]
        result["capture_build"], starts, bundle = series(
            client, args.warmup_seconds, args.requests,
            on_samples_start=lambda: client.call("POST", "/api/admin/diagnostics/bundles", {}),
        )
        result["bundle_id"] = bundle["id"]
        for _ in range(60):
            try:
                preview = client.call("POST", f"/api/admin/diagnostics/bundles/{bundle['id']}/preview", {})
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                time.sleep(1)
        else:
            raise RuntimeError("Diagnostic bundle did not become ready")
        result["bundle_partial"] = preview["manifest"]["partial"]
        result["bundle_gaps"] = preview["manifest"]["gaps"]
        bundle_row = next(item for item in client.call("GET", "/api/admin/diagnostics/bundles")["items"]
                          if item["id"] == bundle["id"])
        finished = datetime.fromisoformat(bundle_row["finished_at"]).timestamp()
        created = datetime.fromisoformat(bundle_row["created_at"]).timestamp()
        result["bundle_build_seconds"] = finished - created
        result["search_requests_before_bundle_ready"] = sum(start <= finished for start in starts)
        result["recorder_sampled_success"] = preview["manifest"]["counts"].get("recorder_sampled_success")
        result["status_sampled_success"] = client.call("GET", "/api/admin/diagnostics/status")["recorder"].get("sampled_success")
    finally:
        client.call("POST", f"/api/admin/diagnostics/sessions/{session['id']}/stop", {})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
