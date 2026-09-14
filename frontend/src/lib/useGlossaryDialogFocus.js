"use client";

import { useEffect, useRef } from "react";

/** Keep keyboard focus in a glossary confirmation and restore its opener. */
export function useGlossaryDialogFocus(open, onClose, busy, returnFocusTo) {
  const dialogRef = useRef(null);
  const state = useRef({ onClose, busy });
  useEffect(() => { state.current = { onClose, busy }; }, [onClose, busy]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    const opener = returnFocusTo || document.activeElement;
    const ownedPopups = () => [...dialog.querySelectorAll('[aria-expanded="true"][aria-controls]')]
      .map((element) => document.getElementById(element.getAttribute('aria-controls'))?.closest('[data-dialog-popup]'))
      .filter(Boolean);
    const focusable = () => [dialog, ...ownedPopups()].flatMap((root) => [...root.querySelectorAll(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]',
    )]).filter((element) => element.getClientRects().length > 0);
    (focusable()[0] || dialog).focus();
    const onKey = (event) => {
      if (event.key === "Escape") {
        // A portaled language picker owns the first Escape. React's popup
        // handler closes it and restores its trigger within this dialog.
        if (ownedPopups().some((popup) => popup.contains(event.target))) return;
        event.preventDefault();
        event.stopPropagation();
        if (!state.current.busy) state.current.onClose?.();
      }
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0] || dialog;
      const last = elements.at(-1) || dialog;
      const inside = dialog.contains(document.activeElement) || ownedPopups().some((popup) => popup.contains(document.activeElement));
      if (!elements.length || !inside ||
          (event.shiftKey && document.activeElement === first) ||
          (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      if (opener?.isConnected) opener.focus();
    };
  }, [open, returnFocusTo]);
  return dialogRef;
}
