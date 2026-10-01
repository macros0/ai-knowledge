"""The probe must report what ran and when, without response content."""

import pytest

from test_scripts import probe_diagnostics as probe
from test_scripts import probe_diagnostics_http_matrix as http_probe
from test_scripts.analyze_diagnostics_matrix import analyze


@pytest.mark.parametrize("bad", [None, False, "0"])
def test_backend_loss_evidence_must_be_present_and_numeric(bad):
    baseline = {"corpus_size": 1, "concurrency": 1, "repeat": 1, "mode": "baseline",
                "p95_ms": 10, "peak_rss_bytes": {"backend": 100, "frontend": 100}}
    baseline.update(warmup_seconds=30, elapsed_seconds=60, requests=1000,
        recorder_delta=dict.fromkeys(("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts"), 0),
        frontend_delta=dict.fromkeys(("dropped", "invalid", "expired_queue"), 0),
        response_equality_pass=True, response_fingerprints={"query": "sha"})
    losses = dict.fromkeys(("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts"), 0)
    row = {**baseline, "mode": "standard", "warmup_seconds": 30, "elapsed_seconds": 60,
           "requests": 1000, "recorder_delta": losses,
           "frontend_delta": dict.fromkeys(("dropped", "invalid", "expired_queue"), 0)}
    assert analyze({"rows": [baseline, row]})["all_losses_pass"]
    if bad is None:
        del losses["dropped"]
    else:
        losses["dropped"] = bad
    assert not analyze({"rows": [baseline, row]})["all_losses_pass"]


def test_bundle_observer_uses_ui_cadence_without_losing_short_worker_overlap():
    now = [0.0]
    observed = []
    class Client:
        def call(self, method, route):
            observed.append(now[0])
            return {"items": [{"id": "bundle", "status": "ready" if now[0] >= 3 else "queued",
                               "size_bytes": 100}]}
    result = http_probe._wait_bundle(Client(), "bundle", clock=lambda: now[0],
                                    sleep=lambda delay: now.__setitem__(0, now[0] + delay))
    assert observed == [0, 3]
    assert result["poll_requests"] == 2
    assert result["poll_interval_seconds"] == 3
    # A prepare/ZIP shorter than the API poll interval still has real overlap.
    intervals = [(0.2, 0.4, 200), (0.5, 0.7, 200), (0.9, 1.1, 200), (2, 2.1, 100)]
    phases = [{"phase": "prepare", "started": 0.1, "finished": 0.6},
              {"phase": "zip", "started": 0.6, "finished": 1.0}]
    assert http_probe._worker_overlap(intervals, phases) == 3
    assert "building_started_monotonic" not in result


def test_fast_bundle_poll_is_explicit_and_timeout_is_bounded():
    now = [0.0]
    class Client:
        def call(self, method, route):
            return {"items": []}
    result = http_probe._wait_bundle(Client(), "bundle", poll_interval=.05, timeout=.12,
                                    clock=lambda: now[0],
                                    sleep=lambda delay: now.__setitem__(0, now[0] + delay))
    assert result["status"] == "timeout"
    assert result["poll_requests"] == 3
    assert now[0] == .12
    with pytest.raises(ValueError):
        http_probe._wait_bundle(Client(), "bundle", poll_interval=0)


def test_unknown_capture_level_fails_before_creating_a_run(tmp_path):
    root = tmp_path / "probe"
    with pytest.raises(ValueError, match="capture level"):
        probe.run(root, warmup_seconds=0, requests=1, capture_level="raw")
    assert not root.exists()


def test_phase_overlap_uses_sample_interval_not_builder_ready_time():
    sample = (10.0, 20.0)
    assert probe.phase_overlap(sample, (15.0, 25.0))
    assert not probe.phase_overlap(sample, (20.0, 25.0))
    assert not probe.phase_overlap(sample, (1.0, 10.0))


def test_peak_rss_is_high_water_not_last_reading():
    tracker = probe.PeakRssTracker()
    tracker.observe("parent", 100)
    tracker.observe("parent", 150)
    tracker.observe("parent", 90)
    assert tracker.peak("parent") == 150


def test_http_probe_counts_every_process_in_backend_cgroup(tmp_path):
    proc = tmp_path / "proc"
    cgroups = tmp_path / "cgroup"
    (proc / "101").mkdir(parents=True)
    (proc / "102").mkdir()
    (proc / "101" / "cgroup").write_text("0::/test-backend\n")
    (proc / "101" / "status").write_text("VmRSS:\t100 kB\n")
    (proc / "102" / "status").write_text("VmRSS:\t200 kB\n")
    (cgroups / "test-backend").mkdir(parents=True)
    (cgroups / "test-backend" / "cgroup.procs").write_text("101\n102\n")
    assert http_probe._cgroup_rss_bytes(101, proc_root=proc, cgroup_root=cgroups) == (
        300 * 1024, 200 * 1024)


