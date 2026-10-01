"""Summarize synthetic session spool by event code without retaining payloads."""

import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(matrix, spool_root):
    rows = []
    for item in matrix["rows"]:
        session_id = item.get("session_id")
        if not session_id:
            continue
        components = {}
        for component in ("backend", "frontend"):
            folder = spool_root / component / "events" / session_id
            counts, sizes = Counter(), Counter()
            aggregate_calls = 0
            aggregate_groups = Counter()
            for path in sorted(folder.glob("*.jsonl")):
                with path.open("rb") as handle:
                    for line in handle:
                        try:
                            event = json.loads(line)
                            code = event.get("event_code")
                            if code == "success_aggregate":
                                value = event.get("counts", {}).get("count")
                                if type(value) is int and value >= 0:
                                    aggregate_calls += value
                                    group = "|".join(str(event.get(name) or "-") for name in
                                                     ("route_template", "stage", "dependency"))
                                    aggregate_groups[group] += value
                        except (ValueError, UnicodeDecodeError, AttributeError):
                            code = "invalid"
                        if not isinstance(code, str) or len(code) > 64:
                            code = "invalid"
                        counts[code] += 1
                        sizes[code] += len(line)
            components[component] = {"aggregate_calls": aggregate_calls,
                                     "aggregate_groups": dict(sorted(aggregate_groups.items())),
                                     "event_codes": {
                code: {"events": count, "bytes": sizes[code],
                       "bytes_per_event": sizes[code] / count}
                for code, count in sorted(counts.items())}}
        rows.append({"session_id": session_id, "mode": item["mode"],
                     "repeat": item["repeat"], "concurrency": item["concurrency"],
                     "components": components})
    return {"schema_version": 1, "rows": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--spool-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(json.loads(args.matrix.read_text()), args.spool_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"sessions={len(result['rows'])}")
