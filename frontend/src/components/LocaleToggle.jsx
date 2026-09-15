"use client";

import { useEffect, useState } from "react";
import { GlobeIcon } from "@/components/icons";
import { useI18n } from "@/i18n/LocaleContext";
import { SUPPORTED_LOCALES } from "@/i18n/core";
import locales from "@/i18n/locales/index.js";
import { listActiveLocales } from "@/lib/api";
import { languageLabel } from "@/lib/sourceLocales.mjs";
import SearchableSelect from "./SearchableSelect";

// Переключатель языка по АКТИВНЫМ языкам (из бэкенда, «Поддержка языков»):
// при двух языках клик сразу переключает на другой, от трёх — открывает меню.
// Админ может отключить язык — он исчезает из доступных вариантов.
// Пока список активных не загружен (или бэкенд недоступен) — фолбэк на
// статический манифест. Один активный язык — переключатель скрыт.
export default function LocaleToggle() {
  const { locale, setLocale, t } = useI18n();
  const [active, setActive] = useState(null);
  const [version, setVersion] = useState(0); // bump для рефетча активных

  useEffect(() => {
    const refetch = () => setVersion((v) => v + 1);
    // Именованный обработчик, а не инлайн-стрелка: снимать надо ту же ссылку,
    // иначе removeEventListener не снимает ничего и в StrictMode-dev слушатели
    // копятся (компонент живёт в layout и не перемонтируется при навигации).
    const onVisibility = () => {
      if (document.visibilityState === "visible") refetch();
    };
    // Активные языки меняются админом во вкладке «Поддержка языков»: компонент живёт
    // в layout и не перемонтируется при навигации — без события тоггл «застревал» бы
    // в скрытом состоянии после активации второго языка.
    window.addEventListener("okf:locales-changed", refetch);
    window.addEventListener("focus", refetch);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("okf:locales-changed", refetch);
      window.removeEventListener("focus", refetch);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    listActiveLocales()
      .then((list) => {
        const codes = (list || [])
          .map((l) => l.code)
          .filter((c) => SUPPORTED_LOCALES.includes(c));
        if (mounted && codes.length) setActive(codes);
      })
      .catch(() => {
        // бэкенд недоступен — оставляем манифест
      });
    return () => {
      mounted = false;
    };
  }, [version]);

  const cycle = active && active.length ? active : SUPPORTED_LOCALES;
  if (cycle.length < 2) return null;

  const cur = locales[locale] ?? locales.ru;
  const currentIndex = cycle.indexOf(locale);
  const ariaLabel = t("locale.ariaLabel", { label: cur.label });
  const title = t("locale.title", { label: cur.label });

  if (cycle.length < 3) {
    return (
      <button
        type="button"
        className="icon-btn locale-toggle-btn"
        onClick={() => setLocale(cycle[(currentIndex + 1) % cycle.length])}
        aria-label={ariaLabel}
        title={title}
      >
        <GlobeIcon size={16} />
        <span className="locale-toggle-code">{cur.short}</span>
      </button>
    );
  }

  const localeOptions = cycle.map((code) => ({
    value: code,
    label: locales[code]?.label ?? languageLabel(code, locale) ?? code,
    searchText: `${code} ${locales[code]?.label ?? languageLabel(code, locale) ?? ""}`,
  }));

  if (cycle.length >= 3) {
    return <SearchableSelect
      options={localeOptions}
      value={locale}
      onChange={setLocale}
      ariaLabel={ariaLabel}
      title={title}
      searchPlaceholder={t("locale.searchPlaceholder")}
      emptyLabel={t("locale.notFound")}
      triggerClassName="icon-btn locale-toggle-btn"
      renderTrigger={() => (
        <>
          <GlobeIcon size={16} />
          <span className="locale-toggle-code">{cur.short}</span>
        </>
      )}
    />;
  }

  return null;
}
