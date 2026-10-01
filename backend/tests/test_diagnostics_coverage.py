"""Bundle coverage must describe the capture policy, separately from losses."""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zipfile import ZipFile

from app.models.diagnostics import BundleRequest
from app.services.diagnostics.bundle import build_bundle
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.snapshot import collect_snapshot
from app.services.diagnostics.store import DiagnosticStore


def test_manifest_lists_mixed_capture_policies_and_modes(tmp_path):
    now = datetime.now(timezone.utc)
    first, second = str(uuid4()), str(uuid4())
    policy = {"level": "standard", "version": 1, "aggregate_interval_ms": 5000,
              "success_limit_per_second": 100, "trace_limit_per_second": 20,
              "slow_limit_per_second": 20, "max_inflight_traces": 512,
              "slow_thresholds_ms": {"http_search": 1000, "qdrant_db": 2000,
                                     "embeddings_proxy": 2000, "llm_chat": 5000, "pdf": 10000}}
    policies = [{"session_id": first, "capture_level": "standard", "policy_version": 1,
                 "policy_snapshot": policy},
                {"session_id": second, "capture_level": "detailed", "policy_version": 1,
                 "policy_snapshot": {**policy, "level": "detailed"}}]
    limits = DiagnosticLimits(min_free_bytes=0)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        request = BundleRequest(from_utc=now - timedelta(minutes=1), to_utc=now)
        snapshot = collect_snapshot(request, cutoff_at=now, store=store,
                                    metadata_provider=lambda _: ({}, [], policies))
        try:
            built = build_bundle(snapshot, store.root / "bundles" / "coverage.zip", limits)
            with ZipFile(built.path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
            assert manifest["format_version"] == 2
            assert {item["session_id"] for item in manifest["capture_policies"]} == {first, second}
            assert manifest["coverage_modes"]["sessions"][first] == "aggregated"
            assert manifest["coverage_modes"]["sessions"][second] == "sampled"
            assert manifest["partial"] is True  # frontend unavailable, separate from intentional aggregation
        finally:
            snapshot.release()


def test_manifest_marks_counters_unknown_before_current_process(tmp_path):
    now = datetime.now(timezone.utc)
    started = now - timedelta(seconds=30)
    limits = DiagnosticLimits(min_free_bytes=0)
    with DiagnosticStore(tmp_path / "spool", limits) as store:
        for seconds, expected in ((60, "unknown"), (10, "known")):
            request = BundleRequest(from_utc=now - timedelta(seconds=seconds), to_utc=now)
            snapshot = collect_snapshot(request, cutoff_at=now, store=store,
                                        metadata_provider=lambda _: ({}, [], []),
                                        recorder_status=lambda: {"started_at_utc": started.isoformat()})
            try:
                built = build_bundle(snapshot, store.root / "bundles" / f"counters-{seconds}.zip", limits)
                with ZipFile(built.path) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                assert manifest["loss_counters_state"]["backend_process"] == expected
            finally:
                snapshot.release()


def test_unknown_frontend_counter_history_is_not_event_loss(tmp_path):
    from app.services.diagnostics.snapshot import _frontend_status
    root = tmp_path / "frontend"
    root.mkdir()
    now = datetime.now(timezone.utc)
    (root / "status.json").write_text(json.dumps({"schema_version": 1,
        "boot_id": str(uuid4()), "running": True, "storage_degraded": False,
        "started_at_utc": now.isoformat(), "dropped": 0, "invalid": 0,
        "expired_queue": 0, "queued": 0}))
    counts, gap = _frontend_status(root, now - timedelta(minutes=1))
    assert counts["frontend_counters_unknown"] == 1
    assert gap is None


def test_frontend_slow_sampling_counter_is_visible_without_marking_loss(tmp_path):
    from app.services.diagnostics.snapshot import _frontend_status
    root = tmp_path / "frontend"
    root.mkdir()
    (root / "status.json").write_text(json.dumps({"schema_version": 1,
        "boot_id": str(uuid4()), "running": True, "storage_degraded": False,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "dropped": 0, "invalid": 0, "expired_queue": 0, "queued": 0,
        "sampled_out_slow": 7, "intentional_aggregated": 100, "secret": "CANARY_PRIVATE"}))
    counts, gap = _frontend_status(root)
    assert counts["frontend_sampled_out_slow"] == 7
    assert counts["frontend_intentional_aggregated"] == 100
    assert "CANARY_PRIVATE" not in json.dumps(counts)
    assert gap is None
