"use client";

import { useEffect, useId, useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";
import { listActiveLocales } from "@/lib/api";
import { buildLocaleOptions, languageLabel } from "@/lib/sourceLocales.mjs";

export default function ReferenceLocaleSelect({ value, onChange, disabled = false }) {
  const { t, locale } = useI18n();
  const [active, setActive] = useState([]);
  const [editing, setEditing] = useState(false);
  const selectId = useId();
  const selectedLocale = value || locale;
  useEffect(() => {
    let cancelled = false;
    listActiveLocales().then((items) => { if (!cancelled) setActive(items); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  return (
    <span className="reference-locale-select">
      {editing ? (
        <>
          <label htmlFor={selectId}>{t("reference.originalLanguage")}</label>{" "}
          <select id={selectId} className="doc-filter-select" value={selectedLocale}
            onChange={(e) => {
              onChange(e.target.value);
              setEditing(false);
            }} disabled={disabled}>
            {buildLocaleOptions(selectedLocale, active, locale).map((option) => (
              <option key={option.code} value={option.code}>{option.label}</option>
            ))}
          </select>
        </>
      ) : (
        <span className="doc-locale-badge" title={t("reference.originalLanguage")}>
          {t("reference.originalLanguage")}: {languageLabel(selectedLocale, locale) || selectedLocale}
        </span>
      )}{" "}
      <button type="button" className="tag-edit-toggle"
        aria-label={t(editing ? "reference.collapseLanguage" : "reference.editLanguage")}
        aria-expanded={editing} aria-controls={editing ? selectId : undefined}
        onClick={() => setEditing((current) => !current)} disabled={disabled}>
        {editing ? "−" : "✎"}
      </button>
    </span>
  );
}