def test_matrix_rejects_missing_node_counters_and_zip_queue_only_overlap():
    baseline = {"corpus_size": 1, "concurrency": 1, "repeat": 1,
                "mode": "baseline", "p95_ms": 10.0,
                "peak_rss_bytes": {"backend": 100, "frontend": 100}, "bundle": None}
    baseline.update(warmup_seconds=30, elapsed_seconds=60, requests=1000,
        recorder_delta=dict.fromkeys(("dropped", "invalid", "expired_queue", "storage_errors", "drain_timeouts"), 0),
        frontend_delta=dict.fromkeys(("dropped", "invalid", "expired_queue"), 0),
        response_equality_pass=True, response_fingerprints={"query": "sha"})
    captured = {**baseline, "mode": "detailed", "p95_ms": 10.5,
                "warmup_seconds": 30, "elapsed_seconds": 60, "requests": 1000,
                "recorder_delta": {}, "frontend_delta": {},
                "peak_rss_bytes": {"backend": 110, "frontend": 110}}
    result = analyze({"rows": [baseline, captured]})
    assert not result["all_losses_pass"]
    captured["frontend_delta"] = {key: 0 for key in ("dropped", "invalid", "expired_queue")}
    captured["bundle"] = {"status": "ready", "size_bytes": 100,
                          "observed_building": False, "overlapping_requests": 2}
    result = analyze({"rows": [baseline], "image_ids": {"backend": "image-a"}},
                     bundle={"rows": [baseline, captured],
                             "image_ids": {"backend": "image-a"}})
    assert not result["all_zip_overlap_pass"]


def test_matrix_refuses_zip_comparison_across_builds():
    with pytest.raises(ValueError, match="image IDs"):
        analyze({"rows": [], "image_ids": {"backend": "image-a"}},
                bundle={"rows": [], "image_ids": {"backend": "image-b"}})


def test_http_fingerprint_checks_complete_response_without_persisting_content():
    response = {"query": "synthetic", "hits": [{"score": 1.0, "snippet": "fixture"}]}
    first = http_probe._response_fingerprint(response)
    assert len(first) == 64
    assert first == http_probe._response_fingerprint({"hits": response["hits"], "query": "synthetic"})
    assert first != http_probe._response_fingerprint({**response, "hits": []})


def test_http_phase_distribution_uses_only_requests_overlapping_actual_child():
    intervals = [(1, 2, 1000), (2, 3, 1000), (3, 5, 2000)]
    phases = [{"phase": "prepare", "started": 1.5, "finished": 2.5},
              {"phase": "zip", "started": 3, "finished": 4}]
    result = http_probe._phase_distributions(intervals, phases)
    assert result["prepare"]["requests"] == 2
    assert result["zip"]["requests"] == 1
    assert result["zip"]["p95_ms"] == 2000


