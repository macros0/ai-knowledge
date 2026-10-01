"""Capture levels must survive API, SQL upgrade and safe event reading."""

from dataclasses import FrozenInstanceError
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, text

from app.config import Settings
from app.models.diagnostics import SessionStart
from app.services.diagnostics.sanitize import sanitize_event


def _event(code, *, version=2, **extra):
    return {
        "schema_version": version,
        "event_id": str(uuid4()),
        "boot_id": str(uuid4()),
        "timestamp_utc": "2026-09-29T00:00:00+00:00",
        "component": "backend",
        "origin": "server",
        "level": "INFO",
        "event_code": code,
        **extra,
    }


def test_new_session_defaults_to_standard_and_rejects_unknown_level():
    assert SessionStart().capture_level == "standard"
    assert SessionStart().minutes == 15
    assert SessionStart(scope="search_chat", capture_level="detailed").doc_id is None
    with pytest.raises(ValidationError):
        SessionStart(capture_level="raw")


def test_policy_snapshot_is_immutable_and_bounded(tmp_path):
    from app.services.diagnostics.policy import build_policy

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "logs")
    policy = build_policy("standard", settings)
    assert policy.level == "standard"
    assert policy.version == 1
    assert policy.aggregate_interval_ms == 5000
    assert policy.success_limit_per_second == 10
    assert policy.trace_limit_per_second == 1
    assert policy.max_inflight_traces == 512
    with pytest.raises(FrozenInstanceError):
        policy.level = "detailed"
    with pytest.raises(ValueError):
        build_policy("raw", settings)


def test_v2_aggregate_accepts_only_bounded_counts_and_v1_stays_strict():
    counts = {"count": 2, "duration_sum_us": 52000, "duration_max_us": 42000,
              "le_10ms": 1, "le_50ms": 1, "le_250ms": 0, "le_1000ms": 0,
              "le_5000ms": 0, "le_30000ms": 0, "gt_30000ms": 0}
    aggregate = _event("success_aggregate", stage="search", outcome="success",
                       window_start_utc="2026-09-29T00:00:00+00:00",
                       window_end_utc="2026-09-29T00:00:05+00:00", counts=counts)
    assert sanitize_event(aggregate) is not None
    assert sanitize_event({**aggregate, "schema_version": 1}) is None
    assert sanitize_event({**aggregate, "counts": {**counts, "le_50ms": 2}}) is None
    assert sanitize_event({**aggregate, "query": "CANARY_PRIVATE"}) is None
    summary = _event("operation_summary", stage="search", duration_ms=42,
                     counts={"processed": 2}, outcome="success")
    assert sanitize_event(summary) is not None
    assert sanitize_event({**summary, "outcome": "failed"}) is None
    assert sanitize_event(_event("operation_failed", error_code="internal_error")) is not None


def test_legacy_rows_keep_detailed_policy_zero_after_forward_migration(tmp_path):
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"

    def load(name):
        spec = importlib.util.spec_from_file_location(name, versions / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    old = load("030b1c2d3e4f_diagnostics_sessions_bundles.py")
    new = load("040b1c2d3e4f_diagnostic_capture_policy.py")
    engine = create_engine("sqlite:///" + str(tmp_path / "policy.db"))
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            old.upgrade()
            session_id = str(uuid4())
            connection.execute(text("INSERT INTO diagnostic_sessions "
                "(id, status, scope, boot_id, created_by_id, created_by, created_at, expires_at) "
                "VALUES (:id, 'stopped', 'system', :boot, 'admin', 'admin', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"id": session_id, "boot": str(uuid4())})
            new.upgrade()
            row = connection.execute(text("SELECT capture_level, policy_version, policy_snapshot "
                                          "FROM diagnostic_sessions WHERE id=:id"), {"id": session_id}).one()
            assert row[0] == "detailed"
            assert row[1] == 0
            assert row[2] in ("{}", {})
            new.downgrade()
    engine.dispose()


def test_shared_v2_corpus_matches_backend_validator():
    corpus = json.loads((Path(__file__).resolve().parents[2] /
                         "tests/fixtures/diagnostics/events-v2.json").read_text(encoding="utf-8"))
    for event in corpus["valid"]:
        assert sanitize_event(event) is not None
        for change in corpus["invalid_changes"]:
            assert sanitize_event({**event, **change}) is None
