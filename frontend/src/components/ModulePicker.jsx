"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { positionPopup } from "@/lib/popupPosition";
import { useI18n } from "@/i18n/LocaleContext";
import {
  readRecentModules,
  recordRecentModule,
  pruneRecentModules,
} from "@/lib/recentModules.mjs";

const MAX_MATCHES = 20;

/**
 * Фильтр модуля в чате (Этап 5.1, замена чипов всех модулей).
 *
 * Показывает короткий закреплённый ряд «последних использованных» модулей
 * (персонально, из localStorage) + компактное inline-поле автодополнения по
 * закрытому справочнику модулей. Масштабируется на любое число модулей без
 * деградации UI. Выбор (чипом или через поиск) двигает модуль в начало ряда и
 * вызывает onChange; сброс devFilter делается вызывающим (ChatPanel) в onChange —
 * оба пути выбора идут через один колбэк.
 */
export default function ModulePicker({ modules, value, username, onChange }) {
  const { t } = useI18n();
  const [recents, setRecents] = useState([]);
  const [input, setInput] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const boxRef = useRef(null);
  const popupRef = useRef(null);
  const listId = useId();

  // Чтение localStorage только на клиенте (SSR-safe); отсекаем модули, которых
  // уже нет в справочнике, и персистим отсечённое. Пока справочник не загружен
  // (modules пуст) — НЕ трогаем localStorage: prune с пустым valid-множеством
  // стёр бы сохранённые «последние» при ремоунте чата (переключение вкладок).
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!modules || modules.length === 0) return;
    const storage = window.localStorage;
    const loaded = readRecentModules(storage, username);
    const valid = new Set(modules);
    const filtered = loaded.filter((m) => valid.has(m));
    setRecents(filtered);
    if (filtered.length !== loaded.length) {
      pruneRecentModules(storage, username, modules);
    }
  }, [username, modules]);

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
      if (boxRef.current?.contains(e.target)) return;
      if (popupRef.current?.contains(e.target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  if (!modules || modules.length === 0) return null;

  const q = input.trim().toLowerCase();
  let matches = modules;
  if (q) {
    const starts = [];
    const contains = [];
    for (const m of modules) {
      const n = m.toLowerCase();
      if (n.startsWith(q)) starts.push(m);
      else if (n.includes(q)) contains.push(m);
    }
    matches = [...starts, ...contains];
  }
  matches = matches.slice(0, MAX_MATCHES);

  const choose = (m) => {
    setRecents(recordRecentModule(window.localStorage, username, m));
    onChange(m);
    setInput("");
    setOpen(false);
    setActive(-1);
  };

  const toggleChip = (m) => {
    if (value === m) {
      onChange("");
      return;
    }
    setRecents(recordRecentModule(window.localStorage, username, m));
    onChange(m);
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
      setActive((a) => Math.min(a + 1, matches.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (open && active >= 0 && matches[active]) {
        choose(matches[active]);
      } else if (q) {
        const exact = modules.find((m) => m.toLowerCase() === q);
        if (exact) choose(exact);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
    } else {
      setOpen(true);
      setActive(-1);
    }
  };

  return (
    <div className="module-picker">
      <div className="chat-module-chips" role="group" aria-label={t("chat.moduleQuickFilterAria")}>
        {recents.map((m) => (
          <button
            key={m}
            type="button"
            className={`module-chip${value === m ? " active" : ""}`}
            aria-pressed={value === m}
            onClick={() => toggleChip(m)}
          >
            {m}
          </button>
        ))}
      </div>
      <div className="module-combobox" ref={boxRef}>
        <input
          className="module-search-input"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setOpen(true);
            setActive(-1);
          }}
          onKeyDown={onKeyDown}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            if (!input.trim()) setOpen(false);
          }}
          placeholder={t("chat.moduleSearchPlaceholder")}
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-controls={listId}
          aria-activedescendant={open && active >= 0 ? `${listId}-opt-${active}` : undefined}
        />
        {open &&
          createPortal(
            <ul className="tag-combobox-list" role="listbox" id={listId} ref={popupRef}>
              {matches.length === 0 && (
                <li className="tag-combobox-empty">{t("chat.moduleNotFound")}</li>
              )}
              {matches.map((m, i) => (
                <li
                  key={m}
                  id={`${listId}-opt-${i}`}
                  role="option"
                  aria-selected={i === active}
                  className={`tag-combobox-item ${i === active ? "active" : ""}`}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    choose(m);
                  }}
                  onMouseEnter={() => setActive(i)}
                >
                  <span className="tag-combobox-name">{m}</span>
                </li>
              ))}
            </ul>,
            document.body
          )}
      </div>
    </div>
  );
}
