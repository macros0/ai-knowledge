"use client";

import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/i18n/LocaleContext";
import { BULK_LIMITS } from "@/lib/documentBulkLimits.mjs";
import { TrashIcon } from "./icons";

export default function SelectionBar({ selectedIds, canDelete = false, canExport = false, exportMaxDocs = 1000,
  onEditTags, onOpenPreview, onGeneration, onExport, onClear }) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const triggerRef = useRef(null);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event) => {
      if (event.type === "keydown") {
        if (event.key !== "Escape") return;
        event.preventDefault();
        setMenuOpen(false);
        triggerRef.current?.focus();
      } else if (!menuRef.current?.contains(event.target)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [menuOpen]);
  if (!selectedIds.length) return null;
  const count = selectedIds.length;
  const action = (callback) => { setMenuOpen(false); triggerRef.current?.focus(); callback(); };
  return <div className="selection-panel">
    <div className="bulk-bar bulk-bar-soft">
      <strong className="bulk-count" role="status">{t("selection.count", { selected: count })}</strong>
      <LimitedAction name={t("selection.editTags")} onClick={onEditTags} count={count} max={BULK_LIMITS.tags} helpId="tags-selection-limit" />
      {canExport && <LimitedAction name={t("selection.export")} onClick={onExport} count={count} max={exportMaxDocs} helpId="export-selection-limit" />}
      {canDelete && <div className="bulk-more" ref={menuRef}>
        <button ref={triggerRef} type="button" className="bulk-tag-btn" onClick={() => setMenuOpen((value) => !value)} aria-expanded={menuOpen}>
          {t("selection.more")} ▾
        </button>
        {menuOpen && <div className="bulk-more-options">
          <button type="button" onClick={() => action(() => onGeneration("resume"))}>{t("bulkGeneration.resume")}</button>
          <button type="button" onClick={() => action(() => onGeneration("regenerate"))}>{t("bulkGeneration.regenerate")}</button>
          <LimitedAction name={<><TrashIcon size={14} /> {t("selection.delete")}</>} onClick={() => action(onOpenPreview)} count={count} max={BULK_LIMITS.delete} helpId="delete-selection-limit" />
        </div>}
      </div>}
      <button type="button" className="bulk-tag-btn selection-clear" onClick={onClear}>{t("selection.clear")}</button>
    </div>
  </div>;
}

function LimitedAction({ name, onClick, count, max, helpId }) {
  const { t } = useI18n();
  return <span className="bulk-action-with-help">
    <button type="button" className="bulk-tag-btn" onClick={onClick} disabled={count > max}
      aria-describedby={count > max ? helpId : undefined}>{name}</button>
    {count > max && <span id={helpId} className="bulk-action-help" tabIndex={0}>{t("selection.actionLimit", { max })}</span>}
  </span>;
}
