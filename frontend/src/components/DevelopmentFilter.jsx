"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { positionPopup } from "@/lib/popupPosition";
import { useI18n } from "@/i18n/LocaleContext";

/**
 * Поисковый фильтр по разработке для панели фильтров списка документов.
 *
 * По UX повторяет DevelopmentPicker (поиск по первым символам номера — префикс),
 * но это фильтр, а не присвоение: выбор одного значения сужает список документов.
 * Попап — через createPortal в document.body с position:fixed (как в пикере),
 * иначе его обрезает скролл-контейнер предка.
 *
 * onChange: id разработки (number) или null — «все разработки».
 */
export default function DevelopmentFilter({ developments, value, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null);
  const popupRef = useRef(null);
  const inputRef = useRef(null);

  const current = developments.find((d) => d.id === Number(value)) || null;

  const openPopup = () => {
    setQuery("");
    setOpen(true);
  };

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useLayoutEffect(() => {
    if (open) {
      const trigger = rootRef.current;
      const popup = popupRef.current;
      if (trigger && popup) positionPopup(trigger, popup);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (rootRef.current?.contains(e.target)) return;
      if (popupRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!q) return developments;
    return developments.filter((d) => String(d.number).toLowerCase().startsWith(q));
  }, [developments, q]);

  const pick = (devId) => {
    onChange(devId);
    setOpen(false);
    setQuery("");
  };

  return (
    <span className="dev-filter" ref={rootRef}>
      <button
        type="button"
        className="doc-filter-select dev-filter-trigger"
        onClick={openPopup}
        title={t("dev.filterTitle")}
        aria-label={t("dev.filterAria")}
      >
        {current
          ? `${current.number}${current.display_name || current.name ? ` · ${current.display_name || current.name}` : ""}`
          : t("dev.all")}
      </button>
      {current && (
        <button
          type="button"
          className="dev-filter-clear"
          onClick={() => pick(null)}
          title={t("dev.clearTitle")}
          aria-label={t("dev.clearAria")}
        >
          ×
        </button>
      )}
      {open &&
        createPortal(
          <div
            className="dev-picker-pop"
            ref={popupRef}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <input
              ref={inputRef}
              className="dev-picker-input"
              placeholder={t("dev.searchPlaceholder")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
              }}
            />
            <ul className="dev-picker-list">
              <li>
                <button type="button" className="dev-picker-option" onClick={() => pick(null)}>
                  {t("dev.all")}
                </button>
              </li>
              {filtered.map((d) => (
                <li key={d.id}>
                  <button type="button" className="dev-picker-option" onClick={() => pick(d.id)}>
                    <span className="dev-picker-num">{d.number}</span>
                    <span className="dev-picker-name">{d.display_name || d.name}</span>
                  </button>
                </li>
              ))}
              {filtered.length === 0 && <li className="dev-picker-empty">{t("dev.notFound")}</li>}
            </ul>
          </div>,
          document.body
        )}
    </span>
  );
}
