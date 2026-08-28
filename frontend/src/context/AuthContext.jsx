"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  getMe,
  logout as logoutRequest,
  simulateAuth,
} from "@/lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [mode, setMode] = useState("disabled");
  const [simUsers, setSimUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  const apply = useCallback((data) => {
    setUser(data?.user ?? null);
    setMode(data?.mode ?? "disabled");
    setSimUsers(data?.sim_users ?? []);
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await getMe());
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(
    async (username) => {
      setLoading(true);
      try {
        apply(await simulateAuth(username));
      } finally {
        setLoading(false);
      }
    },
    [apply]
  );

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // session всё равно чистим клиентски
    }
    try {
      apply(await getMe());
    } catch {
      setUser(null);
      setSimUsers([]);
    }
  }, [apply]);

  const value = useMemo(
    () => ({ user, mode, simUsers, loading, login, logout, refresh }),
    [user, mode, simUsers, loading, login, logout, refresh]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}