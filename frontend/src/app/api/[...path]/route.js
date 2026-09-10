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
  const { path } = await context.params;
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  for (const name of CLIENT_CONTROLLED_FORWARDING) headers.delete(name);

  const init = {
    method: request.method,
    headers,
    redirect: "manual",
    cache: "no-store",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  const upstream = await fetch(backendTarget(request, path), init);
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
