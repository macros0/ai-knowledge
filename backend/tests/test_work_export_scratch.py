"""Only abandoned, explicitly owned local export scratch may be removed."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.services.work.scratch import export_attempt, clean_export_attempts


def test_live_attempt_and_unmarked_directory_are_preserved(tmp_path):
    settings = SimpleNamespace(exports_dir=tmp_path)
    foreign = tmp_path / "attempts-v1" / ("a" * 32)
    foreign.mkdir(parents=True)
    (foreign / "valuable").write_text("keep")
    with export_attempt(settings) as attempt:
        (attempt / "archive.part").write_bytes(b"zip")
        clean_export_attempts(settings)
        assert attempt.is_dir()
        assert (foreign / "valuable").read_text() == "keep"
    assert not attempt.exists()
    assert foreign.exists()


def test_kernel_releases_attempt_lock_after_process_crash(tmp_path):
    code = """
import os, sys
from pathlib import Path
from types import SimpleNamespace
from app.services.work.scratch import export_attempt
with export_attempt(SimpleNamespace(exports_dir=Path(sys.argv[1]))) as attempt:
    (attempt / 'archive.part').write_bytes(b'incomplete')
    os._exit(7)
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], timeout=20)
    assert result.returncode == 7
    assert len(list((tmp_path / "attempts-v1").iterdir())) == 1
    clean_export_attempts(SimpleNamespace(exports_dir=tmp_path))
    assert list((tmp_path / "attempts-v1").iterdir()) == []


def test_cleanup_failure_does_not_replace_successful_result(tmp_path, monkeypatch):
    import app.services.work.scratch as scratch
    monkeypatch.setattr(scratch.shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("busy")))
    with export_attempt(SimpleNamespace(exports_dir=tmp_path)) as attempt:
        (attempt / "archive.part").write_bytes(b"zip")
    assert attempt.exists()


def test_absent_root_is_not_created_and_symlinks_are_not_followed(tmp_path):
    settings = SimpleNamespace(exports_dir=tmp_path / "absent")
    assert clean_export_attempts(settings) is None
    assert not settings.exports_dir.exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "valuable").write_text("keep")
    root = settings.exports_dir / "attempts-v1"
    root.mkdir(parents=True)
    try:
        os.symlink(outside, root / ("b" * 32), target_is_directory=True)
    except OSError:
        pytest.skip("symlink permission unavailable")
    clean_export_attempts(settings)
    assert (outside / "valuable").read_text() == "keep"


def test_attempt_directory_identity_cannot_escape_owned_root(tmp_path):
    with export_attempt(SimpleNamespace(exports_dir=tmp_path)) as attempt:
        assert attempt.resolve().parent == (tmp_path / "attempts-v1").resolve()
        assert len(attempt.name) == 32
        assert Path(attempt).is_dir()


def test_unknown_or_foreign_marker_is_preserved(tmp_path):
    settings = SimpleNamespace(exports_dir=tmp_path)
    root = tmp_path / "attempts-v1"
    for name, content in [("c" * 32, b"broken"), ("d" * 32, b'{"version": 99}')]:
        directory = root / name
        directory.mkdir(parents=True)
        (directory / ".export-attempt-v1").write_bytes(content)
    assert clean_export_attempts(settings, limit=1) == "c" * 32
    assert clean_export_attempts(settings, after="c" * 32, limit=1) == "d" * 32
    assert sorted(path.name for path in root.iterdir()) == ["c" * 32, "d" * 32]
