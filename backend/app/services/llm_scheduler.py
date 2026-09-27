"""Single-process admission to the local GPU; running calls are never preempted."""
import threading
import time


class LLMCancelled(Exception):
    """The caller has disconnected or cancelled the request."""


class PriorityScheduler:
    def __init__(self):
        self._condition = threading.Condition()
        self._queue = []
        self._busy = False

    @property
    def waiting(self):
        with self._condition:
            return len(self._queue)

    def acquire(self, interactive: bool, timeout: float, cancel=None):
        ticket = (0 if interactive else 1, object())
        deadline = time.monotonic() + timeout
        with self._condition:
            self._queue.append(ticket)
            try:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise LLMCancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Local LLM queue wait exceeded")
                    first = min(self._queue, key=lambda item: item[0])
                    if not self._busy and first is ticket:
                        self._busy = True
                        break
                    self._condition.wait(min(.1, max(0, deadline - time.monotonic())))
            finally:
                self._queue.remove(ticket)
                self._condition.notify_all()
        released = False

        def release():
            nonlocal released
            with self._condition:
                if not released:
                    released = True
                    self._busy = False
                    self._condition.notify_all()
        return release


local_scheduler = PriorityScheduler()
