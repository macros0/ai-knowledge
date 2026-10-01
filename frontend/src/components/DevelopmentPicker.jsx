"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { createPortal } from "react-dom";
import { LinkIcon } from "./icons";
import { positionPopup } from "@/lib/popupPosition";
import { useI18n } from "@/i18n/LocaleContext";

/**
 * Поисковый комбобокс для присвоения разработки документу.
 *
 * Попап рендерится через createPortal в document.body с position:fixed — иначе
 * его обрезает любой скролл-контейнер предка (.document-list/main с
 * overflow-y:auto), и список опций становился невидимым.
 *
 * Поиск — строго по первым символам номера разработки (префикс).
 * onChange вызывается с id разработки (number) или null («без разработки»).
 */
export default function DevelopmentPicker({ developments, value, suggestion = null, onChange, disabled = false, portalContainer = null, currentDocument = null }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null); // обёртка триггера
  const popupRef = useRef(null); // попап (в портале)
  const inputRef = useRef(null);

  const current = developments.find((d) => d.id === Number(value)) ||
    (value != null && currentDocument?.development_id === value ? {
      id: value, number: currentDocument.development_number || String(value), name: currentDocument.development_name,
    } : null);
  const suggestionNumber = String(suggestion?.number || "").trim();

  const openPopup = () => {
    if (disabled) return;
    setQuery(suggestionNumber);
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
    // Поиск строго по первым символам НОМЕРА разработки (префикс).
    return developments.filter((d) => String(d.number).toLowerCase().startsWith(q));
  }, [developments, q]);

  const pick = (devId) => {
    if (disabled) return;
    onChange(devId);
    setOpen(false);
    setQuery("");
  };

  return (
    <span className="dev-picker" ref={rootRef}>
      {current ? (
        <span className="dev-picker-assigned">
          {portalContainer || disabled ? <span className="dev-picker-link">
            {current.number}{current.display_name || current.name ? ` · ${current.display_name || current.name}` : ""}
          </span> : <Link
            className="dev-picker-link"
            href={`/developments/${current.id}`}
            title={t("dev.title", { name: (current.display_name || current.name) || "" })}
          >
            <LinkIcon size={12} />
            {current.number}
            {current.display_name || current.name ? ` · ${current.display_name || current.name}` : ""}
          </Link>}
          <button
            type="button"
            className="tag-edit-toggle"
            disabled={disabled}
            onClick={openPopup}
            title={t("dev.changeTitle")}
            aria-label={t("dev.changeAria")}
          >
            ✎
          </button>
        </span>
      ) : (
        <button
          type="button"
          disabled={disabled}
          className={`dev-picker-trigger${suggestion ? " dev-picker-trigger-suggestion" : ""}`}
          onClick={openPopup}
          title={suggestion ? t("dev.suggestionTitle") : t("dev.assignTitle")}
        >
          {suggestion
            ? t("dev.suggestionTrigger", { number: suggestionNumber || suggestion.name || "—" })
            : t("dev.none")}
        </button>
      )}
      {open && !disabled &&
        createPortal(
          <div
            className="dev-picker-pop"
            ref={popupRef}
            onMouseDown={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.preventDefault(); e.stopPropagation(); setOpen(false);
                rootRef.current?.querySelector("button")?.focus();
              }
            }}
          >
            <input
              ref={inputRef}
              className="dev-picker-input"
              placeholder={t("dev.searchPlaceholder")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <ul className="dev-picker-list">
              <li>
                <button type="button" className="dev-picker-option" onClick={() => pick(null)}>
                  {t("dev.none")}
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
          portalContainer || document.body
        )}
    </span>
  );
}
