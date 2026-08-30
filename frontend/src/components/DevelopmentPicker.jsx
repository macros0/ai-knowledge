"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { createPortal } from "react-dom";
import { LinkIcon, PencilIcon } from "./icons";

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
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const rootRef = useRef(null); // обёртка триггера
  const popupRef = useRef(null); // попап (в портале)
  const inputRef = useRef(null);

  const current = developments.find((d) => d.id === Number(value)) || null;

  const openPopup = () => {
    const r = rootRef.current?.getBoundingClientRect();
    if (r) {
      setPos({ top: r.bottom + 4, left: r.left });
    }
    setQuery("");
    setOpen(true);
  };

  useEffect(() => {
    if (open) inputRef.current?.focus();
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
            title={`Разработка: ${current.name || ""}`}
          >
            <LinkIcon size={12} />
            {current.number}
            {current.name ? ` · ${current.name}` : ""}
          </Link>
          <button
            type="button"
            className="dev-picker-edit"
            onClick={openPopup}
            title="Изменить разработку"
            aria-label="Изменить разработку"
          >
            <PencilIcon size={12} />
          </button>
        </span>
      ) : (
        <button
          type="button"
          className="dev-picker-trigger"
          onClick={openPopup}
          title="Присвоить разработку"
        >
          — без разработки —
        </button>
      )}
      {open &&
        createPortal(
          <div
            className="dev-picker-pop"
            ref={popupRef}
            style={{ top: pos.top, left: pos.left }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <input
              ref={inputRef}
              className="dev-picker-input"
              placeholder="Поиск по номеру…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpen(false);
              }}
            />
            <ul className="dev-picker-list">
              <li>
                <button type="button" className="dev-picker-option" onClick={() => pick(null)}>
                  — без разработки —
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
              {filtered.length === 0 && <li className="dev-picker-empty">Ничего не найдено</li>}
            </ul>
          </div>,
          document.body
        )}
    </span>
  );
}
