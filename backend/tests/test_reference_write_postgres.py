"""Comparative write timing, no invented write regression threshold."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.tag_registry import TagRegistry
from tests.test_reference_postgres_concurrency import reference_pg_db as _pg_fixture

reference_pg_db = _pg_fixture


def test_write_api_p50_p95_against_committed_baseline(reference_pg_db):
    baseline = os.environ.get("REFERENCE_TEST_BASELINE_APP")
    if not baseline:
        pytest.skip("REFERENCE_TEST_BASELINE_APP is not configured")
    assert (Path(baseline) / "app/main.py").is_file()
    TagRegistry().add([f"benchmark-{index:03d}" for index in range(50)])
    script = Path(__file__).parent / "support/reference_write_benchmark.py"
    samples = {"baseline": [], "candidate": []}
    for name in ("baseline", "candidate", "candidate", "baseline"):
        environment = dict(os.environ)
        if name == "baseline":
            environment["PYTHONPATH"] = baseline + os.pathsep + str(Path(__file__).parents[2] / "doc-parser/src")
        process = subprocess.run([sys.executable, str(script)], env=environment, capture_output=True, text=True, timeout=90)
        assert process.returncode == 0, process.stderr
        samples[name].append(json.loads(process.stdout.strip().splitlines()[-1]))
    print(json.dumps({"write_samples": samples, "baseline_mode": "legacy writer", "candidate_mode": "mandatory CAS, lease disabled"}))
