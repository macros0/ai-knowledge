"use client";

import { TrashIcon } from "./icons";

/**
 * Danger-zone: панель массовых действий над выбранными документами (только admin).
 * Видна при выборе хотя бы одного документа. Запуск — только через предпросмотр.
 */
export default function BulkActionsBar({ selectedIds, onClear, onOpenPreview }) {
  if (selectedIds.length === 0) return null;

  return (
    <div className="bulk-bar">
      <span className="bulk-count">
        <TrashIcon size={14} /> Выбрано: <strong>{selectedIds.length}</strong>
      </span>
      <button className="bulk-preview-btn" onClick={onOpenPreview}>
        Показать, что будет затронуто
      </button>
      <button className="bulk-clear-btn" onClick={onClear}>
        Снять выбор
      </button>
    </div>
  );
}
