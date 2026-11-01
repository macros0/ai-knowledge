"""Dedicated worker command; never imports the public HTTP application."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import signal
import threading

from app.config import get_settings
from app.services.work.runtime import start_runtime


class WorkerHealthServer:
    def __init__(self, health, *, host="0.0.0.0", port=8001):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path == "/health/live":
                    ok = health.live()
                elif self.path == "/health/ready":
                    ok = health.ready()
                else:
                    self.send_error(404)
                    return
                body = json.dumps({"status": "ok" if ok else "unavailable"}).encode()
                self.send_response(200 if ok else 503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.1},
            name="worker-health-http", daemon=True)

    @property
    def address(self):
        return self._server.server_address

    def start(self):
        self._thread.start()

    def stop(self, timeout: float) -> bool:
        if self._thread.is_alive():
            self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout)
        return not self._thread.is_alive()


def main(argv=None, *, settings=None, stop_event=None):
    settings = settings or get_settings()
    if settings.app_role != "worker" or settings.okf_deployment_schema != "replicas-v1":
        raise ValueError("The command requires the replicas-v1 worker role")
    parser = argparse.ArgumentParser(description="Durable background worker with an internal health port")
    parser.add_argument("--health-host", default="0.0.0.0")
    parser.add_argument("--health-port", type=int, default=8001)
    parser.add_argument("--shutdown-timeout", type=float, default=60)
    args = parser.parse_args(argv)
    if not 0 <= args.health_port <= 65535 or not 0 <= args.shutdown_timeout <= 600:
        parser.error("Invalid bounded worker health/shutdown configuration")
    stop = stop_event or threading.Event()
    previous = {}
    if stop_event is None:
        def request_stop(_signal, _frame):
            stop.set()
        for name in (signal.SIGTERM, signal.SIGINT):
            previous[name] = signal.getsignal(name)
            signal.signal(name, request_stop)
    handle, server, diagnostic_runtime, finished = None, None, None, False
    try:
        from app.services.audit_runtime import validate_existing_audit, preflight_audit
        from app.services.diagnostics.runtime import install_entrypoint_runtime
        validate_existing_audit()
        preflight_audit(settings)
        diagnostic_runtime = install_entrypoint_runtime(settings)
        if not diagnostic_runtime.start() and settings.diagnostics_bundle_enabled:
            raise RuntimeError("Worker diagnostic storage unavailable")
        handle = start_runtime(settings, stop_event=stop)
        server = WorkerHealthServer(handle.health, host=args.health_host, port=args.health_port)
        server.start()
        while not stop.wait(0.25):
            pass
    finally:
        if handle is not None:
            finished = handle.stop(timeout=args.shutdown_timeout)
        if server is not None:
            server.stop(timeout=1)
        if diagnostic_runtime is not None:
            diagnostic_runtime.stop()
        for name, handler in previous.items():
            signal.signal(name, handler)
    return 0 if finished else 1


if __name__ == "__main__":
    raise SystemExit(main())
