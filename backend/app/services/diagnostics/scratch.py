"""Owned local instance scratch. No database or application runtime in children."""
from contextlib import ExitStack, contextmanager
import heapq
import json
import math
import os
import shutil
import socket
import threading

from .paths import assert_no_links, canonical_root
from .sanitize import valid_uuid

MARKER = ".instance-v1.json"


def marker_value(identity, *, state="active"):
    return {"version": 1, "instance_id": identity, "host": socket.gethostname(), "state": state}


def _marker(root):
    path = root / MARKER
    assert_no_links(path, root)
    if not path.is_file() or path.stat().st_size > 2048:
        raise ValueError("Unknown diagnostic instance")
    value = json.loads(path.read_bytes())
    if value not in (marker_value(root.name), marker_value(root.name, state="retiring")):
        raise ValueError("Unknown diagnostic instance")
    return value


@contextmanager
def _lock(path, *, create=False):
    assert_no_links(path, path.parent)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    handle = os.fdopen(fd, "r+b", buffering=0)
    locked = False
    try:
        if os.fstat(fd).st_size == 0:
            handle.write(b"0")
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            pass
        yield locked
    finally:
        handle.close()  # The OS releases ownership even after process death.


@contextmanager
def child_lease(root, *, timeout_seconds=300):
    root = canonical_root(root)
    if not (root / MARKER).exists():
        yield  # Legacy subprocesses retain their existing filesystem contract.
        return
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 1 <= timeout_seconds <= 600):
        raise ValueError("Invalid diagnostic child lifetime")
    with _lock(root / ".child.lock", create=True) as locked:
        if not locked or _marker(root)["state"] != "active":
            raise ValueError("Diagnostic child scratch unavailable")
        timer = threading.Timer(timeout_seconds, lambda: os._exit(2))
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()


@contextmanager
def parent_operation(root):
    root = canonical_root(root)
    if not (root / MARKER).exists():
        yield True
        return
    with _lock(root / ".operation.lock", create=True) as locked:
        yield locked and _marker(root)["state"] == "active"


def _remove(base, root):
    absolute_base, absolute_root = base.resolve(), root.resolve()
    if (valid_uuid(root.name) is None or absolute_root.parent != absolute_base
            or absolute_root == absolute_base):
        return
    assert_no_links(root, base)
    pending, visited = [absolute_root], 0
    while pending:
        for path in pending.pop().iterdir():
            visited += 1
            if visited > 65536:
                return
            assert_no_links(path, absolute_root)
            if not path.resolve().is_relative_to(absolute_root):
                return
            if path.is_dir():
                pending.append(path)
    shutil.rmtree(absolute_root)


def clean_instances(settings, *, after=None, limit=100):
    if type(limit) is not int or not 1 <= limit <= 100 or after is not None and valid_uuid(after) is None:
        raise ValueError("Invalid diagnostic scratch cursor")
    base = canonical_root(settings.diagnostics_dir) / "instances"
    assert_no_links(base, base.parent)
    if not base.is_dir():
        return None
    names = heapq.nsmallest(limit + 1, (path.name for path in base.iterdir()
        if valid_uuid(path.name) is not None and (after is None or path.name > after)))
    for name in names[:limit]:
        root = base / name
        try:
            assert_no_links(root, base)
            _marker(root)
            with ExitStack() as locks:
                writer_free = locks.enter_context(_lock(root / ".writer.lock"))
                if not writer_free:
                    continue
                child_free = locks.enter_context(_lock(root / ".child.lock", create=True))
                operation_free = locks.enter_context(_lock(root / ".operation.lock", create=True))
                if not child_free or not operation_free:
                    continue
                _marker(root)
                # Late children/parents recheck this under their lock. Closing
                # handles before removal is required on Windows.
                with (root / MARKER).open("wb") as marker:
                    marker.write(json.dumps(marker_value(name, state="retiring")).encode())
                    marker.flush()
                    os.fsync(marker.fileno())
            _remove(base, root)
        except (OSError, ValueError, TypeError):
            continue  # Busy, foreign, linked and unrecognised trees are retained.
    return names[limit - 1] if len(names) > limit else None
