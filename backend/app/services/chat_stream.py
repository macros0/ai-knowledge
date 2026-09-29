"""NDJSON transport: provisional deltas, one authoritative result, safe errors."""
import asyncio
import json
import logging
import queue
import threading
import time

from app.config import get_settings
from app.api.errors import ApiError
from app.services.errors import public_error_code
from app.services.llm_profiles import request_scope
from app.services.llm_scheduler import LLMCancelled
from app.services import chat_history

logger = logging.getLogger(__name__)


async def stream_chat(work, *, independent_calls: bool = False, current_user=None):
    events = queue.Queue()
    cancel = threading.Event()
    attempt_ref = None

    def emit_start(ref):
        nonlocal attempt_ref
        attempt_ref = ref
        events.put({"type": "start", "session_id": ref.session_id,
                    "assistant_message_id": ref.message_id, "attempt_id": ref.attempt_id})
    def emit(text):
        if cancel.is_set():
            raise LLMCancelled()
        events.put({"type": "delta", "text": text})

    def emit_sources(sources):
        if cancel.is_set():
            raise LLMCancelled()
        events.put({"type": "sources", "sources": sources})

    def emit_progress(progress):
        if not cancel.is_set():
            events.put({"type": "progress", **progress})

    def run():
        try:
            settings = get_settings()
            with request_scope(on_text=emit, on_sources=emit_sources, on_start=emit_start,
                               on_progress=emit_progress, cancel=cancel,
                               deadline=None if independent_calls else time.monotonic() + settings.llm_chat_total_timeout_seconds):
                result = work()
            if not cancel.is_set():
                events.put({"type": "result", "data": result.model_dump(mode="json")})
        except LLMCancelled:
            pass
        except Exception as exc:
            # Neither provider response bodies nor exception text cross the UI boundary.
            events.put({"type": "error", "code": exc.code if isinstance(exc, ApiError) else public_error_code(exc),
                        "status": exc.status_code if isinstance(exc, ApiError) else 503})
            logger.warning("Streaming chat failed (%s)", type(exc).__name__)

    worker = asyncio.create_task(asyncio.to_thread(run))
    try:
        ping_at = time.monotonic() + 5
        status_at = time.monotonic() + 1
        event_seq = 0
        while True:
            if attempt_ref is not None and current_user is not None and time.monotonic() >= status_at:
                status_at = time.monotonic() + 1
                try:
                    if chat_history.attempt_status(attempt_ref, current_user) in {"stopped", "failed"}:
                        cancel.set()
                except (chat_history.ChatOwnershipError, chat_history.ChatSessionDeletedError):
                    cancel.set()
            try:
                event = events.get_nowait()
            except queue.Empty:
                if worker.done():
                    break
                if time.monotonic() >= ping_at:
                    yield '{"type":"ping"}\n'
                    ping_at = time.monotonic() + 5
                await asyncio.sleep(.05)
                continue
            event_seq += 1
            if attempt_ref is not None:
                event.setdefault("session_id", attempt_ref.session_id)
                event.setdefault("assistant_message_id", attempt_ref.message_id)
                event.setdefault("attempt_id", attempt_ref.attempt_id)
                event["event_seq"] = event_seq
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] in ("result", "error"):
                break
    finally:
        cancel.set()
        # Cancelling the awaiter cannot kill its OS thread. The worker's LLM
        # request owns and releases the GPU slot after its stream really closes.
        if not worker.done():
            worker.cancel()
