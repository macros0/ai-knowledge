"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import { useAuth } from "./AuthContext";

const DocumentFiltersContext = createContext(null);

// Провайдер в root layout сохраняет выбор между разделами и при открытии
// корзины. Состояние живёт в текущем приложении и принадлежит пользователю.
export function DocumentFiltersProvider({ children }) {
  const { user, mode } = useAuth();
  const owner = `${mode}:${user?.user_id ?? user?.username ?? "anonymous"}`;
  return <DocumentFiltersState key={owner}>{children}</DocumentFiltersState>;
}

function DocumentFiltersState({ children }) {
  const [savedView, setSavedView] = useState({ query: "", urlQuery: null, filtersOpen: false });
  const rememberFilters = useCallback((query, urlQuery, filtersOpen) => {
    setSavedView((previous) => previous.query === query && previous.urlQuery === urlQuery && previous.filtersOpen === filtersOpen
      ? previous
      : { query, urlQuery, filtersOpen });
  }, []);
  const value = useMemo(() => ({ savedView, rememberFilters }), [savedView, rememberFilters]);
  return <DocumentFiltersContext.Provider value={value}>{children}</DocumentFiltersContext.Provider>;
}

export function useDocumentFilters() {
  const context = useContext(DocumentFiltersContext);
  if (!context) throw new Error("useDocumentFilters must be used within DocumentFiltersProvider");
  return context;
}
