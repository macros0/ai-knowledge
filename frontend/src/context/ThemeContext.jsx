"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { applyTheme, readStored, resolveTheme, setStored } from "@/lib/theme";

const ThemeContext = createContext(null);

function storedMode() {
  // На сервере (SSR) window нет — возвращаем дефолт; боевой режим
  // до гидратации выставляет bootScript, поэтому мигания нет.
  if (typeof window === "undefined") return "auto";
  return readStored(window) || "auto";
}

export function ThemeProvider({ children }) {
  const [mode, setMode] = useState(storedMode);
  const [resolvedTheme, setResolvedTheme] = useState(() =>
    resolveTheme(storedMode(), typeof window !== "undefined" ? window : undefined)
  );

  // Конкретная тема синхронизируется с атрибутом data-theme на <html>.
  // Это единственное место применения — гонок «initial vs stored» нет.
  useEffect(() => {
    applyTheme(resolvedTheme, window);
  }, [resolvedTheme]);

  // Не-auto: выбранная тема = фактическая.
  useEffect(() => {
    if (mode !== "auto") setResolvedTheme(mode);
  }, [mode]);

  // Auto: следим за сменой системной темы (live-переключение).
  useEffect(() => {
    if (mode !== "auto") return undefined;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const sync = () => setResolvedTheme(mq.matches ? "light" : "dark");
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, [mode]);

  const changeMode = useCallback((next) => {
    if (next !== "auto" && next !== "light" && next !== "dark") return;
    setMode(next);
    setStored(next, window);
  }, []);

  const value = useMemo(
    () => ({ mode, resolvedTheme, setMode: changeMode }),
    [mode, resolvedTheme, changeMode]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme должен использоваться внутри <ThemeProvider>");
  return ctx;
}
