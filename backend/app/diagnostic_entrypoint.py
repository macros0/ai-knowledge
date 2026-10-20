"""Install the safe recorder before importing the FastAPI application."""
import argparse
import importlib
import logging
from uvicorn.logging import AccessFormatter
import os
import sys
import traceback

_OMITTED_ACCESS_RECORD = "HTTP access record omitted: unsupported format"


class QueryFreeAccessFilter(logging.Filter):
    """Remove request parameters before any Uvicorn access handler sees them."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        known = (
            record.msg == '%s - "%s %s HTTP/%s" %d'
            and type(args) is tuple
            and len(args) == 5
            and all(type(value) is str for value in args[:4])
            and type(args[4]) is int
            and record.exc_info is None
            and record.exc_text is None
            and record.stack_info is None
        )
        if known:
            path = args[2].partition("?")[0].partition("#")[0]
            record.args = (args[0], args[1], path, args[3], args[4])
        else:
            record.msg = _OMITTED_ACCESS_RECORD
            record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class QueryFreeAccessFormatter(AccessFormatter):
    """Handle content-free fallback records without five request arguments."""

    def formatMessage(self, record: logging.LogRecord) -> str:
        if record.msg == _OMITTED_ACCESS_RECORD and record.args == ():
            return _OMITTED_ACCESS_RECORD
        return super().formatMessage(record)


def main(argv=None, *, settings=None, runner=None) -> int:
    # Windows spawn reimports this module before worker resource admission.
    # Provider/diagnostics imports belong only to the actual server process.
    from app.services.diagnostics import runtime as diagnostics_runtime

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("UVICORN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workers", type=int, default=int(os.getenv("UVICORN_WORKERS", "1")))
    args = parser.parse_args(argv)
    runtime = diagnostics_runtime.install_entrypoint_runtime(settings)
    try:
        if args.workers != 1:
            raise ValueError("Diagnostics require a single backend worker")
        importlib.import_module("app.main")
        if runner is None:
            import uvicorn
            runner = uvicorn.run
        from app.config import get_settings
        from copy import deepcopy
        from uvicorn.config import LOGGING_CONFIG
        log_config = deepcopy(LOGGING_CONFIG)
        log_config["filters"] = {"query_free_access": {"()": QueryFreeAccessFilter}}
        log_config["loggers"]["uvicorn.access"]["filters"] = ["query_free_access"]
        log_config["formatters"]["access"]["()"] = QueryFreeAccessFormatter
        if getattr(settings or get_settings(), "audit_stdout_mode", "disabled") == "durable":
            log_config["handlers"]["access"]["stream"] = "ext://sys.stderr"
        runner("app.main:app", host=args.host, port=args.port, workers=args.workers, log_config=log_config)
        return 0
    except Exception as exc:
        if runtime.available:
            runtime.recorder.emit("server_start_failed", exception=exc)
        # The journal above stays content-free. The container log still has to
        # say why the process exited; print directly instead of logging so the
        # recorder's log handler does not record a duplicate event.
        traceback.print_exception(exc, file=sys.stderr)
        return 1
    finally:
        runtime.stop()
        diagnostics_runtime._entrypoint_runtime = None


if __name__ == "__main__":
    raise SystemExit(main())
