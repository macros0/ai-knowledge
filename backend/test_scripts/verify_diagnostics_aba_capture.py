"""Verify issued synthetic requests against capture totals from Linux volumes."""
import argparse
import json
from pathlib import Path

from probe_diagnostics_http import Client
from probe_diagnostics_http_matrix import _capture_search_counts, _frontend_status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--spool-root", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sessions", type=int, choices=(8, 12), default=8)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    sessions = []
    for row in data["rows"]:
        if row["session_id"] is None:
            continue
        counts = _capture_search_counts(args.spool_root, row["session_id"])
        expected = row["issued_requests_with_warmup"]
        sessions.append({"session_id": row["session_id"], "mode": row["mode"],
            "experiment": row["experiment"], "position": row["position"],
            "issued_search_requests_with_warmup": expected, "counts": counts,
            "counts_match_issued": all(value == expected for value in counts.values())})
    admin = Client(args.base_url)
    admin.login()
    status = admin.call("GET", "/api/admin/diagnostics/status")
    value = {"diagnostic_only": True, "sessions": sessions,
        "expected_sessions": args.expected_sessions,
        "all_counts_match_issued": len(sessions) == args.expected_sessions and all(row["counts_match_issued"] for row in sessions),
        "runtime_available": status["runtime"]["available"], "active_capture": bool(status["session"]["session"]),
        "backend_counters": {key: count for key, count in status["recorder"].items() if type(count) is int},
        "frontend_counters": _frontend_status(args.spool_root)}
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value[key] for key in ("all_counts_match_issued", "runtime_available", "active_capture")}))
    if not value["all_counts_match_issued"] or not value["runtime_available"] or value["active_capture"]:
        raise RuntimeError("Windows synthetic capture verification failed; preserve artifacts")


if __name__ == "__main__":
    main()
