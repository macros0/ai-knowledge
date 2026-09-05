"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import {
  DEFAULT_LOCALE,
  detectLocale,
  normalizeLocale,
  readStored,
  setStored,
  createTranslator,
  formatDate,
  formatDateTime,
  formatNumber,
} from "./core";
import { makeTitle } from "./titles";

const LocaleContext = createContext(null);

// SSR-безопасный начальный язык:
//  - на клиенте (после гидратации) — сохранённый выбор или авто по браузеру;
//  - на сервере — переданный из layout (SSR-локаль из cookie/Accept-Language),
//    иначе дефолт.
function initialLocale(ssrLocale) {
  if (typeof window !== "undefined") {
    return readStored(window) || detectLocale(window.navigator?.languages);
  }
  return normalizeLocale(ssrLocale) || DEFAULT_LOCALE;
}

export function LocaleProvider({ initialLocale: ssrLocale, children }) {
  const pathname = usePathname();
  const [locale, setLocaleState] = useState(() => initialLocale(ssrLocale));

  // Язык всегда валиден (невалидный кламп в дефолт).
  const effective = normalizeLocale(locale);

  const changeLocale = useCallback((next) => {
    const code = normalizeLocale(next);
    setLocaleState(code);
    setStored(code, window);
  }, []);

  const { t, tc } = useMemo(() => createTranslator(effective), [effective]);

  // lang на <html> держится в синхроне с выбранным языком (до гидратации —
  // bootScript, после — здесь).
  useEffect(() => {
    if (typeof window !== "undefined") {
      window.document.documentElement.lang = effective;
    }
  }, [effective]);

  // Мгновенное обновление document.title при смене языка.
  useEffect(() => {
    if (typeof window !== "undefined") {
      window.document.title = makeTitle(pathname, effective);
    }
  }, [pathname, effective]);

  const value = useMemo(
    () => ({
      locale: effective,
      t,
      tc,
      setLocale: changeLocale,
      fmtDate: (v) => formatDate(v, effective),
      fmtDateTime: (v) => formatDateTime(v, effective),
      fmtNumber: (v) => formatNumber(v, effective),
    }),
    [effective, t, tc, changeLocale]
  );

  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useI18n() {
  const ctx = useContext(LocaleContext);
  if (!ctx) throw new Error("useI18n должен использоваться внутри <LocaleProvider>");
  return ctx;
}