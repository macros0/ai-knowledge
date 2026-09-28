"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import ErrorReference from "./ErrorReference";

const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  const dismiss = useCallback((id) => {
    setToasts((t) => t.filter((x) => x.id !== id));
  }, []);

  const showToast = useCallback(
    (payload, options = {}) => {
      const structured = payload && typeof payload === "object" && typeof payload.message === "string";
      const message = structured ? payload.message : payload;
      const { type = "error", duration = 5000, action = null, requestId = null, localReportId = null } =
        structured ? payload.options || {} : options;
      const id = Date.now() + Math.random();
      setToasts((t) => [...t, { id, message, type, action, requestId, localReportId }]);
      if (duration > 0) {
        setTimeout(() => dismiss(id), duration);
      }
    },
    [dismiss]
  );

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      {mounted &&
        createPortal(
          <div className="toast-container">
            {toasts.map((t) => (
              <div key={t.id} className={`toast toast-${t.type}`}>
                <span className="toast-msg" onClick={() => dismiss(t.id)}>
                  {t.message}
                </span>
                <ErrorReference requestId={t.requestId} localReportId={t.localReportId} />
                {t.action && (
                  <button
                    type="button"
                    className="toast-action"
                    onClick={() => {
                      dismiss(t.id);
                      t.action.onClick?.();
                    }}
                  >
                    {t.action.label}
                  </button>
                )}
              </div>
            ))}
          </div>,
          document.body
        )}
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    return { showToast: () => {} };
  }
  return ctx;
}
