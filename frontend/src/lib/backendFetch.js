import { cookies, headers as requestHeaders } from "next/headers";
import { serializeCookies } from "./serializeCookies.mjs";
import { requestIdFromHeaders, withRequestContext } from "./requestContext.mjs";
import { emitServerEvent } from "./diagnosticServer.mjs";
import { routeTemplate } from "./diagnosticSchema.mjs";

/**
 * Серверный fetch к бэкенду с пробросом авторизационной cookie.
 *
 * Серверные компоненты Next.js не проксируют cookie автоматически — без явного
 * заголовка Cookie запрос к бэкенду уходит анонимным (401 → notFound() → 404).
 * Форвардим весь Cookie-заголовок целиком (не по имени `session`: имя — дефолт
 * Starlette, но не фиксировано навсегда; заодно переносятся любые будущие cookie
 * вроде CSRF/Keycloak refresh).
 *
 * ВАЖНО: значения сериализуем вручную через getAll().value, а НЕ через
 * cookieStore.toString() — toString() в Next.js 16 URL-кодирует значения
 * (encodeURIComponent), что ломает signed-cookie Starlette (base64 `=`, `+`, `/`
 * превращаются в %3D/%2B/%2F, бэкенд не декодирует → 401).
 */
export async function backendFetch(path, options = {}) {
  const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:18000";
  const cookieStore = await cookies();
  const cookieString = serializeCookies(cookieStore.getAll());
  const headers = new Headers(options.headers);
  if (cookieString) {
    headers.set("Cookie", cookieString);
  }
  const requestId = requestIdFromHeaders(await requestHeaders());
  headers.set("x-request-id", requestId);
  return withRequestContext({ requestId }, async () => {
    const started = performance.now();
    try {
      const response = await fetch(`${backendUrl}${path}`, { ...options, headers, cache: "no-store" });
      emitServerEvent("request_finished", { route_template: routeTemplate(path), http_status: response.status,
        duration_ms: Math.max(0, performance.now() - started) });
      return response;
    } catch (error) {
      if (!options.signal?.aborted) emitServerEvent("proxy_failed", { route_template: routeTemplate(path),
        duration_ms: Math.max(0, performance.now() - started), error_code: "network_error" }, error);
      throw error;
    }
  });
}