def test_capture_counts_distinguish_aggregates_requests_operations_and_dependencies(tmp_path):
    import json
    root = tmp_path / "spool"
    session = "synthetic-session"
    directory = root / "backend" / "events" / session
    directory.mkdir(parents=True)
    common = {"diagnostic_session_id": session, "component": "backend"}
    rows = [{**common, "event_code": "success_aggregate", "route_template": "/api/search", "counts": {"count": 2}},
            {**common, "event_code": "request_finished", "route_template": "/api/search"},
            {**common, "event_code": "success_aggregate", "stage": "search", "counts": {"count": 3}},
            {**common, "event_code": "dependency_call_finished", "dependency": "qdrant"},
            {**common, "event_code": "success_aggregate", "dependency": "qdrant", "counts": {"count": 2}},
            {**common, "event_code": "success_aggregate", "route_template": "/api/settings", "counts": {"count": 4}},
            {**common, "component": "frontend", "event_code": "success_aggregate",
             "route_template": "/api/settings", "counts": {"count": 4}}]
    (directory / "segment.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    totals = http_probe._capture_search_counts(root, session)
    assert totals["backend_requests"] == 3
    assert totals["backend_operations"] == 3
    assert totals["backend_qdrant"] == 3
    mixed = http_probe._capture_search_counts(root, session, include_settings=True)
    assert mixed["backend_settings"] == mixed["frontend_settings"] == 4


def test_corpora_have_explicit_distinct_route_and_query_distributions():
    from collections import Counter
    small = Counter(http_probe._workload_slot(1, i) for i in range(30))
    assert small == {("/api/search", query): 10 for query in http_probe.QUERIES}
    large = Counter(http_probe._workload_slot(100, i) for i in range(10))
    assert large == {("/api/search", http_probe.QUERIES[0]): 4,
                     ("/api/search", http_probe.QUERIES[1]): 3,
                     ("/api/search", http_probe.QUERIES[2]): 2,
                     ("/api/settings", None): 1}


def test_shared_vm_rss_is_counted_once_with_raw_evidence():
    sample = http_probe._address_space_rss(101, [101, 102, 103],
        rss_reader=lambda pid: {101: 400, 102: 400, 103: 25}[pid],
        vm_compare=lambda left, right: {frozenset((101, 102)): True}.get(frozenset((left, right)), False))
    assert sample == {"total": 425, "descendants": 25, "raw_total": 825,
                      "raw_descendants": 425, "unknown_comparisons": 0, "shared_vm_members": 1}


def test_unavailable_vm_comparison_keeps_real_child_rss():
    sample = http_probe._address_space_rss(101, [101, 102],
        rss_reader=lambda pid: {101: 400, 102: 350}[pid], vm_compare=lambda a, b: None)
    assert sample["total"] == sample["raw_total"] == 750
    assert sample["descendants"] == 350
    assert sample["unknown_comparisons"] > 0


def test_exec_between_vm_checks_rereads_distinct_child():
    reads = iter((400, 400, 12))
    comparisons = iter((True, False))
    sample = http_probe._address_space_rss(101, [101, 102],
        rss_reader=lambda pid: next(reads), vm_compare=lambda a, b: next(comparisons))
    assert sample["raw_total"] == 800
    assert sample["total"] == 412
    assert sample["descendants"] == 12
    assert sample["shared_vm_members"] == 0


def test_distinct_large_child_is_not_hidden():
    sample = http_probe._address_space_rss(101, [101, 102],
        rss_reader=lambda pid: {101: 400, 102: 350}[pid], vm_compare=lambda a, b: False)
    assert sample["total"] == 750
    assert sample["descendants"] == 350


def test_unknown_second_vm_check_retains_conservative_rss():
    comparisons = iter((True, None))
    sample = http_probe._address_space_rss(101, [101, 102],
        rss_reader=lambda pid: 400, vm_compare=lambda a, b: next(comparisons))
    assert sample["total"] == 800
    assert sample["unknown_comparisons"] == 1


def test_missing_container_rss_cannot_be_reported_as_zero():
    with pytest.raises(RuntimeError, match="Container RSS unavailable"):
        http_probe._address_space_rss(101, [101], rss_reader=lambda pid: None)


@pytest.mark.parametrize("alive", [True, None])
def test_live_or_unknown_child_rss_is_never_zero(alive):
    with pytest.raises(RuntimeError, match="Child RSS unavailable"):
        http_probe._address_space_rss(101, [101, 102],
            rss_reader=lambda pid: 100 if pid == 101 else None,
            vm_compare=lambda left, right: False, process_alive=lambda pid: alive)


def test_confirmed_exited_child_may_be_omitted():
    result = http_probe._address_space_rss(101, [101, 102],
        rss_reader=lambda pid: 100 if pid == 101 else None,
        vm_compare=lambda left, right: False, process_alive=lambda pid: False)
    assert result["total"] == result["raw_total"] == 100
    assert result["descendants"] == 0


def test_child_read_retries_transient_exec_gap_before_measuring():
    readings = iter((100, None, 200))
    result = http_probe._address_space_rss(101, [101, 102], rss_reader=lambda pid: next(readings),
        vm_compare=lambda left, right: False, process_alive=lambda pid: True)
    assert result["total"] == result["raw_total"] == 300
    assert result["descendants"] == 200


def test_unreadable_live_child_after_vm_transition_is_not_zero():
    readings = iter((100, 100, None, None, None, None, None))
    comparisons = iter((True, False))
    with pytest.raises(RuntimeError, match="Child RSS unavailable"):
        http_probe._address_space_rss(101, [101, 102], rss_reader=lambda pid: next(readings),
            vm_compare=lambda left, right: next(comparisons), process_alive=lambda pid: True)


def test_child_rss_gap_waits_for_confirmed_exit(monkeypatch):
    alive = iter((True, False))
    waits = []
    monkeypatch.setattr(http_probe.time, "sleep", waits.append)
    result = http_probe._address_space_rss(101, [101, 102],
        rss_reader=lambda pid: 100 if pid == 101 else None,
        vm_compare=lambda left, right: False, process_alive=lambda pid: next(alive))
    assert result["total"] == result["raw_total"] == 100
    assert result["descendants"] == 0
    assert waits == [0.001]


def test_child_rss_after_two_missing_reads_is_counted(monkeypatch):
    readings = iter((100, None, None, 200))
    waits = []
    monkeypatch.setattr(http_probe.time, "sleep", waits.append)
    result = http_probe._address_space_rss(101, [101, 102],
        rss_reader=lambda pid: next(readings), vm_compare=lambda left, right: False,
        process_alive=lambda pid: True)
    assert result["total"] == result["raw_total"] == 300
    assert result["descendants"] == 200
    assert waits == [0.001]


@pytest.mark.parametrize("failure, expected", [
    (ProcessLookupError(3, "gone"), False),
    (PermissionError(13, "denied"), None),
])
def test_proc_stat_failure_distinguishes_exit_from_unknown(failure, expected):
    class UnreadableProc:
        def __truediv__(self, value):
            return self

        def read_text(self):
            raise failure
    assert http_probe._process_alive(102, proc_root=UnreadableProc()) is expected
