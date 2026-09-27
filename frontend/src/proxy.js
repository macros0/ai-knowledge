import { NextResponse } from "next/server";
import { buildContentSecurityPolicy, createNonce } from "./lib/contentSecurityPolicy.mjs";

// Next.js 16 Proxy (бывший middleware): nonce-CSP для каждой страницы.
// Nonce уходит и в заголовок запроса — по нему Next.js проставляет nonce своим
// скриптам при рендере, а layout.js читает x-nonce для инлайн-скриптов.
// Страницы и так динамические (layout читает cookies/headers).
export function proxy(request) {
  const nonce = createNonce();
  const policy = buildContentSecurityPolicy(nonce, { dev: process.env.NODE_ENV === "development" });

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", policy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", policy);
  return response;
}

export const config = {
  matcher: [
    {
      // API (/api/*) проксируется к бэкенду со своей CSP; статика и /health
      // не являются документами.
      source: "/((?!api|health|_next/static|_next/image|favicon.ico|icon.png).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
