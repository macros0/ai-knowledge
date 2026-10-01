"""A failed measurement must preserve safe lease and recorder observations."""
import json
from urllib.error import HTTPError

import pytest

from test_scripts.probe_diagnostics_admission import observe_measure, snapshot


def test_http_failure_preserves_observations_without_response_or_error_text(tmp_path):
    output = tmp_path / "observed.json"

    def fail():
        raise HTTPError("http://private/CANARY", 503, "CANARY_SECRET", {}, None)

    with pytest.raises(HTTPError):
        observe_measure(output, measure=fail, read=lambda: {"control": {"active": True}})
    saved = json.loads(output.read_text())
    assert saved["failure"] == {"error_type": "HTTPError", "http_status": 503}
    assert saved["observations"][0]["control"]["active"] is True
    assert "CANARY" not in output.read_text()


def test_observer_allowlists_files_and_preserves_success(tmp_path):
    (tmp_path / "backend/control").mkdir(parents=True)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend/control/capture.json").write_text(json.dumps({
        "active": True, "revision": 2, "lease_until": 100, "private": "CANARY",
    }))
    (tmp_path / "frontend/status.json").write_text(json.dumps({
        "running": True, "dropped": 0, "private": "CANARY",
    }))
    output = tmp_path / "observed.json"
    result = observe_measure(output, measure=lambda: {"capture_counts_pass": True},
                             read=lambda: snapshot(tmp_path))
    assert result["capture_counts_pass"]
    saved = json.loads(output.read_text())
    assert saved["result"] == result
    assert saved["observations"][0]["frontend"] == {"running": True, "dropped": 0}
    assert "CANARY" not in output.read_text()
    with pytest.raises(FileExistsError):
        observe_measure(output, measure=lambda: pytest.fail("overwrote evidence"), read=lambda: {})


def test_wrapped_bundle_http_failure_retains_status_without_private_cause(tmp_path):
    output = tmp_path / "bundle-failed.json"

    def fail():
        try:
            raise HTTPError("http://private/CANARY", 429, "CANARY_SECRET", {}, None)
        except HTTPError as exc:
            raise RuntimeError("Synthetic ZIP failed") from exc

    with pytest.raises(RuntimeError):
        observe_measure(output, measure=fail, read=lambda: {})
    assert json.loads(output.read_text())["failure"]["http_status"] == 429
    assert "CANARY" not in output.read_text()
