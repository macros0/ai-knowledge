"use client";

import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { positionPopup } from "@/lib/popupPosition";
import { filterSearchableOptions } from "@/lib/searchableSelect.mjs";

const MAX_OPTIONS = 40;
const DEFAULT_POPUP_MIN_WIDTH = 260;
const DEFAULT_POPUP_MAX_WIDTH = 420;

export default function SearchableSelect({
  options = [],
  value = "",
  onChange,
  placeholder = "—",
  searchPlaceholder = "Search…",
  emptyLabel = "No matches",
  ariaLabel,
  title,
  allowCustomInput = false,
  customOptionLabel = (input) => `Use “${input}”`,
  popupMinWidth = DEFAULT_POPUP_MIN_WIDTH,
  popupMaxWidth = DEFAULT_POPUP_MAX_WIDTH,
  disabled = false,
  className = "",
  triggerClassName = "doc-filter-select",
  renderTrigger,
  renderOption,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const rootRef = useRef(null);
  const popupRef = useRef(null);
  const inputRef = useRef(null);
  const listId = useId();

  const current = options.find((option) => String(option.value) === String(value)) || null;
  const filtered = useMemo(
    () => filterSearchableOptions(options, query, MAX_OPTIONS),
    [options, query]
  );
  const customOption = allowCustomInput && query.trim() &&
    !options.some((option) => String(option.value).toLowerCase() === query.trim().toLowerCase())
    ? { value: query.trim(), label: customOptionLabel(query.trim()), custom: true }
    : null;
  const visibleOptions = customOption ? [customOption, ...filtered] : filtered;

  const close = (restoreFocus = false) => {
    setOpen(false);
    setQuery("");
    setActive(-1);
    if (restoreFocus) rootRef.current?.querySelector('button')?.focus();
  };

  const choose = (next) => {
    onChange?.(next);
    close(true);
  };

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return undefined;
    const trigger = rootRef.current;
    const popup = popupRef.current;
    if (trigger && popup) {
      const triggerWidth = trigger.getBoundingClientRect().width;
      const availableWidth = Math.max(0, window.innerWidth - 16);
      const preferredWidth = Math.min(Math.max(triggerWidth, popupMinWidth), popupMaxWidth);
      const popupWidth = Math.min(preferredWidth, availableWidth);
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
  }, [open, popupMaxWidth, popupMinWidth]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => {
      if (rootRef.current?.contains(event.target)) return;
      if (popupRef.current?.contains(event.target)) return;
      close();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const onKeyDown = (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setActive((index) => Math.min(index + 1, visibleOptions.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, -1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (open && active >= 0 && visibleOptions[active]) choose(visibleOptions[active].value);
      else if (open && visibleOptions.length === 1) choose(visibleOptions[0].value);
      else if (open && allowCustomInput && query.trim()) choose(query.trim());
    } else if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    }
  };

  const triggerContent = renderTrigger
    ? renderTrigger(current, { open })
    : current?.label ?? placeholder;

  return (
    <span className={`searchable-select ${className}`.trim()} ref={rootRef}>
      <button
        type="button"
        className={triggerClassName}
        onClick={() => {
          setQuery("");
          setActive(-1);
          setOpen((currentOpen) => !currentOpen);
        }}
        aria-label={ariaLabel}
        title={title}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={open ? listId : undefined}
        disabled={disabled}
      >
        {triggerContent}
      </button>
      {open &&
        createPortal(
          <div className="dev-picker-pop searchable-select-pop" ref={popupRef} data-dialog-popup
            onKeyDown={(event) => {
              if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
            }}>
            <input
              ref={inputRef}
              className="dev-picker-input"
              value={query}
              placeholder={searchPlaceholder}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(-1);
              }}
              onKeyDown={onKeyDown}
              autoComplete="off"
              aria-label={searchPlaceholder}
              aria-controls={listId}
            />
            <ul className="dev-picker-list" id={listId} role="listbox">
              {visibleOptions.map((option, index) => (
                <li key={String(option.value)}>
                  <button
                    type="button"
                    className={`dev-picker-option${index === active ? " active" : ""}`}
                    onMouseDown={(event) => {
                      event.preventDefault();
                      choose(option.value);
                    }}
                    onMouseEnter={() => setActive(index)}
                    role="option"
                    aria-selected={String(option.value) === String(value)}
                  >
                    {renderOption ? renderOption(option) : option.label}
                  </button>
                </li>
              ))}
              {filtered.length === 0 && <li className="dev-picker-empty">{emptyLabel}</li>}
            </ul>
          </div>,
          document.body
        )}
    </span>
  );
}
