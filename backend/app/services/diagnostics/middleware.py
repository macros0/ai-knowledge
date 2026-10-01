"""Request correlation without reading, buffering or replacing body streams."""
import time
from uuid import uuid4

from starlette.responses import JSONResponse

from .context import bind_context, canonical_request_id
from .recorder import begin_trace, emit_event
from .schema import DiagnosticContext, ROUTE_TEMPLATES


_PARAMETER_ROUTES = tuple((template, template.split("/")) for template in sorted(ROUTE_TEMPLATES)
                          if "{" in template)


def _route_template(scope):
    route = getattr(scope.get("route"), "path", None)
    if route in ROUTE_TEMPLATES:
        return route
    # An inner router may resolve a copied ASGI scope. Infer only a template
    # from the existing allowlist; never return a raw path or parameter value.
    path = scope.get("path", "")
    if path in ROUTE_TEMPLATES:
        return path
    parts = path.split("/")
    for template, pattern in _PARAMETER_ROUTES:
        if len(parts) == len(pattern) and all(
                actual == expected or (expected.startswith("{") and expected.endswith("}") and actual)
                for actual, expected in zip(parts, pattern)):
            return template
    return "/unknown"


class DiagnosticContextMiddleware:
    def __init__(self, app, *, api_prefix="/api"):
        self.app = app
        self.search_routes = frozenset({f"{api_prefix}/chat", f"{api_prefix}/chat/stream", f"{api_prefix}/search"})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        candidates = [value for key, value in scope.get("headers", ()) if key.lower() == b"x-request-id"]
        incoming = candidates[0].decode("ascii", errors="replace") if len(candidates) == 1 else None
        request_id = canonical_request_id(incoming) or str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        kind = "search_chat" if scope.get("path") in self.search_routes else "interface"
        context = DiagnosticContext(request_id=request_id, operation_kind=kind)
        started = time.monotonic()
        status = None
        failure = None

        async def send_correlated(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [(key, value) for key, value in message.get("headers", ())
                           if key.lower() != b"x-request-id"]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        with bind_context(context):
            if scope.get("path") not in {"/health", "/health/ready", "/api/admin/diagnostics/status"}:
                begin_trace(context)
            try:
                await self.app(scope, receive, send_correlated)
            except Exception as exc:
                failure = exc
                if status is not None:
                    raise
                # Handles failures before the inner application's error handlers.
                await JSONResponse(status_code=500, content={
                    "detail": "Внутренняя ошибка. Обратитесь в техническую поддержку.",
                    "code": "internal_error", "request_id": request_id,
                })(scope, receive, send_correlated)
            finally:
                fields = {"route_template": _route_template(scope),
                          "duration_ms": max(0, (time.monotonic() - started) * 1000)}
                method = scope.get("method")
                if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
                    fields["http_method"] = method
                if status is not None:
                    fields["http_status"] = status
                emit_event("request_finished", context=context, exception=failure, fields=fields)
