"use client";

// Одиночный выбор тега из общего справочника: фильтр по подстроке (без учёта
// регистра), счётчик использования, клавиатурная навигация. Используется в
// bulk-баре (добавить/убрать тег). Полностью контролируемый: value/onChange —
// как у обычного input.

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTagDictionary } from "@/lib/tagDictionary";
import { useI18n } from "@/i18n/LocaleContext";
import { positionPopup } from "@/lib/popupPosition";

const MAX_OPTIONS = 20;

export default function TagCombobox({
  value = "",
  onChange,
  placeholder = "",
  allowNew = true,
  ariaLabel,
  className = "",
  disabled = false,
}) {
  const { t, tc } = useI18n();
  const dictionary = useTagDictionary();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef(null);
  const popupRef = useRef(null);
  // Уникальный id listbox на экземпляр (aria-controls/aria-activedescendant).
  const listId = useId();

  useLayoutEffect(() => {
    if (!open) return undefined;
    const trigger = boxRef.current;
    const popup = popupRef.current;
    if (trigger && popup) {
      const triggerWidth = trigger.getBoundingClientRect().width;
      const availableWidth = Math.max(0, window.innerWidth - 16);
      const popupWidth = Math.min(Math.max(triggerWidth, 260), Math.min(420, availableWidth));
      positionPopup(trigger, popup, { width: popupWidth });
    }
    const onScroll = (event) => {
      if (!popupRef.current?.contains(event.target)) setOpen(false);
    };
    const onResize = () => setOpen(false);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  const q = value.trim().toLowerCase();
  const filtered = (
    q
      ? dictionary.filter(
          (t) =>
            (t.name || "").toLowerCase().includes(q) ||
            (t.display || "").toLowerCase().includes(q)
        )
      : dictionary
  ).slice(0, MAX_OPTIONS);

  useEffect(() => {
    const onDocClick = (e) => {
      if (boxRef.current?.contains(e.target)) return;
      if (popupRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const commit = (name) => {
    onChange(name);
    setOpen(false);
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && active >= 0 && filtered[active]) {
        commit(filtered[active].name);
      } else if (allowNew && value.trim()) {
        commit(value.trim());
      } else {
        setOpen(false);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    } else {
      setOpen(true);
      setActive(-1);
    }
  };

  return (
    <div className="tag-combobox" ref={boxRef}>
      <input
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
          setActive(-1);
        }}
        onKeyDown={onKeyDown}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        placeholder={placeholder}
        autoComplete="off"
        disabled={disabled}
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? `${listId}-opt-${active}` : undefined}
        aria-label={ariaLabel}
        className={className}
      />
      {open && createPortal(
        <ul className="tag-combobox-list" role="listbox" id={listId} ref={popupRef}>
          {filtered.length === 0 && (
            <li className="tag-combobox-empty">
              {allowNew && value.trim()
                ? t("tags.picker.addQuote", { name: value.trim() })
                : t("tags.combobox.noMatch")}
            </li>
          )}
          {filtered.map((t, i) => (
            <li
              key={t.name}
              id={`${listId}-opt-${i}`}
              role="option"
              aria-selected={i === active}
              className={`tag-combobox-item ${i === active ? "active" : ""} ${t.count === 0 ? "unused" : ""}`}
              onMouseDown={(e) => {
                e.preventDefault();
                commit(t.name);
              }}
              onMouseEnter={() => setActive(i)}
            >
              <span className="tag-combobox-name">{t.display || t.name}</span>
              <span className="tag-combobox-count" title={tc("tags.picker.usedIn", t.count)}>
                {t.count}
              </span>
            </li>
          ))}
        </ul>,
        document.body
      )}
    </div>
  );
}
