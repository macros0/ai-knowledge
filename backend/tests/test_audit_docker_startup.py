"""Opt-in proof against the built Linux image and a disposable PostgreSQL."""
import os
import subprocess

import pytest


def test_packaged_entrypoint_has_user_home_and_audit_preflight():
    image = os.environ.get("AUDIT_DOCKER_TEST_IMAGE")
    url = os.environ.get("AUDIT_DOCKER_TEST_POSTGRES_URL")
    network = os.environ.get("AUDIT_DOCKER_TEST_NETWORK")
    if not all((image, url, network)):
        pytest.skip("Built image and disposable Docker PostgreSQL required")
    from sqlalchemy.engine import make_url
    assert make_url(url).database.startswith("audit_test_")
    child = """
import os,pwd
from pathlib import Path
assert os.getuid() != 0
assert Path.home() == Path(pwd.getpwuid(os.getuid()).pw_dir), (os.getuid(),str(Path.home()))
from app.config import get_settings
from app.services.audit_runtime import validate_audit_transport_profile,preflight_audit
validate_audit_transport_profile(get_settings())
preflight_audit(get_settings())
print('nonroot-home-and-postgres-audit-ready')
"""
    result = subprocess.run([
        "docker", "run", "--rm", "--network", network,
        "-e", "DATABASE_URL=" + url, "-e", "AUDIT_STDOUT_MODE=durable",
        "-e", "DATA_DIR=/data", image, "python", "-c", child,
    ], capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "nonroot-home-and-postgres-audit-ready"
