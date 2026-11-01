"use client";

import { useEffect, useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";
import { listActiveLocales } from "@/lib/api";
import { buildLocaleOptions, languageLabel } from "@/lib/sourceLocales.mjs";
import SearchableSelect from "./SearchableSelect";

export default function ReferenceLocaleSelect({
  value,
  onChange,
  disabled = false,
  labelKey = "reference.originalLanguage",
  editLabelKey = "reference.editLanguage",
  collapseLabelKey = "reference.collapseLanguage",
  portalContainer = null,
  activeLocales,
}) {
  const { t, locale } = useI18n();
  const [active, setActive] = useState([]);
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(0);
  // Null means the stored language is unknown; do not silently display the
  // UI language as if it described the reference.
  const selectedLocale = value || "und";
  useEffect(() => {
    if (activeLocales) return;
    const refresh = () => setVersion(value => value + 1);
    const visible = () => {if (document.visibilityState === "visible") refresh();};
    window.addEventListener("okf:locales-changed", refresh);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visible);
    return () => {
      window.removeEventListener("okf:locales-changed", refresh);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [activeLocales]);
  useEffect(() => {
    if (activeLocales) return;
    let cancelled = false;
    listActiveLocales().then((items) => { if (!cancelled) setActive(items); }).catch(() => {});
    return () => { cancelled = true; };
  }, [activeLocales, version]);
  return (
    <span className="reference-locale-select">
      {editing ? (
        <>
          <span>{t(labelKey)}</span>{" "}
          <SearchableSelect
            options={buildLocaleOptions(selectedLocale, activeLocales || active, locale).map((option) => ({
              value: option.code,
              label: option.label,
              searchText: `${option.code} ${option.label}`,
            }))}
            value={selectedLocale}
            onChange={(next) => {
              onChange(next);
              setEditing(false);
            }}
            disabled={disabled}
            portalContainer={portalContainer}
            searchPlaceholder={t("docs.localeSearchPlaceholder")}
            emptyLabel={t("docs.empty")}
            ariaLabel={t(labelKey)}
          />
        </>
      ) : (
        <span className="doc-locale-badge" title={t(labelKey)}>
          {t(labelKey)}: {languageLabel(selectedLocale, locale) || selectedLocale}
        </span>
      )}{" "}
      <button type="button" className="tag-edit-toggle"
        aria-label={t(editing ? collapseLabelKey : editLabelKey)}
        aria-expanded={editing}
        onClick={() => setEditing((current) => !current)} disabled={disabled}>
        {editing ? "−" : "✎"}
      </button>
    </span>
  );
}
