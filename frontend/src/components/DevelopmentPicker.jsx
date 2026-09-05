"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { createPortal } from "react-dom";
import { LinkIcon, PencilIcon } from "./icons";
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
export default function DevelopmentPicker({ developments, value, onChange }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef(null); // обёртка триггера
  const popupRef = useRef(null); // попап (в портале)
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
    // Поиск строго по первым символам НОМЕРА разработки (префикс).
    return developments.filter((d) => String(d.number).toLowerCase().startsWith(q));
  }, [developments, q]);

  const pick = (devId) => {
    onChange(devId);
    setOpen(false);
    setQuery("");
  };

  return (
    <span className="dev-picker" ref={rootRef}>
      {current ? (
        <span className="dev-picker-assigned">
          <Link
            className="dev-picker-link"
            href={`/developments/${current.id}`}
            title={t("dev.title", { name: current.name || "" })}
          >
            <LinkIcon size={12} />
            {current.number}
            {current.name ? ` · ${current.name}` : ""}
          </Link>
          <button
            type="button"
            className="dev-picker-edit"
            onClick={openPopup}
            title={t("dev.changeTitle")}
            aria-label={t("dev.changeAria")}
          >
            <PencilIcon size={12} />
          </button>
        </span>
      ) : (
        <button
          type="button"
          className="dev-picker-trigger"
          onClick={openPopup}
          title={t("dev.assignTitle")}
        >
          {t("dev.none")}
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
                  {t("dev.none")}
                </button>
              </li>
              {filtered.map((d) => (
                <li key={d.id}>
                  <button type="button" className="dev-picker-option" onClick={() => pick(d.id)}>
                    <span className="dev-picker-num">{d.number}</span>
                    <span className="dev-picker-name">{d.name}</span>
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
