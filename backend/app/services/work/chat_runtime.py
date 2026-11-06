"""Request-local lease/cancellation heartbeat; never starts an API job executor."""
from contextlib import contextmanager
from datetime import timedelta
import logging
import threading
from uuid import uuid4

from app import error_codes as codes
from app.api.errors import ApiError
from app.db.session import session_scope
from app.services import chat_history
from app.services.llm_profiles import request_state, request_scope
from app.services.llm_scheduler import LLMCancelled
from .chat import (admit_chat_run, renew_chat_run, finish_chat_run, _locked,
    ChatRunOwnershipLost, ChatRunConflict, ChatCapacityExceeded)
from .store import _utc
from .coordination import reserve_capacity, renew_capacity, release_capacity, CoordinationOwnershipLost

logger = logging.getLogger(__name__)


class RequestCancellation:
    def __init__(self, parent):
        self.parent = parent
        self.local = threading.Event()

    def is_set(self):
        return self.local.is_set() or (self.parent is not None and self.parent.is_set())

    def set(self):
        self.local.set()

    def wait(self, timeout=None):
        self.local.wait(timeout)
        return self.is_set()


class ChatRequestRuntime:
    def __init__(self, claim, *, parent_cancel=None):
        self.claim = claim
        self.cancel = RequestCancellation(parent_cancel)
        self.stop = threading.Event()
        self.thread = None

    def tick(self):
        if self.cancel.is_set():
            return
        try:
            with session_scope() as session:
                row, now = _locked(session, self.claim)
                if _utc(row.lease_until) <= now + timedelta(seconds=20):
                    self.claim = renew_chat_run(session, self.claim)
        except Exception as exc:
            self.cancel.set()
            logger.info("Chat request ownership unavailable (%s)", type(exc).__name__)

    def _run(self):
        while not self.stop.wait(1):
            self.tick()
            if self.cancel.is_set():
                return

    def start(self):
        self.thread = threading.Thread(target=self._run, name="chat-request-heartbeat", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None and self.thread.ident is not None:
            self.thread.join(2)
            if self.thread.is_alive():
                logger.warning("Chat request heartbeat shutdown deferred")


class PreviewChatRuntime(ChatRequestRuntime):
    def __init__(self, lease, *, parent_cancel=None):
        super().__init__(None, parent_cancel=parent_cancel)
        self.lease = lease

    def tick(self):
        if self.cancel.is_set():
            return
        try:
            with session_scope() as session:
                self.lease = renew_capacity(session, self.lease, ttl_seconds=30)
        except Exception as exc:
            self.cancel.set()
            logger.info("Preview chat capacity unavailable (%s)", type(exc).__name__)

    def _run(self):
        while not self.stop.wait(10):
            self.tick()
            if self.cancel.is_set():
                return


@contextmanager
def preview_chat_capacity(settings):
    with session_scope() as session:
        lease = reserve_capacity(session, resource="chat", owner_id="preview-" + uuid4().hex,
            limit=settings.chat_max_inflight, ttl_seconds=30)
    if lease is None:
        raise ApiError(429, codes.RATE_LIMITED, "Сервис ответов перегружен. Повторите попытку позже.",
            headers={"Retry-After": "5"})
    runtime = PreviewChatRuntime(lease, parent_cancel=request_state().get("cancel"))
    succeeded = False
    try:
        runtime.tick()
        if runtime.cancel.is_set():
            raise LLMCancelled()
        runtime.start()
        with request_scope(cancel=runtime.cancel, chat_capacity=lease):
            yield
            if runtime.cancel.is_set():
                raise LLMCancelled()
            succeeded = True
    finally:
        runtime.close()
        # A still-running renewal owns its outcome; let the finite generic
        # lease expire instead of racing it with an uncertain release token.
        if runtime.thread is None or not runtime.thread.is_alive():
            try:
                with session_scope() as session:
                    release_capacity(session, runtime.lease)
            except CoordinationOwnershipLost:
                runtime.cancel.set()
                if succeeded:
                    raise LLMCancelled() from None
            except Exception as exc:
                logger.warning("Preview chat capacity release deferred (%s)", type(exc).__name__)
        elif succeeded:
            raise LLMCancelled()


@contextmanager
def managed_chat(settings, request, user):
    try:
        with session_scope() as session:
            claim = admit_chat_run(session, settings, request, user, owner_id=uuid4().hex)
    except ChatCapacityExceeded as exc:
        raise ApiError(429, codes.RATE_LIMITED, "Сервис ответов перегружен. Повторите попытку позже.",
            headers={"Retry-After": "5"}) from exc
    except ChatRunConflict as exc:
        raise ApiError(409, codes.CONFLICT, "Эта попытка уже принята") from exc
    except ValueError as exc:
        raise ApiError(422, codes.INVALID_REQUEST, "Некорректная попытка ответа") from exc
    except chat_history.ChatOwnershipError as exc:
        raise ApiError(403, codes.FORBIDDEN, "Сессия принадлежит другому пользователю") from exc
    except chat_history.ChatSessionDeletedError as exc:
        raise ApiError(409, codes.CONFLICT, "Сессия удалена") from exc
    runtime = ChatRequestRuntime(claim, parent_cancel=request_state().get("cancel"))
    try:
        runtime.tick()
        if runtime.cancel.is_set():
            raise LLMCancelled()
        runtime.start()
        with request_scope(cancel=runtime.cancel, chat_claim=claim):
            yield claim.ref
    except BaseException as exc:
        try:
            with session_scope() as session:
                finish_chat_run(session, claim, user,
                    status="stopped" if runtime.cancel.is_set() or isinstance(exc, LLMCancelled) else "failed", answer="")
        except ChatRunOwnershipLost:
            pass
        except Exception as cleanup_error:
            logger.warning("Chat request completion deferred (%s)", type(cleanup_error).__name__)
        if isinstance(exc, ChatRunOwnershipLost) or runtime.cancel.is_set():
            raise LLMCancelled() from None
        raise
    finally:
        runtime.close()
