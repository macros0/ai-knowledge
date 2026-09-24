"use client";

import { useState } from "react";
import { bulkUpdateTags, friendlyApiError } from "@/lib/api";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { useToast } from "./Toast";
import { useI18n } from "@/i18n/LocaleContext";
import { TrashIcon } from "./icons";
import TagCombobox from "./TagCombobox";
import ReferenceLocaleSelect from "./ReferenceLocaleSelect";

// Должен совпадать с backend BULK_TAGS_MAX_DOCS (config.bulk_tags_max_docs).
const MAX_BULK_DOCS = 50;

/**
 * Панель выделения документов (для Editor/Admin), свёрнута под спойлер.
 *
 * Массовые действия — не частая операция, поэтому по умолчанию виден только
 * компактный заголовок-кнопка «Массовые действия» со счётчиком выделения.
 * Тело панели (переключатель «Выделить все», независимая кнопка «Выделить на
 * странице», счётчик «N из total», массовые правки тегов, деструктивный
 * предпросмотр для Admin) раскрывается по клику; при появлении выделения —
 * автоматически.
 *
 * «Выделить все» — двухпозиционный toggle с собственным состоянием: кнопка
 * сама знает, включена она (✓) или частично рассинхронизирована с фильтром
 * (◐), и её клик предсказуем в обе стороны. Точный факт «сколько выделено»
 * несёт счётчик «N из total», а не кнопка.
 */
export default function SelectionBar({
  selectedIds,
  total,
  allByFilterOn,
  allByFilterPartial,
  canDelete = false,
  canExport = false,
  open = false,
  onToggle,
  onToggleAllByFilter,
  onSelectPage,
  onOpenPreview,
  onGeneration,
  onExport,
  onDone,
}) {
  const [addTag, setAddTag] = useState("");
  const [removeTag, setRemoveTag] = useState("");
  const [busy, setBusy] = useState(false);
  const { showToast } = useToast();
  const { t, locale } = useI18n();
  const [tagLocale, setTagLocale] = useState(locale);

  const hasSelection = selectedIds.length > 0;
  const danger = canDelete && hasSelection;

  const applyTags = async (op) => {
    const value = op === "add" ? addTag.trim() : removeTag.trim();
    if (!value || !hasSelection || busy) return;
    if (selectedIds.length > MAX_BULK_DOCS) {
      showToast(
        t("selection.limitToast", { max: MAX_BULK_DOCS, selected: selectedIds.length }),
        { type: "warning", duration: 8000 }
      );
      return;
    }
    setBusy(true);
    try {
      const result = await bulkUpdateTags(selectedIds, {
        add: op === "add" ? [value] : [],
        canonicalLocale: tagLocale || locale,
        remove: op === "remove" ? [value] : [],
      });
      bumpTagVersion();
      onDone?.(result);
      if (op === "add") setAddTag("");
      else setRemoveTag("");
    } catch (err) {
      showToast(t("selection.bulkError", { message: friendlyApiError(err, t) }), { type: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="selection-panel">
      <button
        type="button"
        className="selection-toggle"
        onClick={onToggle}
        aria-expanded={open}
        aria-label={t("selection.bulkActions")}
      >
        <span>{t("selection.bulkActions")}</span>
        <span className="selection-count">
          {t("selection.selected", { selected: selectedIds.length, total })}
        </span>
        <span className="selection-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className={`bulk-bar ${danger ? "bulk-bar-danger" : "bulk-bar-soft"}`}>
          <button
            type="button"
            className={`all-by-filter-btn${allByFilterOn ? " active" : ""}${allByFilterPartial ? " partial" : ""}`}
            onClick={onToggleAllByFilter}
            disabled={busy || total === 0}
            title={t("selection.selectAllTitle")}
          >
            {allByFilterOn ? "✓ " : allByFilterPartial ? "◐ " : ""}{t("selection.selectAll")}
          </button>
          <button
            type="button"
            className="bulk-tag-btn"
            onClick={onSelectPage}
            disabled={busy || total === 0}
            title={t("selection.selectPageTitle")}
          >
            {t("selection.selectPage")}
          </button>
          <span className="bulk-count">
            {t("selection.selected", { selected: selectedIds.length, total })}
          </span>
          {canExport && (
            <button
              type="button"
              className="bulk-tag-btn"
              onClick={onExport}
              disabled={busy || !hasSelection}
              title={t("selection.exportTitle", { selected: selectedIds.length })}
            >
              {t("selection.export")}
            </button>
          )}
          {canDelete && <>
            <button type="button" className="bulk-tag-btn" disabled={busy || !hasSelection} onClick={() => onGeneration("regenerate")}>
              {t("bulkGeneration.regenerate")}
            </button>
            <button type="button" className="bulk-tag-btn" disabled={busy || !hasSelection} onClick={() => onGeneration("resume")}>
              {t("bulkGeneration.resume")}
            </button>
            <button type="button" className="bulk-tag-btn" disabled={busy} onClick={() => onGeneration("interrupted")}>
              {t("bulkGeneration.interrupted")}
            </button>
          </>}
          <ReferenceLocaleSelect value={tagLocale} onChange={setTagLocale} disabled={busy} />
          <TagCombobox
            value={addTag}
            onChange={setAddTag}
            placeholder={t("selection.addPlaceholder")}
            ariaLabel={t("selection.addAria")}
            className="bulk-tag-input"
            disabled={!hasSelection}
          />
          <button
            type="button"
            className="bulk-tag-btn"
            onClick={() => applyTags("add")}
            disabled={busy || !hasSelection}
          >
            {t("selection.add")}
          </button>
          <TagCombobox
            value={removeTag}
            onChange={setRemoveTag}
            placeholder={t("selection.removePlaceholder")}
            ariaLabel={t("selection.removeAria")}
            allowNew={false}
            className="bulk-tag-input"
            disabled={!hasSelection}
          />
          <button
            type="button"
            className="bulk-tag-btn"
            onClick={() => applyTags("remove")}
            disabled={busy || !hasSelection}
          >
            {t("selection.remove")}
          </button>
          {canDelete && (
            <button
              type="button"
              className="bulk-preview-btn"
              onClick={onOpenPreview}
              disabled={busy || !hasSelection}
            >
              <TrashIcon size={14} /> {t("selection.previewImpact")}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
