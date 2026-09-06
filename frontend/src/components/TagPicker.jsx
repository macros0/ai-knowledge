"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { bumpTagVersion, useTagDictionary } from "@/lib/tagDictionary";
import { positionPopup } from "@/lib/popupPosition";
import { useI18n } from "@/i18n/LocaleContext";

const MAX_OPTIONS = 20;

export default function TagPicker({
  label,
  placeholder = "",
  selected,
  onChange,
  refreshKey = 0,
  className = "",
}) {
  const { t, tc } = useI18n();
  const dictionary = useTagDictionary();
  const [input, setInput] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef(null); // обёртка combobox (для позиционирования и клика)
  const popupRef = useRef(null); // попап в портале
  // Уникальный id listbox на экземпляр: связка combobox ↔ listbox через
  // aria-controls/aria-activedescendant (в отличие от datalist, id не коллизирует).
  const listId = useId();

  // Совместимость со старым контрактом: при смене refreshKey (например, после
  // загрузки документа) форсируем перечитывание общего справочника.
  useEffect(() => {
    if (refreshKey > 0) bumpTagVersion();
  }, [refreshKey]);

  // Попап — в портале (document.body, position:fixed), иначе его обрезает любой
  // scroll-контейнер предка (.panel/.document-list с overflow). Позиция — с
  // разворотом вверх при нехватке места снизу (positionPopup); на скролл страницы
  // закрываем, на resize закрываем (позиция могла устареть).
  useLayoutEffect(() => {
    if (!open) return;
    const trigger = boxRef.current;
    const popup = popupRef.current;
    if (trigger && popup) {
      positionPopup(trigger, popup, { width: trigger.getBoundingClientRect().width });
    }

    const onScroll = (e) => {
      if (popupRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    const onResize = () => setOpen(false);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [open]);

  useEffect(() => {
    const onDocClick = (e) => {
      if (boxRef.current && boxRef.current.contains(e.target)) return;
      if (popupRef.current && popupRef.current.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const q = input.trim().toLowerCase();
  let filtered = dictionary;
  if (q) {
    // Быстрый поиск: сначала префикс-совпадения, затем вхождение в середине.
    // Ищем и по каноническому имени, и по локализованному display.
    const starts = [];
    const contains = [];
    for (const t of dictionary) {
      const n = (t.name || "").toLowerCase();
      const d = (t.display || "").toLowerCase();
      if (n.startsWith(q) || d.startsWith(q)) starts.push(t);
      else if (n.includes(q) || d.includes(q)) contains.push(t);
    }
    filtered = [...starts, ...contains];
  }
  filtered = filtered.slice(0, MAX_OPTIONS);

  // display-имя по каноническому имени (для чипов уже выбранных тегов).
  const displayOf = (name) =>
    dictionary.find((t) => t.name === name)?.display ?? name;

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
            {displayOf(t)}
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
        {open &&
          createPortal(
            <ul
              className="tag-combobox-list"
              role="listbox"
              id={listId}
              ref={popupRef}
            >
              {filtered.length === 0 && (
                <li className="tag-combobox-empty">
                  {input.trim() ? t("tags.picker.addQuote", { name: input.trim() }) : t("tags.picker.noTags")}
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
    </div>
  );
}
