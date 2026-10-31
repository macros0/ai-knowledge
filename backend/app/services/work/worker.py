"""Opt-in worker primitive; no executor is constructed during module import."""
from concurrent.futures import ThreadPoolExecutor, wait
import logging

from app.db.session import session_scope
from .handlers import TransientWorkError
from .models import LostOwnership
from . import store

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, *, registry, owner_id):
        self.settings, self.registry, self.owner_id = settings, registry, owner_id
        self._future = None

    def run_once(self, stop):
        if stop.is_set() or self._future is not None and not self._future.done():
            return False
        with session_scope() as session:
            work = store.claim(session, owner_id=self.owner_id, supported=self.registry.supported,
                               lease_seconds=self.settings.work_lease_seconds)
        if work is None:
            return False
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="durable-work")
        try:
            future = executor.submit(self.registry.resolve(work.spec), work)
            self._future = future
            failure = None
            while True:
                if stop.is_set():
                    return True  # Leave ownership to expire; no late ACK callback.
                completed, _ = wait([future], timeout=self.settings.work_heartbeat_seconds)
                if completed:
                    failure = future.exception()
                    break
                with session_scope() as session:
                    work = store.renew(session, work, lease_seconds=self.settings.work_lease_seconds)
            with session_scope() as session:
                if isinstance(failure, TransientWorkError):
                    row, _ = store._locked(session, work)
                    delays = self.settings.work_retry_backoff_seconds
                    store.retry(session, work, error_code=failure.code, delay_seconds=delays[min(row.attempts - 1, len(delays) - 1)],
                                max_attempts=self.settings.work_max_attempts)
                else:
                    store.finish(session, work, outcome="failed" if failure else "succeeded",
                                 error_code="handler_failed" if failure else None)
            return True
        except LostOwnership:
            logger.info("Work ownership lost; no acknowledgement")
            return True
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def run(self, stop):
        failures = 0
        while not stop.is_set():
            try:
                occupied = self.run_once(stop)
                failures = 0
                delay = self.settings.work_poll_seconds if not occupied else 0
            except Exception as exc:
                failures = min(failures + 1, 5)
                delay = min(2 ** (failures - 1), 30)
                logger.warning("Work poll unavailable (%s)", type(exc).__name__)
            if delay:
                stop.wait(delay)
