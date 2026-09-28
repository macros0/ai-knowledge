"""Privacy contracts: arbitrary exception/content fields must never be persisted."""
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics.sanitize import encode_event, safe_exception, sanitize_event


def event(**changes):
    return {
        "schema_version": 1, "event_id": str(uuid4()),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(), "boot_id": str(uuid4()),
        "component": "backend", "level": "ERROR", "event_code": "operation_failed",
        "origin": "server", "error_code": "internal_error", **changes,
    }


def test_secret_values_never_reach_encoded_event():
    # Accepting unknown text would silently make the diagnostic file a document export.
    for key in ("message", "args", "filename", "query", "sql", "body", "authorization"):
        assert sanitize_event(event(**{key: "CANARY_PRIVATE_DOCUMENT_PASSWORD"})) is None
    try:
        raise ValueError("CANARY_PRIVATE_DOCUMENT_PASSWORD")
    except ValueError as exc:
        cleaned = sanitize_event(event(**safe_exception(exc)))
    output = encode_event(cleaned)
    assert b"CANARY_PRIVATE_DOCUMENT_PASSWORD" not in output
    assert json.loads(output)["error_code"] == "internal_error"


@pytest.mark.parametrize("changes", [
    {"event_code": "user_supplied"}, {"schema_version": 2},
    {"request_id": "secret\npassword"}, {"http_method": "GET?token=secret"},
    {"route_template": "/documents/private.docx?token=secret"},
    {"error_code": "PRIVATE_DOCUMENT_CONTENT"},
    {"frames": [{"module": "C:/secret/password.py", "function": "private", "line": 1}]},
    {"counts": {"customer_name": 3}}, {"duration_ms": float("nan")},
])
def test_unknown_fields_and_event_codes_rejected(changes):
    assert sanitize_event(event(**changes)) is None


def test_utf8_size_limit_and_stack_cap():
    frames = [{"module": "app/services/pipeline.py", "function": "run", "line": 10}] * 100
    encoded = encode_event(event(frames=frames))
    assert len(encoded) <= 8192
    assert len(json.loads(encoded)["frames"]) == 32


def test_cycles_and_broken_repr_are_safe():
    class Broken(ValueError):
        def __str__(self):
            raise AssertionError("exception text must not be formatted")

        def __repr__(self):
            raise AssertionError("exception repr must not be formatted")
    exc = Broken("CANARY")
    exc.__cause__ = exc
    payload = safe_exception(exc)
    assert payload["exception_type"] == "UnknownError"
    assert "CANARY" not in json.dumps(payload)


def test_shared_fixture_and_malformed_field_types():
    fixture = json.loads((Path(__file__).resolve().parents[2] / "tests/fixtures/diagnostics/events-v1.json").read_text())
    for raw in fixture["valid"]:
        assert sanitize_event(raw) is not None
        for changes in fixture["invalid_changes"]:
            assert sanitize_event({**raw, **changes}) is None
    for key in ("component", "level", "origin", "event_code", "timestamp_utc", "schema_version"):
        for bad_value in ([], {}, None, True):
            assert sanitize_event(event(**{key: bad_value})) is None


def test_limits_reject_overpromised_budget():
    with pytest.raises(ValueError):
        DiagnosticLimits(backend_bytes=100, baseline_bytes=101)
    with pytest.raises(ValueError):
        DiagnosticLimits(total_bytes=500, backend_bytes=480, frontend_bytes=21)


@pytest.mark.parametrize("frame", [
    {"module": "frontend/src/lib/backendFetch.js", "function": "client", "line": 12},
    {"module": "frontend/_next/abcdef123456.js", "function": "CANARY", "line": 12},
    {"module": "https://host/_next/abcdef123456.js?token=CANARY", "function": "client", "line": 12},
])
def test_client_frames_reject_arbitrary_names_and_server_paths(frame):
    assert sanitize_event(event(component="browser", origin="client_reported", event_code="browser_error", frames=[frame])) is None


def test_settings_validate_diagnostics_budgets_and_external_root(tmp_path):
    from app.config import Settings
    with pytest.raises(ValueError):
        Settings(_env_file=None, diagnostics_session_mb=600)
    with pytest.raises(ValueError):
        Settings(_env_file=None, data_dir=tmp_path, diagnostics_dir=tmp_path / "logs")
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "logs")
    assert isinstance(settings.diagnostics_dir, Path)
    assert settings.diagnostics_limits().backend_bytes == 480 * 1048576
