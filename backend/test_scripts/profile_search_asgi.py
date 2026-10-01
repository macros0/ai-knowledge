"""Test-only numeric profiler; launch only on the disposable synthetic stack."""
from contextvars import ContextVar
from functools import wraps
import json
import time

PROFILE = ContextVar('diag_numeric_profile', default=None)


def timed(name, function):
    @wraps(function)
    def measured(*args, **kwargs):
        profile = PROFILE.get()
        if profile is None:
            return function(*args, **kwargs)
        started = time.perf_counter_ns()
        try:
            return function(*args, **kwargs)
        finally:
            profile[name] = profile.get(name, 0.0) + (time.perf_counter_ns() - started) / 1_000_000
    return measured


class ProfileApplication:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('path') != '/api/search':
            return await self.app(scope, receive, send)
        profile = dict.fromkeys(('qdrant_ms', 'hydration_ms', 'merge_ms', 'connection_ms', 'commit_ms', 'sql_ms', 'sql_count'), 0)
        token = PROFILE.set(profile)
        started = time.perf_counter_ns()

        async def measured_send(message):
            if message['type'] == 'http.response.start':
                values = dict(profile)
                values['backend_ms'] = (time.perf_counter_ns() - started) / 1_000_000
                header = json.dumps(values, separators=(',', ':'), allow_nan=False).encode('ascii')
                message = {**message, 'headers': [*message.get('headers', []), (b'x-diag-numeric-profile', header)]}
            await send(message)
        try:
            await self.app(scope, receive, measured_send)
        finally:
            PROFILE.reset(token)


def install_probes():
    from sqlalchemy import event
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    from app.api import search
    from app.services.vector_store import VectorStore

    search.load_visible_retrieval_hits = timed('hydration_ms', search.load_visible_retrieval_hits)
    search.merge_and_format = timed('merge_ms', search.merge_and_format)
    VectorStore.search_composite = timed('qdrant_ms', VectorStore.search_composite)
    Session.connection = timed('connection_ms', Session.connection)
    Session.commit = timed('commit_ms', Session.commit)

    def before_cursor(connection, cursor, statement, parameters, context, executemany):
        if PROFILE.get() is not None:
            context._diag_numeric_start = time.perf_counter_ns()

    def after_cursor(connection, cursor, statement, parameters, context, executemany):
        profile = PROFILE.get()
        started = getattr(context, '_diag_numeric_start', None)
        if profile is not None and started is not None:
            profile['sql_ms'] = profile.get('sql_ms', 0.0) + (time.perf_counter_ns() - started) / 1_000_000
            profile['sql_count'] = profile.get('sql_count', 0) + 1
    event.listen(Engine, 'before_cursor_execute', before_cursor)
    event.listen(Engine, 'after_cursor_execute', after_cursor)


def main():
    import uvicorn
    from app.services.diagnostics import runtime
    installed = runtime.install_entrypoint_runtime()
    try:
        from app.main import app
        install_probes()
        uvicorn.run(ProfileApplication(app), host='0.0.0.0', port=8000, workers=1, access_log=False)
    finally:
        installed.stop()
        runtime._entrypoint_runtime = None


if __name__ == '__main__':
    main()
