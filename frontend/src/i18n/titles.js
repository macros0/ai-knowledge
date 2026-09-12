// Карта pathname → ключ заголовка страницы. Используется и на сервере (для
// SSR-метаданных), и на клиенте (мгновенное обновление document.title при
// смене языка без перезагрузки). Node-тестируемый чистый модуль.
//
// Порядок важен: более специфичные пути идут раньше общих (prefix-маппинг).

import locales from "./locales/index.js";

export function lookupTitleKey(pathname) {
  const p = pathname || "/";
  if (p === "/") return "titles.documents";
  if (p.startsWith("/chat/history/admin")) return "titles.historyAdmin";
  if (p.startsWith("/chat/history")) return "titles.history";
  if (p.startsWith("/chat")) return "titles.chat";
  if (p.startsWith("/developments/")) return "titles.developmentCard";
  if (p.startsWith("/developments")) return "titles.developments";
  if (p.startsWith("/documents/")) return "titles.document";
  if (p.startsWith("/admin/languages")) return "titles.adminLanguages";
  if (p.startsWith("/admin/glossary")) return "titles.adminGlossary";
  if (p.startsWith("/admin")) return "titles.admin";
  if (p.startsWith("/security")) return "titles.security";
  return null;
}

export function titleFor(pathname, locale) {
  const key = lookupTitleKey(pathname);
  if (!key) return locales[locale]?.messages?.[key] ?? null;
  return locales[locale]?.messages?.[key] ?? null;
}

export function makeTitle(segment, locale) {
  const base = titleFor(segment, locale);
  const app = locales[locale]?.messages?.["app.title"] || "OKF Knowledge Service";
  return base ? `${base} — ${app}` : app;
}
