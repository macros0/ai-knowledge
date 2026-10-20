"""Exercise packaged stdout and OS ownership in separate disposable processes."""
import json
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process/SIGKILL proof")

CHILD = r"""
import os, sys, time
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app import config
from app.db.models import AuditLog, AuditOutbox
from app.services import audit
from app.services.audit_outbox import AuditDispatcher

folder = Path(sys.argv[1])
mode = sys.argv[2]
settings = config.Settings(_env_file=None, data_dir=folder, audit_stdout_mode="durable")
config.get_settings = lambda: settings
url = os.environ.get("AUDIT_PROCESS_DATABASE_URL")
if url:
    from sqlalchemy.engine import make_url
    parsed = make_url(url)
    assert parsed.get_backend_name() == "postgresql" and parsed.database.startswith("audit_test_")
else:
    url = "sqlite:///" + str(folder / "process.db")
engine = create_engine(url)
sessions = sessionmaker(engine)
if mode == "seed":
    AuditLog.__table__.create(engine)
    AuditOutbox.__table__.create(engine)
    with sessions.begin() as session:
        audit.record_in_session(session, action_type=audit.DOCUMENT_UPLOAD, username="Unicode-тест")
    sys.exit(0)
dispatcher = AuditDispatcher(settings, session_factory=sessions)
if mode == "crash":
    dispatcher._mark_written = lambda audit_id: os._exit(73)
if mode == "partial":
    def partial(payload):
        os.write(1, b"\n" + payload[:20])
        os._exit(74)
    dispatcher.write_line = partial
if mode == "lock":
    dispatcher._lock()
    (folder / "lock-ready").write_text("ready")
    while True: time.sleep(.1)
try:
    dispatcher.start()
except ValueError:
    sys.exit(4)
if mode in {"crash", "partial"}:
    time.sleep(10)
    sys.exit(9)
for _ in range(100):
    if dispatcher.status()["counters"].get("stdout_written", 0): break
    time.sleep(.02)
assert dispatcher.stop(timeout_seconds=2)
engine.dispose()
"""


def run_child(tmp_path, mode):
    return subprocess.run([sys.executable, "-c", CHILD, str(tmp_path), mode], capture_output=True, timeout=15)


def test_process_crash_after_stdout_repeats_exact_payload(tmp_path):
    assert run_child(tmp_path, "seed").returncode == 0
    crashed = run_child(tmp_path, "crash")
    assert crashed.returncode == 73, crashed.stderr.decode()
    restarted = run_child(tmp_path, "deliver")
    assert restarted.returncode == 0, restarted.stderr.decode()
    assert crashed.stdout == restarted.stdout
    event = json.loads(restarted.stdout)
    assert event["actor"]["name"] == "Unicode-тест"
    import sqlite3
    with sqlite3.connect(tmp_path / "process.db") as db:
        assert db.execute("SELECT status,attempt_count,payload FROM audit_outbox").fetchone() == ("stdout_written", 2, restarted.stdout.strip().decode())


def test_process_partial_write_is_fenced_before_full_retry(tmp_path):
    assert run_child(tmp_path, "seed").returncode == 0
    partial = run_child(tmp_path, "partial")
    assert partial.returncode == 74
    restarted = run_child(tmp_path, "deliver")
    assert restarted.returncode == 0, restarted.stderr.decode()
    frames = (partial.stdout + restarted.stdout).splitlines()
    events = []
    for frame in frames:
        try: events.append(json.loads(frame))
        except ValueError: pass
    assert events == [json.loads(restarted.stdout)]
    assert restarted.stdout.startswith(b"\n") and restarted.stdout.endswith(b"\n")


def test_second_process_refused_and_sigkill_releases_lock(tmp_path):
    assert run_child(tmp_path, "seed").returncode == 0
    owner = subprocess.Popen([sys.executable, "-c", CHILD, str(tmp_path), "lock"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        while not (tmp_path / "lock-ready").exists() and time.monotonic() < deadline:
            assert owner.poll() is None
            time.sleep(.02)
        assert (tmp_path / "lock-ready").exists()
        assert run_child(tmp_path, "deliver").returncode == 4
    finally:
        owner.kill()
        owner.communicate(timeout=3)
    assert owner.returncode == -9
    restarted = run_child(tmp_path, "deliver")
    assert restarted.returncode == 0, restarted.stderr.decode()
    assert json.loads(restarted.stdout)["action"] == "document_upload"
