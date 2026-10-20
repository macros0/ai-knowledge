"""Private CLI reports only observable DB state and safely fails closed."""
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine
from app.db.models import AuditLog, AuditOutbox

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/audit_delivery_status.py'


def environment(url=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith(('DATABASE_', 'AUTH_', 'KEYCLOAK_', 'SSO_'))}
    env['ENVIRONMENT'] = 'development'
    if url is not None: env['DATABASE_URL'] = url
    return env


def test_cli_database_failure_is_nonzero_without_url_or_empty_queue(tmp_path):
    url = f"sqlite:///{tmp_path / 'CANARY_DB_DIR' / 'missing.db'}"
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=tmp_path, env=environment(url), capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and not result.stdout
    assert result.stderr.strip() == 'Audit status unavailable'
    assert 'CANARY' not in result.stderr


def test_cli_reports_unknown_runtime_and_ignores_root_dotenv(tmp_path):
    url = f"sqlite:///{tmp_path / 'owned-status.db'}"
    engine = create_engine(url)
    try:
        AuditLog.__table__.create(engine); AuditOutbox.__table__.create(engine)
    finally: engine.dispose()
    (tmp_path / '.env').write_text('DATABASE_URL=invalid-CANARY\n')
    private = tmp_path / 'runtime.env'
    private.write_text('DATABASE_URL=' + url + '\nENVIRONMENT=development\n')
    private.chmod(0o600)
    for arguments, env in [([], environment(url)), (['--runtime-env-file', str(private)], environment())]:
        result = subprocess.run([sys.executable, str(SCRIPT), *arguments], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        status = json.loads(result.stdout)
        assert status['runtime_state'] == 'unknown'
        assert status['counts'] == {'pending': 0, 'held': 0, 'stdout_written': 0}
        assert 'payload' not in result.stdout and 'CANARY' not in result.stdout and 'healthy' not in result.stdout
