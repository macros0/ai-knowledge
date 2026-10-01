"""Install the safe recorder before importing the FastAPI application."""
import argparse
import importlib
import os

from app.services.diagnostics import runtime as diagnostics_runtime


def main(argv=None, *, settings=None, runner=None) -> int:
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
        runner("app.main:app", host=args.host, port=args.port, workers=args.workers)
        return 0
    except Exception as exc:
        if runtime.available:
            runtime.recorder.emit("server_start_failed", exception=exc)
        return 1
    finally:
        runtime.stop()
        diagnostics_runtime._entrypoint_runtime = None


if __name__ == "__main__":
    raise SystemExit(main())
