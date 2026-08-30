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

  const logout = useCallback(() => {
    // RP-Initiated Logout — браузерная навигация (приложение → Keycloak logout →
    // post_logout_redirect_uri). fetch не последует за цепочкой редиректов.
    window.location.assign("/api/auth/logout");
  }, []);

  const role = user?.roles?.[0] ?? null;

  const hasRole = useCallback(
    (...roles) => {
      if (!user) return false;
      const userRoles = user.roles ?? [];
      return roles.some((r) => userRoles.includes(r));
    },
    [user]
  );

  const value = useMemo(
    () => ({ user, mode, simUsers, loading, login, logout, refresh, role, hasRole }),
    [user, mode, simUsers, loading, login, logout, refresh, role, hasRole]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}