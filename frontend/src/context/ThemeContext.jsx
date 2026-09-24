"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { applyTheme, readStored, resolveTheme, setStored } from "@/lib/theme";

const ThemeContext = createContext(null);

export function ThemeProvider({ children }) {
  // SSR и первый клиентский рендер должны показывать одинаковый переключатель.
  // null означает «настройки ещё не прочитаны»: эффекты не должны затереть
  // тему, которую bootScript уже применил до первой отрисовки страницы.
  const [mode, setMode] = useState(null);
  const [resolvedTheme, setResolvedTheme] = useState(null);

  useEffect(() => {
    const initialMode = readStored(window) || "auto";
    setMode(initialMode);
    setResolvedTheme(resolveTheme(initialMode, window));
  }, []);

  // Конкретная тема синхронизируется с атрибутом data-theme на <html>.
  // Это единственное место применения — гонок «initial vs stored» нет.
  useEffect(() => {
    if (resolvedTheme !== null) applyTheme(resolvedTheme, window);
  }, [resolvedTheme]);

  // Не-auto: выбранная тема = фактическая.
  useEffect(() => {
    if (mode !== null && mode !== "auto") setResolvedTheme(mode);
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
    () => ({ mode: mode ?? "auto", resolvedTheme: resolvedTheme ?? "dark", setMode: changeMode }),
    [mode, resolvedTheme, changeMode]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme должен использоваться внутри <ThemeProvider>");
  return ctx;
}
