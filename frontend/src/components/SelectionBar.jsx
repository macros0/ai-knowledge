"use client";

import { useState } from "react";
import { bulkUpdateTags } from "@/lib/api";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { TrashIcon } from "./icons";
import TagCombobox from "./TagCombobox";

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
  open = false,
  onToggle,
  onToggleAllByFilter,
  onSelectPage,
  onOpenPreview,
  onDone,
}) {
  const [addTag, setAddTag] = useState("");
  const [removeTag, setRemoveTag] = useState("");
  const [busy, setBusy] = useState(false);

  const hasSelection = selectedIds.length > 0;
  const danger = canDelete && hasSelection;

  const applyTags = async (op) => {
    const value = op === "add" ? addTag.trim() : removeTag.trim();
    if (!value || !hasSelection || busy) return;
    setBusy(true);
    try {
      const result = await bulkUpdateTags(selectedIds, {
        add: op === "add" ? [value] : [],
        remove: op === "remove" ? [value] : [],
      });
      bumpTagVersion();
      onDone?.(result);
      if (op === "add") setAddTag("");
      else setRemoveTag("");
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
        aria-label="Массовые действия"
      >
        <span>Массовые действия</span>
        <span className="selection-count">
          Выбрано: <strong>{selectedIds.length}</strong> из {total}
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
            title="Выделить все документы по текущему фильтру (до 50) / снять выделение"
          >
            {allByFilterOn ? "✓ " : allByFilterPartial ? "◐ " : ""}Выделить все
          </button>
          <button
            type="button"
            className="bulk-tag-btn"
            onClick={onSelectPage}
            disabled={busy || total === 0}
            title="Добавить к выделению документы текущей страницы"
          >
            Выделить на странице
          </button>
          <span className="bulk-count">
            Выбрано: <strong>{selectedIds.length}</strong> из {total}
          </span>
          <TagCombobox
            value={addTag}
            onChange={setAddTag}
            placeholder="Добавить тег"
            ariaLabel="Добавить тег выбранным документам"
            className="bulk-tag-input"
            disabled={!hasSelection}
          />
          <button
            type="button"
            className="bulk-tag-btn"
            onClick={() => applyTags("add")}
            disabled={busy || !hasSelection}
          >
            Добавить
          </button>
          <TagCombobox
            value={removeTag}
            onChange={setRemoveTag}
            placeholder="Убрать тег"
            ariaLabel="Убрать тег у выбранных документов"
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
            Убрать
          </button>
          {canDelete && (
            <button
              type="button"
              className="bulk-preview-btn"
              onClick={onOpenPreview}
              disabled={busy || !hasSelection}
            >
              <TrashIcon size={14} /> Показать, что будет затронуто
            </button>
          )}
        </div>
      )}
    </div>
  );
}
