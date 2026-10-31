"""Renew a SQL read lease while providers verify, spool and stream bytes."""
from contextlib import contextmanager, suppress
import math
import threading
import time

from app.db.session import session_scope
from .contract import BlobLeaseLost
from .lifecycle import acquire_read_lease, release_read_lease, renew_read_lease, _seconds


class _Lease:
    def __init__(self, ref, ttl, interval):
        self.ref, self.ttl, self.interval = ref, ttl, interval
        self.stop = threading.Event()
        self.lost = threading.Event()
        began = time.monotonic()
        with session_scope() as session:
            self.token = acquire_read_lease(session, ref, ttl_seconds=ttl)
        # Conservative margin includes database timestamp granularity and stalls.
        self.deadline = began + ttl * .8
        self.thread = threading.Thread(target=self._renew, name="artifact-read-lease", daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.close()
            raise

    def _renew(self):
        while not self.stop.wait(self.interval):
            began = time.monotonic()
            try:
                with session_scope() as session:
                    renew_read_lease(session, self.ref.object_id, self.token, ttl_seconds=self.ttl)
                if self.lost.is_set() or time.monotonic() >= self.deadline:
                    self.lost.set()
                    return
                self.deadline = began + self.ttl * .8
            except Exception:
                self.lost.set()
                return

    def check(self):
        if self.lost.is_set() or time.monotonic() >= self.deadline:
            self.lost.set()
            raise BlobLeaseLost("Artifact read lease lost")

    def close(self):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=1)
        # A failed release leaves only an expiring lease; never mask a read error.
        with suppress(Exception):
            with session_scope() as session:
                release_read_lease(session, self.ref.object_id, self.token)


class LeasedBlobStore:
    def __init__(self, store, *, ttl_seconds=60, renew_interval=None):
        self.store = store
        self.store_id = store.store_id
        self.ttl = _seconds(ttl_seconds)
        self.interval = self.ttl / 3 if renew_interval is None else renew_interval
        if (type(self.interval) not in (int, float) or not math.isfinite(self.interval)
                or not 0 < self.interval <= self.ttl / 3):
            raise ValueError("Invalid artifact renewal interval")

    @contextmanager
    def open(self, ref, *, start=0, end=None, guard=None):
        lease = _Lease(ref, self.ttl, self.interval)
        def check():
            lease.check()
            if guard:
                guard()
        try:
            with self.store.open(ref, start=start, end=end, guard=check) as source:
                check()
                yield source
        finally:
            lease.close()

    def stat(self, ref):
        with self.open(ref):
            return ref
