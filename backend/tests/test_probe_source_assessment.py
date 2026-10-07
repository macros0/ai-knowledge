"""Offline probe validates contracts; it cannot accept model quality or latency."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "backend/test_scripts/probe_source_assessment.py"


def test_default_dry_run_does_not_call_a_model_or_claim_acceptance(tmp_path):
    fixture = tmp_path / "cases.json"
    fixture.write_text(
        json.dumps(
            [
                {
                    "query": "compensation",
                    "blocks": [
                        {"doc_id": "synthetic", "title": "HR", "content": "employee record"}
                    ],
                    "expected_decision": "reject",
                    "provider_items": [{"source_index": 1, "label": "irrelevant"}],
                }
            ]
        )
    )
    import sys

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(fixture)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["live"] is False and report["acceptance"] == "not_measured"
    assert report["completion_count"] == 0
    assert report["cases"][0]["decision"] == "reject"


def test_live_requires_explicit_profile_before_loading_any_endpoint(tmp_path):
    import sys

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "missing.json"), "--live"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and "--profile" in result.stderr


def test_bad_fixture_fails_without_exposing_contents(tmp_path):
    import sys

    fixture = tmp_path / "bad.json"
    fixture.write_text('{"secret":"private"}')
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(fixture)], capture_output=True, text=True
    )
    assert result.returncode != 0 and "private" not in result.stderr
