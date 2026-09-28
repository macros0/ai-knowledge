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
from app.services.diagnostics.context import bind_context, current_context, exception_recorded
from app.services.diagnostics.recorder import emit_event

logger = logging.getLogger(__name__)


async def stream_chat(work, *, context=None):
    context = context or current_context()
    events = queue.Queue()
    cancel = threading.Event()
    def emit(text):
        if cancel.is_set():
            raise LLMCancelled()
        events.put({"type": "delta", "text": text})

    def run_with_context():
        try:
            settings = get_settings()
            with request_scope(on_text=emit, cancel=cancel,
                               deadline=time.monotonic() + settings.llm_chat_total_timeout_seconds):
                result = work()
            if not cancel.is_set():
                events.put({"type": "result", "data": result.model_dump(mode="json")})
        except LLMCancelled:
            pass
        except Exception as exc:
            # Neither provider response bodies nor exception text cross the UI boundary.
            code = exc.code if isinstance(exc, ApiError) else public_error_code(exc)
            events.put({"type": "error", "code": code,
                        "status": exc.status_code if isinstance(exc, ApiError) else 503,
                        "request_id": context.request_id})
            if not exception_recorded(exc, "operation_failed"):
                emit_event("operation_failed", context=context, exception=exc,
                           fields={"stage": "chat", "error_code": code})
            logger.warning("Streaming chat failed (%s)", type(exc).__name__)

    def run():
        with bind_context(context):
            run_with_context()

    worker = asyncio.create_task(asyncio.to_thread(run))
    try:
        ping_at = time.monotonic() + 5
        while True:
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
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] in ("result", "error"):
                break
    finally:
        cancel.set()
        # Cancelling the awaiter cannot kill its OS thread. The worker's LLM
        # request owns and releases the GPU slot after its stream really closes.
        if not worker.done():
            worker.cancel()
