"""Global completion budget; ambiguous remote execution remains quarantined."""
import logging
import threading
import time
from uuid import uuid4

from app.db.session import session_scope
from .coordination import reserve_capacity, complete_capacity, assert_capacity_live, CoordinationOwnershipLost

logger = logging.getLogger(__name__)


class ModelBudget:
    def __init__(self, lease):
        self.lease = lease
        self._lock = threading.Lock()
        self._closed = False

    def complete(self, *, confirmed):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if not confirmed:
            logger.warning("Model capacity retained pending provider reconciliation")
            return
        try:
            with session_scope() as session:
                complete_capacity(session, self.lease)
        except Exception as exc:
            # Returning a known answer must not be replaced by a cleanup error.
            # Failure leaves the global slot reserved instead of over-admitting.
            logger.warning("Model capacity release deferred (%s)", type(exc).__name__)


def acquire_model_budget(settings, *, max_wait, cancel=None):
    from app.services.llm_client import LLMBusyError, LLMCancelled
    from app.db.session import _current_session
    if _current_session.get() is not None:
        raise ValueError("Model admission must run outside a SQL transaction")
    from app.services.llm_profiles import request_state
    from .chat import _locked as chat_owner, ChatRunOwnershipLost
    state = request_state()
    def check_chat(session):
        if state.get("chat_claim") is not None:
            chat_owner(session, state["chat_claim"])
        if state.get("chat_capacity") is not None:
            lease = state["chat_capacity"]
            if lease.resource != "chat":
                raise ValueError("Invalid preview capacity resource")
            assert_capacity_live(session, lease)
    owner = uuid4().hex
    deadline = time.monotonic() + max(0, max_wait)
    while True:
        if cancel is not None and cancel.is_set():
            raise LLMCancelled()
        try:
            with session_scope() as session:
                # Chat→model lock order. Waiting for model capacity may outlive
                # a request lease even when its heartbeat connection is stuck.
                check_chat(session)
                lease = reserve_capacity(session, resource="llm", owner_id=owner,
                    limit=settings.llm_global_max_concurrency, ttl_seconds=120, release_required=True)
                if lease is not None:
                    check_chat(session)
        except (ChatRunOwnershipLost, CoordinationOwnershipLost):
            if cancel is not None:
                cancel.set()
            raise LLMCancelled() from None
        if lease is not None:
            return ModelBudget(lease)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMBusyError("Global model capacity is occupied or awaiting reconciliation")
        if cancel is not None:
            cancel.wait(min(0.1, remaining))
        else:
            time.sleep(min(0.1, remaining))
