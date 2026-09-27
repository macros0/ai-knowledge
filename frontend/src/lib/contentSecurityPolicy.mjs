// Content-Security-Policy страниц фронтенда (API-ответы несут свою политику
// бэкенда `default-src 'none'`). Чистая функция — node-тестируемая; nonce и
// заголовки ставит src/proxy.js на каждый запрос страницы.
//
// script-src: только скрипты с nonce этого ответа и загруженные ими
// ('strict-dynamic'): внедрённый в документ или ответ LLM <script> не
// выполнится. Next.js сам проставляет nonce своим скриптам, прочитав его из
// заголовка запроса; собственные инлайн-скрипты (тема, язык) получают его из
// layout.js.
// style-src 'unsafe-inline': SSR отдаёт style="..." атрибутами, а nonce к
// атрибутам не применяется; риск инлайн-стилей несравним с инлайн-скриптами.
// img-src без внешних источников: картинки документов — только вложения
// с того же origin, внешний <img> в ответе LLM не уведёт данные наружу.
// upgrade-insecure-requests намеренно нет: HTTPS обеспечивает reverse proxy,
// а HSTS ставит бэкенд.

export function buildContentSecurityPolicy(nonce, { dev = false } = {}) {
  if (!nonce) throw new Error("CSP nonce is required");
  const directives = [
    ["default-src", "'self'"],
    ["script-src", "'self'", `'nonce-${nonce}'`, "'strict-dynamic'", ...(dev ? ["'unsafe-eval'"] : [])],
    ["style-src", "'self'", "'unsafe-inline'"],
    ["img-src", "'self'", "blob:", "data:"],
    ["font-src", "'self'", "data:"],
    ["connect-src", "'self'"],
    ["object-src", "'none'"],
    ["base-uri", "'self'"],
    ["form-action", "'self'"],
    ["frame-ancestors", "'none'"],
  ];
  return directives.map((parts) => parts.join(" ")).join("; ");
}

export function createNonce() {
  return Buffer.from(crypto.randomUUID()).toString("base64");
}
