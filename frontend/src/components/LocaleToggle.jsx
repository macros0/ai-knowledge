"use client";

import { GlobeIcon } from "@/components/icons";
import { useI18n } from "@/i18n/LocaleContext";
import { SUPPORTED_LOCALES } from "@/i18n/core";
import locales from "@/i18n/locales/index.js";

// Циклический переключатель языка: RU → EN → RU. Названия языков — эндонимы
// (из манифеста), перевод не требуется.
export default function LocaleToggle() {
  const { locale, setLocale } = useI18n();
  const index = SUPPORTED_LOCALES.indexOf(locale);
  const next = SUPPORTED_LOCALES[(index + 1) % SUPPORTED_LOCALES.length];
  const cur = locales[locale];
  const nextLabel = locales[next].label;

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