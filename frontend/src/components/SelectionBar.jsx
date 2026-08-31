"use client";

import { useEffect, useRef, useState } from "react";
import { bulkUpdateTags } from "@/lib/api";
import { bumpTagVersion } from "@/lib/tagDictionary";
import { TrashIcon } from "./icons";
import TagCombobox from "./TagCombobox";

/**
 * Панель выделения документов (для Editor/Admin), свёрнута под спойлер.
 *
 * Массовые действия — не частая операция, поэтому по умолчанию виден только
 * компактный заголовок-кнопка «Массовые действия» со счётчиком выделения.
 * Тело панели (главный чекбокс страницы, «Выделить все по фильтру», «Снять
 * выделение», массовые правки тегов, деструктивный предпросмотр для Admin)
 * раскрывается по клику; при появлении выделения — автоматически.
 */
export default function SelectionBar({
  selectedIds,
  pageDocIds,
  total,
  allOnPageSelected,
  someOnPageSelected,
  canDelete = false,
  open = false,
  onToggle,
  onTogglePage,
  onSelectAll,
  onClear,
  onOpenPreview,
  onDone,
}) {
  const [addTag, setAddTag] = useState("");
  const [removeTag, setRemoveTag] = useState("");
  const [busy, setBusy] = useState(false);
  const pageCheckRef = useRef(null);

  // React не поддерживает indeterminate как prop — выставляем свойство через ref.
  useEffect(() => {
    if (pageCheckRef.current) {
      pageCheckRef.current.indeterminate = someOnPageSelected && !allOnPageSelected;
    }
  }, [someOnPageSelected, allOnPageSelected]);

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
          Выбрано: <strong>{selectedIds.length}</strong>
        </span>
        <span className="selection-chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className={`bulk-bar ${danger ? "bulk-bar-danger" : "bulk-bar-soft"}`}>
      <input
        ref={pageCheckRef}
        type="checkbox"
        className="selection-page-checkbox"
        checked={allOnPageSelected}
        onChange={onTogglePage}
        aria-label="Выбрать все на текущей странице"
        title="Выбрать все на текущей странице"
      />
      <span className="bulk-count">
        Выбрано: <strong>{selectedIds.length}</strong>
      </span>
      <button
        className="bulk-tag-btn"
        onClick={onSelectAll}
        disabled={busy || total === 0}
        title="Выделить документы по текущему фильтру (до 50)"
      >
        Выделить все по фильтру
      </button>
      <button
        className="bulk-clear-btn"
        onClick={onClear}
        disabled={busy || !hasSelection}
      >
        Снять выделение
      </button>
      <TagCombobox
        value={addTag}
        onChange={setAddTag}
        placeholder="Добавить тег"
        ariaLabel="Добавить тег выбранным документам"
        className="bulk-tag-input"
        disabled={!hasSelection}
      />
      <button
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
        className="bulk-tag-btn"
        onClick={() => applyTags("remove")}
        disabled={busy || !hasSelection}
      >
        Убрать
      </button>
      {canDelete && (
        <button
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