"use client";

import SearchableSelect from "./SearchableSelect";
import { useI18n } from "@/i18n/LocaleContext";

/**
 * Поисковый фильтр по разработке для панели фильтров списка документов.
 * Выбор одного значения сужает список документов; null означает «все».
 */
export default function DevelopmentFilter({ developments, value, onChange }) {
  const { t } = useI18n();
  const current = developments.find((d) => d.id === Number(value)) || null;
  const options = [
    { value: "", label: t("dev.all") },
    ...developments.map((development) => ({
      value: String(development.id),
      label: `${development.number}${development.display_name || development.name ? ` · ${development.display_name || development.name}` : ""}`,
      searchText: development.number,
      development,
    })),
  ];

  return (
    <span className="dev-filter">
      <SearchableSelect
        options={options}
        value={value == null ? "" : String(value)}
        onChange={(next) => onChange(next ? Number(next) : null)}
        placeholder={t("dev.all")}
        searchPlaceholder={t("dev.searchPlaceholder")}
        emptyLabel={t("dev.notFound")}
        ariaLabel={t("dev.filterAria")}
        popupMinWidth={360}
        popupMaxWidth={560}
        triggerClassName="doc-filter-select dev-filter-trigger"
        renderTrigger={() => current
          ? `${current.number}${current.display_name || current.name ? ` · ${current.display_name || current.name}` : ""}`
          : t("dev.all")}
        renderOption={(option) => option.development ? (
          <>
            <span className="dev-picker-num">{option.development.number}</span>
            <span className="dev-picker-name">{option.development.display_name || option.development.name}</span>
          </>
        ) : option.label}
      />
      {current && (
        <button
          type="button"
          className="dev-filter-clear"
          onClick={() => onChange(null)}
          title={t("dev.clearTitle")}
          aria-label={t("dev.clearAria")}
        >
          ×
        </button>
      )}
    </span>
  );
}
