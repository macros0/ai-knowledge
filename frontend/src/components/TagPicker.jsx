"use client";

import { useEffect, useId, useRef, useState } from "react";
import { bumpTagVersion, useTagDictionary } from "@/lib/tagDictionary";

const MAX_OPTIONS = 20;

export default function TagPicker({
  label,
  placeholder = "",
  selected,
  onChange,
  refreshKey = 0,
  className = "",
}) {
  const dictionary = useTagDictionary();
  const [input, setInput] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef(null);
  // Уникальный id listbox на экземпляр: связка combobox ↔ listbox через
  // aria-controls/aria-activedescendant (в отличие от datalist, id не коллизирует).
  const listId = useId();

  // Совместимость со старым контрактом: при смене refreshKey (например, после
  // загрузки документа) форсируем перечитывание общего справочника.
  useEffect(() => {
    if (refreshKey > 0) bumpTagVersion();
  }, [refreshKey]);

  useEffect(() => {
    const onDocClick = (e) => {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const q = input.trim().toLowerCase();
  const filtered = (
    q ? dictionary.filter((t) => t.name.toLowerCase().includes(q)) : dictionary
  ).slice(0, MAX_OPTIONS);

  const addTag = (value) => {
    const v = value.trim();
    if (v && !selected.includes(v)) onChange([...selected, v]);
    setInput("");
    setOpen(false);
  };

  const removeTag = (tag) => onChange(selected.filter((t) => t !== tag));

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
      if (open && active >= 0 && filtered[active]) addTag(filtered[active].name);
      else addTag(input);
    } else if (e.key === "Escape") {
      setOpen(false);
    } else {
      setOpen(true);
      setActive(-1);
    }
  };

  return (
    <div className={`tag-picker ${className}`.trim()}>
      <span className="tag-picker-label">{label}</span>
      <div className="tag-chips">
        {selected.map((t) => (
          <span key={t} className="tag-chip" onClick={() => removeTag(t)}>
            {t}
          </span>
        ))}
      </div>
      <div className="tag-combobox" ref={boxRef}>
        <input
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            if (input.trim()) addTag(input);
            else setOpen(false);
          }}
          placeholder={placeholder}
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-controls={listId}
          aria-activedescendant={open && active >= 0 ? `${listId}-opt-${active}` : undefined}
        />
        {open && (
          <ul className="tag-combobox-list" role="listbox" id={listId}>
            {filtered.length === 0 && (
              <li className="tag-combobox-empty">
                {input.trim() ? `Добавить «${input.trim()}»` : "Нет тегов в справочнике"}
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
                  addTag(t.name);
                }}
                onMouseEnter={() => setActive(i)}
              >
                <span className="tag-combobox-name">{t.name}</span>
                <span className="tag-combobox-count" title={`Используется в ${t.count} документ(ах)`}>
                  {t.count}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}