import { randomUUID } from "node:crypto";
import { withRequestContext } from "../../../lib/requestContext.mjs";
import { emitServerEvent } from "../../../lib/diagnosticServer.mjs";
import { routeTemplate } from "../../../lib/diagnosticSchema.mjs";

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);
const CLIENT_CONTROLLED_FORWARDING = [
  "forwarded",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-port",
  "x-forwarded-proto",
];

// Cookie forwarding relies on Node/undici's Headers.getSetCookie(), which is
// not available in every Edge runtime implementation.
export const runtime = "nodejs";

function backendTarget(request, path) {
  const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:18000";
  const target = new URL(`/api/${path.join("/")}`, backendUrl);
  target.search = new URL(request.url).search;
  return target;
}

async function proxy(request, context) {
  const requestId = randomUUID();
  return withRequestContext({ requestId }, () => proxyBound(request, context, requestId));
}

async function proxyBound(request, context, requestId) {
  const { path } = await context.params;
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  for (const name of CLIENT_CONTROLLED_FORWARDING) headers.delete(name);
  headers.set("x-request-id", requestId);

  const init = {
    method: request.method,
    headers,
    redirect: "manual",
    cache: "no-store",
    signal: request.signal,
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    // Keep uploads streaming; buffering request.arrayBuffer() here would
    // duplicate up to the configured upload limit in the Next.js heap before
    // the backend can enforce its own streaming guard.
    init.body = request.body;
    init.duplex = "half";
  }

  const started = performance.now();
  const safeRoute = routeTemplate(`/api/${path.join("/")}`);
  const method = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].includes(request.method) ? { http_method: request.method } : {};
  let upstream;
  try {
    upstream = await fetch(backendTarget(request, path), init);
  } catch (error) {
    const aborted = request.signal.aborted;
    const timedOut = !aborted && (error?.name === "TimeoutError" || error?.cause?.code === "UND_ERR_CONNECT_TIMEOUT");
    const status = aborted ? 499 : timedOut ? 504 : 502;
    if (!aborted) emitServerEvent("proxy_failed", { route_template: safeRoute, ...method,
      http_status: status, duration_ms: Math.max(0, performance.now() - started), error_code: "network_error" }, error);
    return Response.json({ detail: aborted ? "Request cancelled" : "Service temporarily unavailable",
      code: "network_error", request_id: requestId }, { status,
      headers: { "x-request-id": requestId, "cache-control": "no-store", "x-content-type-options": "nosniff" } });
  }
  emitServerEvent("request_finished", { route_template: safeRoute, ...method,
    http_status: upstream.status, duration_ms: Math.max(0, performance.now() - started) });
  const responseHeaders = new Headers();
  upstream.headers.forEach((value, name) => {
    if (name !== "set-cookie" && !HOP_BY_HOP.has(name)) {
      responseHeaders.set(name, value);
    }
  });

  // `Headers.forEach()` combines repeated headers. `getSetCookie()` is the
  // Fetch API escape hatch that preserves every upstream cookie (including
  // session and csrf_token) as a separate response header.
  const setCookies = upstream.headers.getSetCookie?.() || [];
  for (const cookie of setCookies) {
    responseHeaders.append("set-cookie", cookie);
  }

  // API responses can contain authenticated documents, search context, or
  // identity data. Do not allow a browser/shared intermediary to retain them
  // after the request, regardless of an upstream default.
  responseHeaders.set("cache-control", "no-store");
  responseHeaders.set("x-request-id", requestId);

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const HEAD = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const OPTIONS = proxy;
