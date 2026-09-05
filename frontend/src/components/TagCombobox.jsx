"use client";

// Одиночный выбор тега из общего справочника: фильтр по подстроке (без учёта
// регистра), счётчик использования, клавиатурная навигация. Используется в
// bulk-баре (добавить/убрать тег). Полностью контролируемый: value/onChange —
// как у обычного input.

import { useEffect, useId, useRef, useState } from "react";
import { useTagDictionary } from "@/lib/tagDictionary";
import { useI18n } from "@/i18n/LocaleContext";

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
  // Уникальный id listbox на экземпляр (aria-controls/aria-activedescendant).
  const listId = useId();

  const q = value.trim().toLowerCase();
  const filtered = (
    q ? dictionary.filter((t) => t.name.toLowerCase().includes(q)) : dictionary
  ).slice(0, MAX_OPTIONS);

  useEffect(() => {
    const onDocClick = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
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
      {open && (
        <ul className="tag-combobox-list" role="listbox" id={listId}>
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
              <span className="tag-combobox-name">{t.name}</span>
              <span className="tag-combobox-count" title={tc("tags.picker.usedIn", t.count)}>
                {t.count}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}