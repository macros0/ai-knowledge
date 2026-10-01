export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { initializeServerDiagnostics } = await import("./lib/diagnosticServer.mjs");
    await initializeServerDiagnostics();
  }
}

export async function onRequestError(error, request, context) {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const [{ emitServerEvent }, { routeTemplate }, { requestIdFromHeaders, withRequestContext }] = await Promise.all([
    import("./lib/diagnosticServer.mjs"), import("./lib/diagnosticSchema.mjs"), import("./lib/requestContext.mjs"),
  ]);
  const requestId = requestIdFromHeaders(new Headers(request.headers));
  await withRequestContext({ requestId }, () => emitServerEvent("render_failed", {
    route_template: routeTemplate(context.routePath), error_code: "render_failed",
    ...(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].includes(request.method) ? { http_method: request.method } : {}),
  }, error));
}
