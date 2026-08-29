import { cookies } from "next/headers";
import { serializeCookies } from "./serializeCookies.mjs";

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
  const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  const cookieStore = await cookies();
  const cookieString = serializeCookies(cookieStore.getAll());
  const headers = { ...(options.headers || {}) };
  if (cookieString) {
    headers.Cookie = cookieString;
  }
  return fetch(`${backendUrl}${path}`, {
    ...options,
    headers,
    cache: "no-store",
  });
}