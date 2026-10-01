"""Three paired standard/c8/active-ZIP checks on the frozen disposable stack."""
import json

if __package__:
    from . import run_diagnostics_final_ct as queue
    from .analyze_diagnostics_matrix import analyze
    from .probe_diagnostics_http_matrix import measure, _containers, _image_ids
    from .probe_diagnostics_http import Client
else:
    import run_diagnostics_final_ct as queue
    from analyze_diagnostics_matrix import analyze
    from probe_diagnostics_http_matrix import measure, _containers, _image_ids
    from probe_diagnostics_http import Client

EXPECTED_IMAGES = {
    "backend": "sha256:2aad41d130c6d877b8bcfd98f3ed7381b4f4acaa2e6a0fe7fec5e0cfdd8bb023",
    "frontend": "sha256:a37ae1505d48528941a849e8ca7c07e568e6c453bdfa59e52626d5fba35b4799",
}


def main():
    root = queue.ROOT
    output = root / "targeted-standard-c8-active-v15-v11.json"
    queue.STATE = root / "targeted-standard-c8-active-v15-v11-state.json"
    if queue.STATE.exists() or output.exists():
        raise FileExistsError("Preserve the previous targeted result; inspect before a retry")
    containers = _containers(queue.PROJECT)
    def frozen():
        if _image_ids(containers) != EXPECTED_IMAGES:
            raise RuntimeError("Disposable stack image changed or differs from frozen v14/v11")
    frozen()
    admin = Client("http://127.0.0.1:18084")
    admin.login()
    current = admin.call("GET", "/api/admin/diagnostics/status")["session"]["session"]
    if current:
        raise RuntimeError("Another capture is active; targeted workload was not started")
    rows = []
    data = {"schema_version": 3, "scope": "targeted acceptance subset; not the complete HTTP matrix",
            "image_ids": EXPECTED_IMAGES, "bundle_poll_interval_seconds": 3, "rows": rows}
    def collect(mode, repeat):
        frozen()
        row = measure("http://127.0.0.1:18084", concurrency=8,
                      warmup_seconds=30, measurement_seconds=60, min_requests=1000,
                      mode=mode, repeat=repeat, corpus_size=1,
                      spool_root=root / "diagnostics-1doc", build_bundle=mode == "standard",
                      actor_username=f"diag.bench{repeat + 3:02d}", containers=containers,
                      bundle_poll_interval=3)
        rows.append(row)
        output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        frozen()
        print(json.dumps({"repeat": repeat, "mode": mode, "p95_ms": row["p95_ms"],
                          "requests": row["requests"], "capture_counts": row["capture_search_counts"],
                          "expected": row["expected_capture_search_calls"]}), flush=True)
        if mode == "standard" and row["capture_counts_pass"] is not True:
            raise queue.MatrixGateError(["all_capture_counts_pass"])
        return {"measurement_collected": True}
    def validate():
        reference = {**data, "rows": [row for row in rows if row["mode"] == "baseline"]}
        captured = {**data, "rows": [row for row in rows if row["mode"] == "standard"]}
        result = analyze(reference, bundle=captured)
        result["scope"] = data["scope"]
        output.with_name(output.stem + "-analysis.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        failed = [key for key in queue.MATRIX_GATES if result.get(key) is not True]
        if failed:
            raise queue.MatrixGateError(failed)
        return {key: result[key] for key in queue.MATRIX_GATES}
    for repeat in (1, 2, 3):
        order = ("standard", "baseline") if repeat == 2 else ("baseline", "standard")
        for mode in order:
            queue.run(f"collect-{mode}-r{repeat}", None, validate=lambda mode=mode: collect(mode, repeat))
        queue.run(f"validate-pair-r{repeat}", None, validate=validate)
    print("All three targeted pairs passed; full acceptance remains separately scoped", flush=True)


if __name__ == "__main__":
    main()
