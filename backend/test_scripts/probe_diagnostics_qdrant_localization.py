"""Six bounded c8 diagnostic series; this is not an acceptance retry."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import urllib.error

import probe_diagnostics_http_matrix as probe
from probe_diagnostics_http import Client


def main():
    config = json.loads(sys.argv[1])
    target = Path(config["output"])
    probe._container_pid = lambda name: config["pids"][name.rsplit("-", 2)[-2]]
    probe._image_ids = lambda containers: config["images"]
    failures = []
    bundle_windows = {}

    class ObservedClient(Client):
        def call(self, method, path, payload=None):
            started = time.perf_counter()
            try:
                response = super().call(method, path, payload)
                if method == "POST" and path == "/api/admin/diagnostics/bundles":
                    bundle_windows[response["id"]] = {"submitted_monotonic": started}
                if method == "GET" and path == "/api/admin/diagnostics/bundles":
                    for bundle in response.get("items", []):
                        window = bundle_windows.get(bundle["id"])
                        if window is not None and bundle["status"] in {"ready", "failed"}:
                            window.setdefault("terminal_observed_monotonic", time.perf_counter())
                            window["terminal_status"] = bundle["status"]
                return response
            except urllib.error.HTTPError as exc:
                row = {"at_utc": datetime.now(timezone.utc).isoformat(),
                       "started_monotonic": started, "finished_monotonic": time.perf_counter(),
                       "http_status": exc.code, "search_request": path == "/api/search"}
                # Read only stable allowlisted codes; exclude detail/provider text.
                try:
                    body = json.loads(exc.read(4096))
                    if body.get("code") in {"dependency_unavailable", "timeout", "internal_error", "rate_limited"}:
                        row["error_code"] = body["code"]
                    if body.get("service") in {"qdrant", "ollama", "llm"}:
                        row["service"] = body["service"]
                except (ValueError, AttributeError):
                    pass
                failures.append(row)
                raise

    probe.Client = ObservedClient
    result = {"purpose": "bounded Qdrant 503 localization", "performance_acceptance": False,
              "images": config["images"], "started_utc": datetime.now(timezone.utc).isoformat(),
              "rows": [], "failures": failures, "bundle_windows": bundle_windows, "completed": False}
    try:
        for repeat in range(1, 4):
            for mode, zipped in (("baseline", False), ("standard", True)):
                row = probe.measure("http://frontend:3000", concurrency=8, warmup_seconds=30,
                    measurement_seconds=60, min_requests=1000, mode=mode, repeat=repeat,
                    corpus_size=1, spool_root=Path("/diag"), build_bundle=zipped,
                    actor_username=f"diag.stop{repeat+9:02d}", stop_before_bundle=True,
                    containers=probe._containers(config["project"]), retain_timelines=True)
                result["rows"].append(row)
                target.write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps({"repeat": repeat, "zip": zipped, "requests": row["requests"],
                                  "p95_ms": row["p95_ms"]}), flush=True)
        result["completed"] = True
    except Exception as exc:
        result["failure_type"] = type(exc).__name__
        raise
    finally:
        result["finished_utc"] = datetime.now(timezone.utc).isoformat()
        target.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
