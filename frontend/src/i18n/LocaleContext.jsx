"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  detectBrowserLocale,
  DEFAULT_LOCALE,
  AUTO_FALLBACK_LOCALE,
  normalizeLocale,
  readLocaleCookie,
  readStored,
  setLocaleCookie,
  setStored,
  createTranslator,
  formatDate,
  formatDateTime,
  formatMailDateTime,
  formatNumber,
} from "./core";
import { makeTitle } from "./titles";
import { getUiDictionary, getConfigRevisions } from "@/lib/api";
import { bumpTagVersion, refreshTagDictionaryIfStale } from "@/lib/tagDictionary";
import { createReferenceWatcher } from "@/lib/referenceRevisionWatcher.mjs";

const LocaleContext = createContext(null);

// SSR-безопасный начальный язык:
//  - на клиенте (после гидратации) — сохранённый выбор или авто по браузеру;
//  - на сервере — переданный из layout (SSR-локаль из cookie/Accept-Language),
//    иначе автоматический английский fallback.
function initialLocale(ssrLocale) {
  if (typeof window !== "undefined") {
    return readStored(window) || detectBrowserLocale(window.navigator);
  }
  return ssrLocale ? normalizeLocale(ssrLocale) : AUTO_FALLBACK_LOCALE;
}

export function LocaleProvider({ initialLocale: ssrLocale, initialOverrides = {}, children }) {
  const pathname = usePathname();
  const router = useRouter();
  const [locale, setLocaleState] = useState(() => initialLocale(ssrLocale));
  // Runtime-override UI-словарей: { locale: dict }. Слой поверх versioned-словаря
  // релиза (Этап 7 фаза C). SSR передаёт override текущей локали; прочие локали
  // догружаются на клиенте при переключении.
  const [overrides, setOverrides] = useState(initialOverrides || {});
  const knownRevision = useRef(null);
  const dictionaryFailed = useRef(false);
  const [dictionaryRetry, setDictionaryRetry] = useState(0);

  // Язык всегда валиден (невалидный кламп в дефолт).
  const effective = normalizeLocale(locale);

  useEffect(() => {
    if (overrides[effective] !== undefined) return;
    let cancelled = false;
    dictionaryFailed.current = false;
    getUiDictionary(effective)
      .then((data) => {
        if (!cancelled) {
          dictionaryFailed.current = false;
          setOverrides((o) => ({ ...o, [effective]: data }));
        }
      })
      .catch(() => {
        // A transport failure is not a permanent "no override" snapshot.
        if (!cancelled) dictionaryFailed.current = true;
      });
    return () => {
      cancelled = true;
    };
  }, [effective, overrides, dictionaryRetry]);

  useEffect(() => {
    const watcher = createReferenceWatcher({
      initialVersion: knownRevision.current,
      load: signal => getConfigRevisions({signal}),
      isVisible: () => document.visibilityState === "visible",
      onChange: revision => {
        knownRevision.current = revision;
        dictionaryFailed.current = false;
        setOverrides({});
        bumpTagVersion();
        window.dispatchEvent(new Event("okf:locales-changed"));
        router.refresh();
      },
      onPoll: () => {
        refreshTagDictionaryIfStale();
        if (dictionaryFailed.current) {
          dictionaryFailed.current = false;
          setDictionaryRetry(value => value + 1);
        }
      },
    });
    const foreground = () => {
      if (document.visibilityState !== "visible") return;
      dictionaryFailed.current = false;
      setOverrides({});
      bumpTagVersion();
      void watcher.refresh();
    };
    window.addEventListener("focus", foreground);
    document.addEventListener("visibilitychange", foreground);
    void watcher.refresh();
    return () => {
      watcher.stop();
      window.removeEventListener("focus", foreground);
      document.removeEventListener("visibilitychange", foreground);
    };
  }, [pathname, router]);

  const changeLocale = useCallback((next) => {
    const code = normalizeLocale(next);
    setLocaleState(code);
    setStored(code, window);
    // RSC-заголовки читают язык из cookie; обновляем их после его записи.
    router.refresh();
    // display-имена тегов локализуются бэкендом по cookie okf.locale — общий
    // справочник (tagDictionary) сбрасываем, чтобы пикеры/фильтры перечитали его
    // на новом языке (иначе держат словарь языка до переключения).
    bumpTagVersion();
  }, [router]);

  const { t, tc } = useMemo(
    () => createTranslator(effective, overrides),
    [effective, overrides]
  );

  // Cookie okf.locale — единственный канал языка к серверу: по нему резолвятся
  // SSR-страницы и `display`-имена тегов (backend `request_locale`, фолбэк ru).
  // Раньше он писался только в `changeLocale`, поэтому первый заход
  // немецкого/английского пользователя давал английский UI с русскими
  // display-именами тегов. Синхронизируем cookie с фактическим языком на маунте
  // — заодно продлевается годичный срок жизни cookie при вечном localStorage.
  useEffect(() => {
    if (typeof window === "undefined") return;
    // Без cookie сервер отвечает на DEFAULT_LOCALE — именно с этим и сравниваем.
    const serverLocale = readLocaleCookie(window) || DEFAULT_LOCALE;
    setLocaleCookie(effective, window);
    // Справочник тегов уже мог уйти на бэкенд без cookie (ensureLoaded зовётся в
    // рендере детей, до эффектов провайдера) — сбрасываем, чтобы пикеры/фильтры
    // перечитали display-имена на языке, который сервер теперь знает. Проверка
    // записи обязательна: при заблокированных cookie сервер языка так и не
    // узнает, и рефетч был бы лишним запросом на каждый маунт.
    if (serverLocale !== effective && readLocaleCookie(window) === effective) {
      bumpTagVersion();
    }
  }, [effective]);

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
      fmtMailDateTime: (v) => formatMailDateTime(v, effective),
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
