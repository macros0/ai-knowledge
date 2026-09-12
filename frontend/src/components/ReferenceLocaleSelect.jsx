"use client";

import { useEffect, useId, useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";
import { listActiveLocales } from "@/lib/api";
import { buildLocaleOptions, languageLabel } from "@/lib/sourceLocales.mjs";

export default function ReferenceLocaleSelect({
  value,
  onChange,
  disabled = false,
  labelKey = "reference.originalLanguage",
  editLabelKey = "reference.editLanguage",
  collapseLabelKey = "reference.collapseLanguage",
}) {
  const { t, locale } = useI18n();
  const [active, setActive] = useState([]);
  const [editing, setEditing] = useState(false);
  const selectId = useId();
  // Null means the stored language is unknown; do not silently display the
  // UI language as if it described the reference.
  const selectedLocale = value || "und";
  useEffect(() => {
    let cancelled = false;
    listActiveLocales().then((items) => { if (!cancelled) setActive(items); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  return (
    <span className="reference-locale-select">
      {editing ? (
        <>
          <label htmlFor={selectId}>{t(labelKey)}</label>{" "}
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
        <span className="doc-locale-badge" title={t(labelKey)}>
          {t(labelKey)}: {languageLabel(selectedLocale, locale) || selectedLocale}
        </span>
      )}{" "}
      <button type="button" className="tag-edit-toggle"
        aria-label={t(editing ? collapseLabelKey : editLabelKey)}
        aria-expanded={editing} aria-controls={editing ? selectId : undefined}
        onClick={() => setEditing((current) => !current)} disabled={disabled}>
        {editing ? "−" : "✎"}
      </button>
    </span>
  );
}
