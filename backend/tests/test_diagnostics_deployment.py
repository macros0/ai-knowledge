"""Production topology keeps diagnostic files bounded and outside business backup data."""
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_compose_diagnostics_mounts_and_log_rotation():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    for service in ("backend", "frontend", "postgres", "qdrant", "migrate"):
        assert services[service]["logging"] == {
            "driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}}
    backend, frontend = services["backend"], services["frontend"]
    backend_mounts = "\n".join(backend["volumes"])
    frontend_mounts = "\n".join(frontend["volumes"])
    assert "${OKF_DIAGNOSTICS_DIR" in backend_mounts
    assert "${OKF_DIAGNOSTICS_DIR" in frontend_mounts
    assert "/diagnostics/frontend:ro" in backend_mounts
    assert "/diagnostics/control:ro" in frontend_mounts
    assert "/diagnostics/backend" not in frontend_mounts
    assert "/data" not in frontend_mounts
    assert "/var/run/docker.sock" not in backend_mounts + frontend_mounts
    assert backend["environment"]["DIAGNOSTICS_DIR"] == "/diagnostics/backend"
    assert frontend["environment"]["OKF_FRONTEND_DIAGNOSTICS_DIR"] == "/diagnostics/frontend"
    assert backend.get("ports") is None
    assert frontend["ports"] == ["${FRONTEND_PORT:-8080}:3000"]


@pytest.mark.parametrize("mode", ["bundled", "external"])
def test_production_example_keeps_diagnostics_separate(mode):
    values = (ROOT / "deploy" / "production" / f"{mode}.env.example").read_text(encoding="utf-8")
    assert "OKF_DIAGNOSTICS_DIR=" in values
    assert "DIAGNOSTICS_CAPTURE_ENABLED=true" in values
    assert "DIAGNOSTICS_BUNDLE_ENABLED=true" in values
    assert "DIAGNOSTICS_DOWNLOAD_ENABLED=true" in values


def test_bootstrap_rejects_link_and_data_overlap(tmp_path):
    script = ROOT / "backend" / "scripts" / "prepare_diagnostics_dirs.py"
    data = tmp_path / "data"
    data.mkdir()
    roots = [data / "diagnostics"]
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(data, target_is_directory=True)
        roots.append(linked)
    except OSError:
        pass  # Windows without Developer Mode cannot create test symlinks.
    for root in roots:
        result = subprocess.run([sys.executable, str(script), "--root", str(root),
                                 "--data-dir", str(data), "--app-uid", "1000", "--app-gid", "1000"],
                                capture_output=True, text=True, timeout=10, check=False)
        assert result.returncode != 0
        assert not (data / "diagnostics" / "backend").exists()
