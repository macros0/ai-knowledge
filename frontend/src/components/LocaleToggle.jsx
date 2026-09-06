"use client";

import { useEffect, useState } from "react";
import { GlobeIcon } from "@/components/icons";
import { useI18n } from "@/i18n/LocaleContext";
import { SUPPORTED_LOCALES } from "@/i18n/core";
import locales from "@/i18n/locales/index.js";
import { listActiveLocales } from "@/lib/api";

// Циклический переключатель языка по АКТИВНЫМ языкам (из бэкенда, «Поддержка
// языков»): админ может отключить язык — он исчезает из цикла переключения.
// Пока список активных не загружен (или бэкенд недоступен) — фолбэк на
// статический манифест. Один активный язык — переключатель скрыт.
export default function LocaleToggle() {
  const { locale, setLocale } = useI18n();
  const [active, setActive] = useState(null);

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
  }, []);

  const cycle = active && active.length ? active : SUPPORTED_LOCALES;
  if (cycle.length < 2) return null;

  const index = cycle.indexOf(locale);
  const next = cycle[(index + 1) % cycle.length];
  const cur = locales[locale] ?? locales.ru;
  const nextLabel = locales[next]?.label ?? next;

  return (
    <button
      type="button"
      className="icon-btn locale-toggle-btn"
      onClick={() => setLocale(next)}
      aria-label={`Язык интерфейса: ${cur.label}`}
      title={`Язык: ${cur.label} · клик — переключить на ${nextLabel}`}
    >
      <GlobeIcon size={16} />
      <span className="locale-toggle-code">{cur.short}</span>
    </button>
  );
}
