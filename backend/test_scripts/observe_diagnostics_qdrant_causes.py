"""Temporary disposable-stand observer; never serialize exception messages."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import runpy
import sys
import threading
import time


def cause_chain(exception):
    rows, seen = [], set()
    while exception is not None and id(exception) not in seen and len(rows) < 8:
        seen.add(id(exception))
        cls = type(exception)
        trusted = cls.__module__.startswith(("httpx", "httpcore", "qdrant_client", "grpc", "app.services.errors")) or cls.__module__ == "builtins"
        name = cls.__name__ if trusted and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", cls.__name__) else "other_exception"
        row = {"exception_type": name}
        status = getattr(exception, "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            row["http_status"] = status
        rows.append(row)
        exception = exception.__cause__ or exception.__context__
    return rows


def install(target):
    from app.services import vector_store
    from app.services.diagnostics.context import current_context
    original = vector_store._qdrant_call
    lock = threading.Lock()

    def observed(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        except Exception as exc:
            context = current_context()
            row = {"at_utc": datetime.now(timezone.utc).isoformat(),
                   "at_monotonic": time.perf_counter(),
                   "duration_ms": (time.perf_counter() - started) * 1000,
                   "request_id": context.request_id, "operation_id": context.operation_id,
                   "cause_chain": cause_chain(exc)}
            # The observer must preserve the application's original failure.
            try:
                with lock, Path(target).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row) + "\n")
            except OSError:
                pass
            raise

    vector_store._qdrant_call = observed


if __name__ == "__main__":
    target = sys.argv.pop(1)
    install(target)
    runpy.run_module("app.diagnostic_entrypoint", run_name="__main__")
