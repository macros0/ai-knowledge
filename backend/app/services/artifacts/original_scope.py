"""Private original files live through one complete request or work attempt."""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path


@dataclass
class OriginalScope:
    stack: ExitStack
    paths: dict[tuple, Path] = field(default_factory=dict)


_active = ContextVar("original_scratch_scope", default=None)


def current_original_scope():
    return _active.get()


@contextmanager
def original_scope():
    if _active.get() is not None:
        yield _active.get()
        return
    with ExitStack() as stack:
        scope = OriginalScope(stack)
        token = _active.set(scope)
        try:
            yield scope
        finally:
            _active.reset(token)


def scoped_originals(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with original_scope():
            return function(*args, **kwargs)
    return wrapped


class OriginalScratchMiddleware:
    """Keep files alive until streaming responses have actually finished."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        with original_scope():
            await self.app(scope, receive, send)
