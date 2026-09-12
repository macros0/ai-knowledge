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
}) {
  const { t, locale } = useI18n();
  const [active, setActive] = useState([]);
  const [editing, setEditing] = useState(false);
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
          <span>{t(labelKey)}</span>{" "}
          <SearchableSelect
            options={buildLocaleOptions(selectedLocale, active, locale).map((option) => ({
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
