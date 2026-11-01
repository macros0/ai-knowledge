"""Local export attempts: kernel locks survive threads and release on process death.

This is ephemeral per-instance storage, never an artifact store or diagnostic
subprocess directory. Only this module's UUID directories and markers are owned.
"""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
from uuid import uuid4

logger = logging.getLogger(__name__)
_NAME = re.compile(r"[0-9a-f]{32}\Z")
_MARKER = ".export-attempt-v1"


def _linked(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _root(settings):
    base = Path(settings.exports_dir)
    root = base / "attempts-v1"
    if _linked(base) or _linked(root):
        raise ValueError("Export scratch cannot be a link")
    return root


def _lock(file):
    file.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(file):
    file.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _remove(root, attempt):
    """Validate the absolute target and every member before recursive removal."""
    if not _NAME.fullmatch(attempt.name) or _linked(root) or _linked(attempt):
        return
    absolute_root, absolute_target = root.resolve(), attempt.resolve()
    if absolute_target.parent != absolute_root or absolute_target == absolute_root:
        return
    pending, seen = [absolute_target], 0
    while pending:
        directory = pending.pop()
        for member in directory.iterdir():
            seen += 1
            if seen > 4096 or _linked(member) or not member.resolve().is_relative_to(absolute_target):
                return
            if member.is_dir():
                pending.append(member)
    shutil.rmtree(absolute_target)


@contextmanager
def export_attempt(settings):
    root = _root(settings)
    root.mkdir(parents=True, exist_ok=True)
    attempt = root / uuid4().hex
    attempt.mkdir()
    try:
        with (attempt / _MARKER).open("x+b") as marker:
            # Lock before writing; a crash before the marker is complete leaves
            # an unrecognised directory, which the cleaner conservatively keeps.
            if not _lock(marker):
                raise OSError("Cannot acquire export scratch lock")
            try:
                marker.write(json.dumps({"version": 1, "directory": attempt.name,
                    "host": socket.gethostname()}).encode("utf-8"))
                marker.flush()
                yield attempt
            finally:
                _unlock(marker)
    finally:
        try:
            _remove(root, attempt)
        except OSError:
            logger.warning("Export scratch cleanup deferred")


def clean_export_attempts(settings, *, after=None, limit=100):
    """Bounded local scan; a returned cursor continues on the next worker tick."""
    root = _root(settings)
    if not root.is_dir():
        return None
    names = sorted(path.name for path in root.iterdir()
        if _NAME.fullmatch(path.name) and (after is None or path.name > after))[:limit]
    for name in names:
        attempt = root / name
        marker_path = attempt / _MARKER
        try:
            if _linked(attempt) or _linked(marker_path) or not marker_path.is_file() or marker_path.stat().st_size > 2048:
                continue
            with marker_path.open("r+b") as marker:
                if not _lock(marker):
                    continue
                try:
                    value = json.loads(marker.read(2049))
                    if value != {"version": 1, "directory": name, "host": socket.gethostname()}:
                        continue
                finally:
                    _unlock(marker)
            _remove(root, attempt)
        except (OSError, ValueError, TypeError):
            # Unknown markers and busy files are retained. No artifact bindings
            # or public receipts depend on successful local scratch deletion.
            continue
    return names[-1] if len(names) == limit else None
