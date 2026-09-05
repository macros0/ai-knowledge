// Серверный (RSC) доступ к переводам. Используется в force-dynamic страницах
// (okf/fulltext/chunks) и generateMetadata, которые уже рендерятся на каждый
// запрос — чтение cookie и заголовка здесь не меняет динамику маршрута.
//
// В Next.js 16 cookies()/headers() асинхронные (возвращают Promise), поэтому
// возвращаем Promise<translator>.

import { cookies, headers } from "next/headers";
import { createTranslator, resolveServerLocale } from "./core";

export async function serverTranslator() {
  let locale = "ru";
  try {
    const cookieStore = await cookies();
    const headerStore = await headers();
    const cookieLocale = cookieStore.get("okf.locale")?.value;
    const accept = headerStore.get("accept-language");
    locale = resolveServerLocale(cookieLocale, accept);
  } catch {
    // cookies/headers недоступны — фолбэк на дефолт
  }
  return createTranslator(locale);
}
