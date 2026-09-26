"use client";

import { useEffect, useRef, useState } from "react";

// При замене одного окна другим прежний контрол уже может быть удалён.
const returnTargets = new WeakMap();

/** Простое модальное окно: оверлей + карточка, закрытие по Esc/клику на оверлей. */
export default function Modal({ title, onClose, children, footer }) {
  const cardRef = useRef(null);
  const closeRef = useRef(onClose);
  const [previousFocus] = useState(() => {
    const element = typeof document === "undefined" ? null : document.activeElement;
    return { element, dialog: element?.closest?.('[role="dialog"]') };
  });
  useEffect(() => { closeRef.current = onClose; }, [onClose]);

  useEffect(() => {
    const card = cardRef.current;
    if (!card) return;
    // Снимок сделан до commit: дочерний autoFocus не должен заменить инициатор.
    const previous = previousFocus.element?.isConnected
      ? previousFocus.element : returnTargets.get(previousFocus.dialog);
    returnTargets.set(card, previous);
    const isTopmost = () => {
      const dialogs = document.querySelectorAll('[role="dialog"][aria-modal="true"]');
      return dialogs[dialogs.length - 1] === card;
    };
    const focusable = () => Array.from(card.querySelectorAll(
      'a[href],button,input,select,textarea,[tabindex],[contenteditable="true"],summary'
    )).filter((el) => el.tabIndex >= 0 && !el.matches(":disabled") &&
      !el.closest("[inert]") && el.getClientRects().length > 0);
    const focusFirst = () => (focusable()[0] || card).focus();
    if (isTopmost() && !card.contains(document.activeElement)) focusFirst();

    const onKey = (e) => {
      if (!isTopmost()) return;
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        closeRef.current?.();
      } else if (e.key === "Tab") {
        const elements = focusable();
        const first = elements[0];
        const last = elements[elements.length - 1];
        if (!first) {
          e.preventDefault();
          card.focus();
        } else if (!card.contains(document.activeElement) || document.activeElement === card ||
          (e.shiftKey ? document.activeElement === first : document.activeElement === last)) {
          e.preventDefault();
          (e.shiftKey ? last : first).focus();
        }
      }
    };
    const onFocus = (e) => {
      if (isTopmost() && !card.contains(e.target)) focusFirst();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocus);
      if (previous?.isConnected && typeof previous.focus === "function") previous.focus();
    };
  }, [previousFocus]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        ref={cardRef}
        className="modal-card"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        {title && <h2 className="modal-title">{title}</h2>}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}
